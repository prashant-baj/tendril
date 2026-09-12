# guardrails/

Bedrock Guardrail **policies as data**. Each `*.json` file is one guardrail's policy
(content filters, denied topics, PII handling, blocked messaging) — expressed declaratively,
owned by safety/domain reviewers, and free of any code or account-specific values.

These files are the *what*. The *how* (provisioning) lives in `infra/stacks/guardrails_stack.py`,
which loads each JSON and creates an `AWS::Bedrock::Guardrail` + a published version via CDK.
This mirrors how prompts are handled (`guardrails/` ↔ `PromptsStack`, ADR-0006): safety config
is separated from code so it can be reviewed, versioned, and deployed on its own.

## Why a separate folder + stack

- **Decoupled deploys.** `cdk deploy tendril-<env>-guardrails` ships a policy change without
  redeploying agents or foundation. Agents reference the guardrail by **stable name** only
  (`GUARDRAIL_NAME`), resolving id + version at runtime — no CloudFormation cross-stack import.
- **Reviewable safety.** A guardrail change is a small, readable JSON diff a domain/safety
  expert can approve, independent of application code.
- **Governed like prompts.** Guardrails are safety-critical config; they are versioned and
  governed the same way as prompts (ADR-0006/0007), and the deterministic floor they enforce
  is never overridable by a model (ADR-0008).

## Files

| File | Guardrail | Applies to |
|------|-----------|-----------|
| `vision_guardrail.json` | `tendril-<env>-vision-guardrail` | vision specialist (plant-photo identification) |

## Policy shape (`vision_guardrail.json`)

- **Content filters** — HATE / INSULTS / SEXUAL / VIOLENCE / MISCONDUCT at HIGH/HIGH, plus
  **PROMPT_ATTACK** on input (HIGH) to catch injection in user text, photos, and tool output.
- **Denied topics** — `NonGardeningAdvice` (keep to the domain) and `UnsafeChemicalUse`
  (block unsafe/illegal/off-label chemical guidance; safe IPM guidance stays allowed).
- **PII** — EMAIL / PHONE / NAME / ADDRESS **anonymized** (privacy-by-design, ADR-0002).
- **Blocked messaging** — in-character refusals for blocked input and output.

## Editing a guardrail

1. Edit the JSON (add a filter, tune a topic, change a PII action).
2. `cdk deploy tendril-<env>-guardrails` — this publishes a **new immutable version**.
3. The agent resolves the guardrail by name and uses its current published version at cold start.

See **ADR-0008** (layered, defense-in-depth) for where this fits: Bedrock Guardrails are the
*managed* layer — the prompt layer is never the safety boundary, and deterministic code + IAM +
HITL sit beneath.
