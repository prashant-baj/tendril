# Tendril — Backlog

Single tracking sheet for every story across epics. Rows are ordered by **Rank** (1 = do next).
To reprioritize, move a row up/down and renumber the Rank column. Status reflects the repo audit
on the date below — re-verify before starting a story.

**Last updated:** 2026-08-20

**Status legend:** ✅ Done · ◐ Partial · ☐ To do
**Epics:** **TF** = Technical Foundation (`stories/technical-foundation.md`) · **PG** = Prompt & Guardrail MVP (`stories/prompt-guardrail-mvp.md`)

| Rank | Epic | ID | Story | Status | Depends on | Notes / gaps |
|-----:|------|----|-------|:------:|------------|--------------|
| 1 | PG | PG-06 | Decoupled deploy & CI for prompts and guardrails | ◐ | PG-03, TF-07 | **CI wired:** `ci.yml` now has `prompts/**`+`guardrails/**` change filters, deploy condition includes them, and deploy line runs `foundation → prompts → guardrails → agentcore` (cold-start order, commented). **Remaining:** verify each stack deploys standalone on next push; optional runbook note. |
| 2 | PG | PG-02 | Prompt-layer safety: scope & refusal guidance | ◐ | PG-01 | Prompt exists; add explicit scope + first-line refusal framing mirroring guardrail denied topics; publish new version. |
| 3 | PG | PG-05 | Deterministic floor: schema validation & fail-safe | ◐ | PG-01, PG-04 | Entrypoint returns a typed dict; add input-payload validation + unit tests (valid / missing prompt / malformed). |
| 4 | PG | PG-07 | Verify: eval scenarios for prompt & guardrail | ☐ | PG-02, PG-04, PG-05 | Eval doc/script: prompt fallback, on-domain answer, off-domain refusal, unsafe-vs-safe chemical (over-block check), injection, PII masking; trace in CloudWatch/OTel. |
| 5 | TF | TF-07 | CI/CD & selective deploy (GitOps) | ◐ | TF-03 | PR lint/test/gitleaks + OIDC + path deploy to dev all working. **Gaps:** no prod deploy path (tag/approval gate); branch protection is account-side (unverifiable from repo). Prompts/guardrails deploy wiring now done (PG-06). |
| 6 | TF | TF-06 | Local development environment | ◐ | TF-01 | Pinned Python/Node, `tasks.ps1`, per-component requirements, setup guides present. **Gap:** `frontend/` is a README only — Angular app not scaffolded (later epic). |
| 7 | TF | TF-05 | Build with AI setup (Strands MCP + rules) | ✅ | TF-01 | `.mcp.json` (`uvx strands-agents-mcp-server`) + repo `llms.txt` fallback added; `CLAUDE.md` updated. One-time local MCP smoke-test pending `uv`. |
| 8 | TF | TF-01 | Repository & monorepo structure | ✅ | — | `.pre-commit-config.yaml` added (ruff system hooks + gitleaks + hygiene). Node/Angular workspace files land with the frontend epic. |
| 9 | TF | TF-08 | Secrets & configuration baseline | ✅ | TF-01, TF-03 | gitleaks now in **pre-commit + CI**; `.env.example` placeholders, `config.get_secret` helper, CDK context; push protection account-side. |
| 10 | TF | TF-02 | AWS account & CLI setup (dev/prod) | ✅ | — | `verify-aws.*` + `developer-setup.md` present; SSO/model-access/Builder ID are account-side but proven by successful deploys + Bedrock responses. |
| 11 | TF | TF-03 | CDK bootstrap & IaC baseline | ✅ | TF-02 | `env_name`-parameterized, env-prefixed stacks, no hardcoded account IDs (grep clean), synth/deploy succeed. |
| 12 | TF | TF-04 | AgentCore CLI & runtime prerequisites | ✅ | TF-02, TF-03 | Strands + bedrock-agentcore, ARM64 Dockerfile, exec role, OTel; hello agent deployed and returned a greeting. |
| 13 | PG | PG-01 | Externalize the hello agent's system prompt | ✅ | TF-04 | `PromptsStack` + `CfnPrompt`/version; runtime resolves by `PROMPT_NAME` with built-in fallback; scoped IAM. |
| 14 | PG | PG-03 | Provision the Bedrock Guardrail as IaC (policy-as-data) | ✅ | TF-03 | `guardrails/hello_guardrail.json` + `GuardrailsStack` → `CfnGuardrail`/version; content filters + denied topics + PII; synth clean. |
| 15 | PG | PG-04 | Attach & resolve the guardrail at runtime | ✅ | PG-03 | Runtime resolves by `GUARDRAIL_NAME`, attaches to `BedrockModel`, fail-open MVP; scoped guardrail IAM. |

## Carried forward (future epics — not yet storied)

Walking skeleton (photo → orchestrator → response), agent factory + prompts, orchestrator & specialists, tools/APIs, tracker/outcome loop, frontend app, notifications, HITL action-gating, aggregated-data/learning layer. Add as feature stories when scoped, then insert into the table with a Rank.

## How to use this sheet

- **Reprioritize:** move a row and renumber **Rank** (1 = next). Keep the table sorted by Rank.
- **Status change:** update the Status cell (✅/◐/☐) and trim the Notes gap when closed.
- **New story:** add a row, give it a Rank, link its epic file. Cross-cutting gaps map to existing stories (e.g. the missing pre-commit file → TF-01 + TF-08), so track them there rather than duplicating.
- **Definition of Done** for every story lives in `engineering-best-practices.md` (CI green, no hardcoded secrets/accounts, deploys to dev, only-changed-folders, docs/ADRs updated, human-reviewed).
