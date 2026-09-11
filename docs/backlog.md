# Tendril — Backlog

Single tracking sheet for every story across epics. Rows are ordered by **Rank** (1 = do next).
To reprioritize, move a row up/down and renumber the Rank column. Status reflects the repo audit
on the date below — re-verify before starting a story.

**Last updated:** 2026-09-10 (re-synced against `origin/dev` + working tree)

**Status legend:** ✅ Done · ◐ Partial · ☐ To do
**Epics:** **TF** = Technical Foundation (`stories/technical-foundation.md`) · **PG** = Prompt & Guardrail MVP (`stories/prompt-guardrail-mvp.md`)

## Known drift / housekeeping (clear these first)

- **Uncommitted work in the tree** (branch `dev`, else up to date with `origin/dev`): the guardrail
  over-block retune (`guardrails/hello_guardrail.json`) and the improved eval
  (`scripts/eval_guardrail.py`) are modified but **not committed or pushed**, so CI hasn't
  redeployed them — the **deployed guardrail still has the over-blocking `UnsafeChemicalUse`
  definition**.
- **`.mcp.json` and `.pre-commit-config.yaml` are missing on disk** — they were never added (the
  device bridge can't write those protected filenames). This regresses TF-05 / TF-01 / TF-08.
- **Stale `.git/index.lock`** is present and blocks `git add`/`commit`. Remove it first
  (`del ".git\index.lock"`); the bridge can't delete it.

| Rank | Epic | ID | Story | Status | Depends on | Notes / gaps |
|-----:|------|----|-------|:------:|------------|--------------|
| 1 | PG | PG-07 | Verify: eval scenarios for prompt & guardrail | ◐ | PG-02, PG-04, PG-05 | Eval script built; live run **4/6 pass**. Open: (a) commit + redeploy the retuned `UnsafeChemicalUse` topic, then re-verify the safe-IPM case no longer over-blocks; (b) **PII returned `action=NONE`** — run `eval_guardrail.py --debug` and diagnose (region/feature vs input-vs-output masking). |
| 2 | TF | TF-05 | Build with AI setup (Strands MCP + rules) | ◐ | TF-01 | `llms.txt` present, `CLAUDE.md` references it. **Gap:** `.mcp.json` (`uvx strands-agents-mcp-server`) is **missing on disk** — MCP docs server not actually wired. Re-add it. |
| 3 | TF | TF-01 | Repository & monorepo structure | ◐ | — | Structure/config/LICENSE/README present. **Gap:** `.pre-commit-config.yaml` is **missing on disk** — local format/lint/gitleaks hooks not installed. Re-add it. |
| 4 | TF | TF-08 | Secrets & configuration baseline | ◐ | TF-01, TF-03 | gitleaks in **CI** ✅; `.env.example`, `config.get_secret`, CDK context ✅. **Gap:** pre-commit gitleaks missing (same missing file as TF-01). |
| 5 | PG | PG-02 | Prompt-layer safety: scope & refusal guidance | ◐ | PG-01 | Prompt exists; add explicit scope + first-line refusal framing mirroring guardrail denied topics; publish new version. |
| 6 | TF | TF-07 | CI/CD & selective deploy (GitOps) | ◐ | TF-03 | PR lint/test/gitleaks + OIDC + path deploy to dev all working; prompts/guardrails wiring done (PG-06). **Gaps:** no prod deploy path (tag/approval gate); branch protection account-side. |
| 7 | TF | TF-06 | Local development environment | ◐ | TF-01 | Pinned Python/Node, `tasks.ps1`, per-component requirements, setup guides present. **Gap:** `frontend/` is a README only — Angular app not scaffolded (later epic). |
| 8 | PG | PG-06 | Decoupled deploy & CI for prompts and guardrails | ✅ | PG-03, TF-07 | `ci.yml` has `prompts/**`+`guardrails/**` filters and deploys `foundation → prompts → guardrails → agentcore` (cold-start order). Committed + pushed; prompts & guardrails deployed standalone. |
| 9 | PG | PG-05 | Deterministic floor: schema validation & fail-safe | ✅ | PG-01, PG-04 | `resolve_user_message` payload validation + lazy init in `agent.py`; 13 unit tests + 7 policy-limit tests + 3 CDK tests, all green. Committed. |
| 10 | TF | TF-02 | AWS account & CLI setup (dev/prod) | ✅ | — | `verify-aws.*` + `developer-setup.md`; account-side items proven by successful deploys + Bedrock responses. |
| 11 | TF | TF-03 | CDK bootstrap & IaC baseline | ✅ | TF-02 | `env_name`-parameterized, env-prefixed stacks, no hardcoded account IDs, synth/deploy succeed. |
| 12 | TF | TF-04 | AgentCore CLI & runtime prerequisites | ✅ | TF-02, TF-03 | Strands + bedrock-agentcore, ARM64 Dockerfile, exec role, OTel; hello agent deployed and returned a greeting. |
| 13 | PG | PG-01 | Externalize the hello agent's system prompt | ✅ | TF-04 | `PromptsStack` reads `prompts/hello-system.md`; runtime resolves by `PROMPT_NAME` with fallback; scoped IAM. Deployed. |
| 14 | PG | PG-03 | Provision the Bedrock Guardrail as IaC (policy-as-data) | ✅ | TF-03 | `guardrails/hello_guardrail.json` + `GuardrailsStack` → `CfnGuardrail`/version; deployed (retune pending, see PG-07). |
| 15 | PG | PG-04 | Attach & resolve the guardrail at runtime | ✅ | PG-03 | Runtime resolves by `GUARDRAIL_NAME`, attaches to `BedrockModel`, fail-open MVP; scoped guardrail IAM. |

## Carried forward (future epics — not yet storied)

Walking skeleton (photo → orchestrator → response), agent factory + prompts, orchestrator & specialists, tools/APIs, tracker/outcome loop, frontend app, notifications, HITL action-gating, aggregated-data/learning layer. Add as feature stories when scoped, then insert into the table with a Rank.

## How to use this sheet

- **Reprioritize:** move a row and renumber **Rank** (1 = next). Keep the table sorted by Rank.
- **Status change:** update the Status cell (✅/◐/☐) and trim the Notes gap when closed.
- **New story:** add a row, give it a Rank, link its epic file. Cross-cutting gaps map to existing stories (e.g. the missing pre-commit file → TF-01 + TF-08), so track them there rather than duplicating.
- **Definition of Done** for every story lives in `engineering-best-practices.md` (CI green, no hardcoded secrets/accounts, deploys to dev, only-changed-folders, docs/ADRs updated, human-reviewed).
