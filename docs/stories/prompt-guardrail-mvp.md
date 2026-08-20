# Stories — Epic: Prompt & Guardrail MVP (Hello agent)

Implements the **MVP scope of [ADR-0006](../architecture/ADRs/0006-prompt-externalization-bedrock-prompt-management.md) (externalized prompts)** and **[ADR-0008](../architecture/ADRs/0008-guardrails-layered-defense-in-depth.md) (layered guardrails)** on the **hello agent** — the walking-skeleton runtime from TF-04. The goal is a thin but *complete vertical slice* of the two safety-relevant foundations — "prompts are the application" and "layered defense-in-depth" — proven end-to-end on one agent before the factory and specialists multiply them.

Each story is **build-ready for AI-assisted development**: explicit acceptance criteria, conforming to the [ADRs](../architecture/ADRs/) and the [engineering best-practices checklist](../engineering-best-practices.md). A story is complete only when it meets the **Definition of Done** (CI green, no hardcoded secrets/accounts, deploys to dev, only-changed-folders, docs/ADRs updated, human-reviewed).

**Story format:** `As a <role>, I want <capability>, so that <benefit>` + Acceptance Criteria + Tasks + Dependencies + Status.

**Scope boundary (hello agent):** the hello agent takes **no real-world actions**, so HITL action-gating and tool allow/deny lists are explicitly **out of scope here** and are carried into the orchestrator/specialist epics (they remain ADR-0008 MVP items for *those* agents). Contextual grounding, automated-reasoning checks, `ApplyGuardrail` tag-based selective evaluation, and per-region topic tuning are **roadmap** per both ADRs.

**Suggested order:** PG-01 → PG-02 → PG-03 → PG-04 → PG-05 → PG-06 → PG-07.

**Status legend:** ✅ done · ◐ partially done · ☐ to do (reflects the repo at the time of writing; verify before starting).

---

## PG-01 — Externalize the hello agent's system prompt

**As a** domain expert / developer, **I want** the hello agent's system prompt provisioned in Bedrock Prompt Management and resolved at runtime by a stable name, **so that** prompt text can change without touching or redeploying agent code (ADR-0006).

**Acceptance Criteria**
- [ ] The prompt is provisioned as `CfnPrompt` + `CfnPromptVersion` in a **dedicated `PromptsStack`**, env-prefixed (`tendril-<env>-hello-system`).
- [ ] The runtime receives only a stable **`PROMPT_NAME`** env var — **no** prompt id/ARN hardcoded and **no** CloudFormation cross-stack import.
- [ ] At cold start the agent resolves the id (`ListPrompts`) and fetches the template (`GetPrompt`), reading the default variant's text.
- [ ] A **built-in default** prompt is used if resolution/fetch fails, so prompt loading never takes the agent down (fail-open).
- [ ] Execution role grants `bedrock:ListPrompts` (no resource) + `bedrock:GetPrompt` scoped to `arn:aws:bedrock:<region>:<account>:prompt/*`.

**Tasks**
- [ ] `PromptsStack` with the hello prompt + published version.
- [ ] Agent `load_system_prompt()` with resolve → fetch → fallback.
- [ ] Pass `PROMPT_NAME` env + scope IAM in the AgentCore stack.

**Dependencies:** TF-04. **Status:** ✅ done (verify AC on next deploy).

---

## PG-02 — Prompt-layer safety: scope & refusal guidance

**As a** safety-conscious developer, **I want** the externalized prompt to carry Tendril's scope and first-line refusal guidance, **so that** the *soft* guardrail layer keeps the agent on-domain and sets tone — while never being relied on as the safety boundary (ADR-0008).

**Acceptance Criteria**
- [ ] The hello system prompt states the gardening domain, invites brief gardening answers, and gives **first-line refusal guidance** for off-domain / unsafe requests (consistent with the guardrail's denied topics).
- [ ] The prompt explicitly **does not** encode any must-not rule as its only defense (those live in the managed/deterministic layers) — documented as a comment/ADR reference.
- [ ] Prompt change ships via `PromptsStack` alone (a new immutable version), no agent redeploy.
- [ ] Wording is expert-editable in the Prompt Management console without a code change.

**Tasks**
- [ ] Revise `HELLO_SYSTEM_PROMPT` with scope + refusal framing.
- [ ] Publish a new prompt version; confirm the runtime picks it up at cold start.
- [ ] Note in ADR-0008 that prompt-layer guidance mirrors (never replaces) the guardrail topics.

**Dependencies:** PG-01. **Status:** ◐ partially done (prompt exists; add explicit scope/refusal guidance).

---

## PG-03 — Provision the Bedrock Guardrail as IaC (policy-as-data)

**As a** safety/domain reviewer, **I want** the hello agent's Bedrock Guardrail defined as reviewable data and provisioned via CDK in its own stack, **so that** safety policy is versioned, auditable, and deployable independently of code (ADR-0008).

**Acceptance Criteria**
- [ ] Guardrail policy lives as **data** in `guardrails/hello_guardrail.json` (the *what*); a dedicated **`GuardrailsStack`** loads it into `CfnGuardrail` + `CfnGuardrailVersion` (the *how*), env-prefixed (`tendril-<env>-hello-guardrail`).
- [ ] **Content filters** configured: HATE/INSULTS/SEXUAL/VIOLENCE/MISCONDUCT (HIGH/HIGH) + **PROMPT_ATTACK** on input (HIGH).
- [ ] **Denied topics** configured: `NonGardeningAdvice` and `UnsafeChemicalUse`, each with a definition + examples; safe label-compliant IPM guidance is **not** blocked.
- [ ] **PII** anonymized: EMAIL / PHONE / NAME / ADDRESS.
- [ ] In-character **blocked-input** and **blocked-output** messages set.
- [ ] Nothing account-specific or secret in the JSON; `cdk synth` clean.

**Tasks**
- [ ] Author `guardrails/hello_guardrail.json` + `guardrails/README.md`.
- [ ] `GuardrailsStack` loads JSON → `CfnGuardrail` (content/topic/PII policies) + version.
- [ ] Wire `GuardrailsStack` into `app.py` as `tendril-<env>-guardrails`.

**Dependencies:** TF-03. **Status:** ✅ done (verify by deploying the stack).

---

## PG-04 — Attach & resolve the guardrail at runtime (managed layer)

**As a** developer, **I want** the agent to resolve the guardrail by stable name and attach it to the model, **so that** every Converse call filters input and output through the managed safety layer (ADR-0008).

**Acceptance Criteria**
- [ ] The runtime receives only a stable **`GUARDRAIL_NAME`** env var — no id/ARN hardcoded, no cross-stack import.
- [ ] At cold start the agent resolves the id (`ListGuardrails`) and attaches `guardrail_id` + version to the `BedrockModel`.
- [ ] Resolution is **fail-open for the MVP** (agent still runs if the guardrail can't be resolved) with a logged warning; a code comment/ADR note records that **production should fail-closed**.
- [ ] Execution role grants `bedrock:ListGuardrails` (no resource) + `bedrock:GetGuardrail`/`bedrock:ApplyGuardrail` scoped to `arn:aws:bedrock:<region>:<account>:guardrail/*`.
- [ ] Guardrail changes deploy via `GuardrailsStack` alone; the runtime picks up the current version at cold start.

**Tasks**
- [ ] Agent `build_model()` with resolve → attach → fail-open fallback.
- [ ] Pass `GUARDRAIL_NAME` env + scope guardrail IAM in the AgentCore stack.
- [ ] Confirm attachment in logs on a live invoke.

**Dependencies:** PG-03. **Status:** ✅ done (verify on a live invoke).

---

## PG-05 — Deterministic floor: schema validation & fail-safe posture

**As a** developer, **I want** hardcoded input/output validation in the agent entrypoint, **so that** structural correctness is enforced deterministically beneath the managed and prompt layers (ADR-0008 hardcoded layer).

**Acceptance Criteria**
- [ ] The entrypoint **validates the input payload** shape and rejects malformed requests with a safe error (never an unhandled exception to the caller).
- [ ] The response is returned in a **validated, typed shape** (e.g., `{"result": str}`), so downstream callers get a stable contract.
- [ ] Failures in prompt or guardrail resolution are handled per their story's posture (fail-open MVP) and **logged**, not silently swallowed.
- [ ] The deterministic checks do **not** depend on the prompt or the model to be correct.
- [ ] Unit tests cover: valid payload, missing/empty `prompt`, and malformed payload.

**Tasks**
- [ ] Add payload validation + output-schema shaping in `invoke()`.
- [ ] Add unit tests for the validation paths.
- [ ] Document the "deterministic floor" intent inline referencing ADR-0008.

**Dependencies:** PG-01, PG-04. **Status:** ◐ partially done (entrypoint returns a typed dict; add explicit validation + tests).

---

## PG-06 — Decoupled deploy & CI for prompts and guardrails

**As a** developer, **I want** the prompts and guardrails stacks to deploy independently in the pipeline via path-based change detection, **so that** a prompt or policy change ships without redeploying agents or foundation (ADR-0006/0008 deployment topology).

**Acceptance Criteria**
- [ ] `cdk deploy tendril-<env>-prompts` and `cdk deploy tendril-<env>-guardrails` each succeed **standalone** (no cross-stack export blocks them).
- [ ] Documented **deploy order** for a cold environment: guardrails + prompts **before** the agentcore runtime (so name resolution succeeds at first cold start).
- [ ] CI **path filters** map `prompts/`/`PromptsStack` and `guardrails/`/`GuardrailsStack` changes to their own deploy jobs; unrelated changes don't redeploy them.
- [ ] The CI deploy step includes `tendril-<env>-prompts` and `tendril-<env>-guardrails`.
- [ ] No account IDs/secrets introduced; OIDC-authenticated deploy (per TF-07).

**Tasks**
- [ ] Add `guardrails/` (and confirm `prompts` path handling) to CI change detection + deploy jobs.
- [ ] Update the CI deploy line to include both stacks.
- [ ] Document deploy order in the dev-environment runbook.

**Dependencies:** PG-03, TF-07. **Status:** ☐ to do (stacks synth clean; CI/runbook wiring pending).

---

## PG-07 — Verify: eval scenarios for prompt & guardrail behavior

**As a** developer, **I want** repeatable scenarios that prove the prompt and guardrail behave as intended, **so that** safety is tested as behavior (not just unit-tested config), per the best-practices "model-driven behavior via eval scenarios + tracing" rule.

**Acceptance Criteria**
- [ ] **Prompt fallback:** with `PROMPT_NAME` unset/misconfigured, the agent still responds using the built-in default (logged as fallback).
- [ ] **On-domain:** a normal gardening question returns a helpful brief answer.
- [ ] **Denied topic — off-domain:** a non-gardening request (e.g., stock tips) is refused with the blocked-input message.
- [ ] **Denied topic — unsafe chemical:** an unsafe/off-label pesticide request is refused; a **safe IPM** question is **not** refused (guards against over-blocking).
- [ ] **Content filter / injection:** a prompt-injection attempt in the user message is caught (blocked or neutralized).
- [ ] **PII:** an input containing an email/phone is **anonymized** in what the model sees / the response, per policy.
- [ ] Results are captured (a short eval doc or script + expected outcomes) and traceable in CloudWatch/OTel.

**Tasks**
- [ ] Write an eval script / scenario doc with the cases above and expected outcomes.
- [ ] Run against dev; record actual vs expected; note any tuning (false pos/neg).
- [ ] Link results from ADR-0008 action items.

**Dependencies:** PG-02, PG-04, PG-05. **Status:** ☐ to do.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint + tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev** via the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**, and is **human-reviewed**. For safety-relevant stories, "tests" includes the **eval scenarios** (PG-07), not only unit tests.

### Carried forward (not in this epic)

HITL action-gating and tool allow/deny lists (orchestrator/specialists, when agents take real actions); contextual grounding + automated-reasoning checks for hard agronomic rules; `ApplyGuardrail` tag-based selective evaluation; per-region denied-topic tuning; guardrail/prompt versioning governance tied to ADR-0007.
