# prompts/

Externalized prompts — the "application" content that specializes agents (ADR-0006).
Prompts are **files** here (the *what*); `infra/stacks/prompts_stack.py` reads them and
provisions each as a Bedrock Prompt (the *how*) — the same policy-as-data split used for
guardrails (`guardrails/` ↔ `GuardrailsStack`).

## File convention

- One file per prompt, **named for the prompt's logical name**: `prompts/<logical-name>.md`.
- The logical name is **env-agnostic** (e.g. `hello-system`). The stack adds the environment
  prefix, so `prompts/hello-system.md` becomes the Bedrock prompt `tendril-<env>-hello-system`.
- The **entire file content is the prompt template text** — no front-matter, no wrapping. Write
  the system prompt directly (markdown is fine; it's passed through as text).
- Naming follows `<specialty>-<role>` per ADR-0006 (e.g. `agronomy-system`, `pest-diagnosis`),
  so each specialty (agronomy, pest, disease, irrigation, fertilizer, pruning, weather,
  beautification, landscaping) can own its own file and be configured by its domain expert.

## Registering a prompt

A file alone isn't provisioned — add it to `PROMPT_CATALOG` in `prompts_stack.py`
(logical name → construct-id prefix + description). This keeps `README.md` and other
non-prompt files from being published, and keeps CloudFormation construct ids stable.

## Editing / publishing

1. Edit `prompts/<logical-name>.md`.
2. `cdk deploy tendril-<env>-prompts` — publishes a **new immutable version**.
3. The agent resolves the prompt by name at cold start and uses its current text
   (with a built-in default fallback if the fetch fails).

## Files

| File | Logical name | Bedrock prompt | Used by |
|------|--------------|----------------|---------|
| `hello-system.md` | `hello-system` | `tendril-<env>-hello-system` | hello agent |
