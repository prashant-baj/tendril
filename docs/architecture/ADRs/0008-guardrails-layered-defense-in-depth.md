# ADR-0008: Guardrails — layered, defense-in-depth

**Status:** Accepted
**Date:** 2026-08-20
**Deciders:** Project owner / lead engineer

## Context

Tendril gives **real-world advice that affects living things and people** — plants, food
crops eaten by families, pets, and the environment — and it ingests **user content** (free-text,
photos, location) and **tool output** that could carry prompt injections. The harm and trust
surface is real: unsafe pesticide/chemical guidance, off-domain or harmful responses, leaked PII,
hallucinated agronomy, or an irreversible action taken without consent.

No single mechanism covers all of this. Prompt instructions are soft (bypassable); managed
content filters are probabilistic; deterministic code is reliable but narrow. Guardrails must
therefore be **layered and embedded across architectural layers** — defense in depth — with each
layer doing what it's best at.

## Decision

Adopt **layered guardrails** with a guiding principle:

- **Must-not (hard safety)** → deterministic or managed **hard-stops** (Bedrock Guardrails block, hardcoded deny-lists, IAM, HITL). Never rely on the prompt for these.
- **Should-not (quality)** → **probabilistic/managed** checks (contextual grounding, steering, denied topics).
- **Tone / scope** → **soft** prompt-layer guidance.

### The layers

| Layer | Mechanism | Catches | Nature |
|-------|-----------|---------|--------|
| **Platform / IAM** | Least-privilege roles, tool scoping, network, secrets | Blast radius, data exfiltration, unauthorized actions | Deterministic |
| **Bedrock Guardrails** | Content filters (incl. prompt-attack, image), denied topics, word filters, **PII block/mask**, contextual grounding, automated reasoning — applied via model config and/or `ApplyGuardrail` | Harmful content, off-domain topics, PII leakage, injection, hallucination | Managed / probabilistic |
| **Strands interventions & steering** (ADR-0001) | Typed actions `deny/guide/confirm/transform`; `HumanInTheLoop` for actions | On-domain enforcement, risky-action gating, output correction, retry-loop control | Deterministic policy + model-assisted |
| **Agent / tool code (hardcoded)** | Input validation, allow/deny lists, output **schema validation**, action gating, confidence thresholds | Structural correctness, must-not rules, unsafe tool calls | Deterministic |
| **Prompt layer** | System-prompt scope, refusal guidance, safety framing (externalized, ADR-0006) | Tone, scope drift, first-line refusals | Soft |
| **Grounding / knowledge** | RAG over vetted sources + contextual grounding checks | Hallucinated/ungrounded advice | Probabilistic |

The core rule: **the prompt layer is never the safety boundary.** Anything that could cause real
harm must be enforced by a deterministic or managed layer beneath it.

### Tendril-specific requirements → layers

| Risk | Primary layers |
|------|----------------|
| Unsafe chemical/pesticide advice; region-illegal inputs | Denied topics + word filters + hardcoded deny + grounding + **human-expert escalation** |
| Off-domain / harmful content (self-harm, etc.) | Content filters + denied topics + prompt/steering + wellbeing escalation |
| Prompt injection (photos, text, tool output) | Content filter **prompt-attack** + input validation + **tag-based selective evaluation** (don't evaluate system/tool/RAG content as user input) |
| PII / privacy (location, personal info) | Sensitive-information filters (**mask**) + privacy-by-design (ADR-0002) |
| Hallucinated agronomy | Contextual grounding + RAG + (later) automated-reasoning checks for hard rules (e.g., dosage bounds) |
| Irreversible / costly actions | **HITL confirm** (ADR-0001) + hardcoded action gating |
| Low-confidence diagnosis | Confidence threshold → escalate / ask for more data, never guess |

## Implementation

- **Bedrock Guardrail as a CDK resource** (`aws_bedrock.CfnGuardrail` + `CfnGuardrailVersion`), env-parameterized and provisioned as IaC (ADR-0005). Like prompts (ADR-0006), it lives in a policy/prompts stack and agents reference it by **id + version** (or stable name) — decoupled, independently deployable.
- **Applied two ways:** attach the guardrail to the model on the Converse call (filters input + output automatically), and/or call **`ApplyGuardrail`** explicitly for finer control and **tag-based selective evaluation** so we scan the *user* content but not system prompts, tool results, or RAG passages.
- **Strands interventions/steering** implement behavioral guardrails inside the agent loop (deny/guide/confirm), and **HITL** gates risky actions.
- **Hardcoded checks** in agent/tool code for must-not rules, schema validation, and action gating — the deterministic floor.
- **Prompt layer** carries scope + refusal guidance (externalized).
- **Grounding**: specialists cite vetted sources; contextual grounding flags ungrounded advice.

Guardrails are **safety-critical configuration** and are therefore **versioned and governed like prompts** (ADR-0006/0007): guardrail changes classified `safety` force-apply to in-flight plans; the deterministic floor is never overridable by a model.

## Options Considered

| Option | Verdict |
|--------|---------|
| **A. Layered defense-in-depth** | **Chosen** — each layer covers the others' gaps |
| B. Bedrock Guardrails only | Rejected — misses action gating, schema, IAM, injection-via-tool-output |
| C. Prompt-only ("just tell it to be safe") | Rejected — soft, trivially bypassed; unsafe for real advice |
| D. Hardcoded-only | Rejected — brittle, doesn't scale to open-ended language risks |

## Trade-off Analysis

Every single-layer option has a fatal gap: prompts are bypassable, managed filters miss
structural/action risks and can over/under-block, and hardcoded rules can't anticipate
open-ended language harms. Defense-in-depth costs more to build and adds some latency/cost
(an `ApplyGuardrail` pass, grounding checks), but it's the only approach that holds for advice
with real-world consequences. We manage the cost by scoping which layers apply where (e.g.,
grounding + automated-reasoning only on high-stakes outputs) and using tag-based selective
evaluation to avoid scanning non-user content.

## Consequences

- **Easier:** consistent, enforceable safety independent of prompt wording; PII handled; injection and off-domain drift caught; auditable guardrail decisions.
- **Harder:** more components to configure and tune (false-positive/negative balance); added latency/cost on guarded paths; guardrail tuning becomes an ongoing, expert-informed activity.
- **To revisit:** grounding/automated-reasoning rollout on high-stakes outputs; region-specific denied topics (legal inputs vary); guardrail versioning/governance workflow.

## Deployment topology (refinement, 2026-08-20)

The Bedrock Guardrail is provisioned in a **dedicated `GuardrailsStack`**, separate from the
runtime (`AgentCoreStack`), with the policy expressed as **data** in `guardrails/*.json`
(the *what*) and the stack as the *how* — exactly mirroring prompts (`guardrails/` ↔
`GuardrailsStack`, as `prompts` ↔ `PromptsStack` in ADR-0006). Agents reference a guardrail
only by its **stable name** (`GUARDRAIL_NAME`, e.g. `tendril-dev-hello-guardrail`) and resolve
the id at runtime via `ListGuardrails` — there is **no CloudFormation cross-stack import**, so
guardrail changes deploy on their own (`cdk deploy tendril-<env>-guardrails`) without
redeploying agents or foundation, and we avoid the CFN exported-value-in-use trap.

The runtime attaches the resolved guardrail to the `BedrockModel` so input and output are
filtered on every Converse call. Resolution is **fail-open for the MVP** (the agent still runs
if the guardrail can't be resolved) because the deterministic floor beneath it — IAM, hardcoded
checks, HITL — does not depend on it; **production should fail-closed**. The execution role is
granted `bedrock:ListGuardrails` (no resource) plus `bedrock:GetGuardrail` / `bedrock:ApplyGuardrail`
scoped to this account/region's guardrails.

## Scope: MVP vs. roadmap

- **MVP:** one Bedrock Guardrail (content filters incl. prompt-attack, an off-domain/harmful **denied-topics** set, **PII mask**) attached to the model; prompt-layer scope guidance; HITL gating for any action; hardcoded output-schema checks. Provisioned via CDK, referenced by id/version.
- **Roadmap:** contextual grounding + automated-reasoning checks for hard agronomic rules; expert-tuned denied topics per region; full steering handlers; versioned guardrail governance tied to ADR-0007.

## Action Items

1. [x] Provision a Bedrock Guardrail via CDK (`CfnGuardrail` + version) in a dedicated `GuardrailsStack` (policy-as-data in `guardrails/*.json`); reference by stable name from agents.
2. [~] Apply it on the model Converse call (attached to `BedrockModel`). Tag-based selective evaluation via explicit `ApplyGuardrail` is roadmap.
3. [x] Configure MVP policies: content filters (+ prompt-attack), denied off-domain/harmful topics (`NonGardeningAdvice`, `UnsafeChemicalUse`), PII anonymize (EMAIL/PHONE/NAME/ADDRESS).
4. [ ] Add hardcoded action-gating + output-schema validation; wire HITL for irreversible/costly actions.
5. [ ] Add scope/refusal guidance to the externalized prompts.
6. [ ] Roadmap: contextual grounding + automated-reasoning for high-stakes outputs; region-specific tuning; guardrail versioning/governance.
