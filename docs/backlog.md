# Tendril — Backlog

Single tracking sheet for every story across epics. Rows are ordered by **Rank** (1 = do next).
To reprioritize, move a row up/down and renumber the Rank column. Status reflects the repo audit
on the date below — re-verify before starting a story.

**Last updated:** 2026-09-12 (added the Garden Onboarding epic OB-01/OB-02 and resequenced the whole table into the UI-first incremental plan in `docs/roadmap.md`; deprioritized WhatsApp, prioritized UI-based HITL)

**Status legend:** ✅ Done · ◐ Partial · ☐ To do
**Epics:** **TF** = Technical Foundation (`stories/technical-foundation.md`) · **PG** = Prompt & Guardrail MVP (`stories/prompt-guardrail-mvp.md`) · **OB** = Garden Onboarding (`stories/garden-onboarding.md`) · **WS** = Walking Skeleton (`stories/walking-skeleton.md`) · **AF** = Agent Factory (`stories/agent-factory.md`)

**Active development plan:** [`docs/roadmap.md`](./roadmap.md) sequences OB → WS → AF as a
UI-first, incremental feature track (2026-09-12) — ranks 1-14 below reflect that plan. The
pre-existing `TF`/`PG` housekeeping (ranks 15+) is still real, open work; it's just no longer the
immediate next thing.

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
| 1 | OB | OB-01 | Setup My Garden | ☐ | — | New screen + `POST /gardens`/`GET /gardens/{id}` + first `ClientApiStack` + transactional Garden write. Smallest possible complete UI→API→Lambda→data slice; zero AI/orchestrator involvement. Do this first (`docs/roadmap.md` Phase 1). |
| 2 | OB | OB-02 | Add a Plant (with a photo) | ☐ | OB-01 | `POST /gardens/{id}/media` (presigned upload, reused by every later photo flow) + `POST /gardens/{id}/plants`; builds the camera/file-picker component WS-05 later reuses. `docs/roadmap.md` Phase 2. |
| 3 | WS | WS-01 | API: contract-first OpenAPI for photo upload + goal intake | ☐ | OB-02 | Media op now lives in OB-02 — this story only adds the goal-intake operation by the time it's picked up (see the epic's 2026-09-12 resequencing note). `docs/roadmap.md` Phase 3. |
| 4 | WS | WS-02 | Infra: Client API, EventBridge trigger, declarative agent registry | ☐ | WS-01 | Adds to the `ClientApiStack` OB-01 already stood up (not a new stack). EventBridge rule, `agents/registry/*.json` + `AgentCoreStack` refactor migrating `hello_agent`, per ADR-0011/ADR-0012. |
| 5 | WS | WS-03 | Application: Client API Lambda handlers | ☐ | WS-01, WS-02 | Goal-intake handler (validate → persist → publish event). |
| 6 | WS | WS-04 | Application: Orchestrator Lambda | ☐ | WS-02, WS-03 | Strands agent loop, EventBridge-triggered, calls the migrated `hello` agent via `InvokeAgentRuntime` as the first registered specialist. |
| 7 | WS | WS-05 | Frontend: real photo capture + goal submission | ☐ | WS-01, WS-03 | Reuses OB-02's camera/file-picker component; wires the Capture screen's goal text to the live API, replacing `MockGoalApi` for this flow. `docs/roadmap.md` Phase 4. |
| 8 | AF | AF-01 | Infra: generalize Prompts/Guardrails/AgentCore to N specialists | ☐ | WS-02 | Loops all three registry-driven stacks over their data folders; deploys a second real agent (Agronomy) with zero stack-code changes. `docs/roadmap.md` Phase 7. |
| 9 | AF | AF-02 | Template agent: one config-driven codebase for every specialist | ☐ | AF-01 | Generalizes `hello_agent`'s code into the shared template every registry entry uses; proves two agents, one codebase. |
| 10 | AF | AF-03 | Tools: Lambda-backed Tool APIs + agent-side binding | ☐ | AF-02 | Weather as the reference Lambda+API tool; tool-agnostic binding mechanism in the template agent. |
| 11 | AF | AF-04 | Guardrails: author the Agronomy specialist's real guardrail policy | ☐ | AF-01 | Real agronomy-specific denied topics/PII policy, replacing AF-01's placeholder stub. |
| 12 | AF | AF-05 | Memory & Context: per-user/per-garden memory for every specialist | ☐ | AF-02 | Strands Memory (Bedrock KB) + context management, scoped per tenant, opt-in per agent — fulfills ADR-0001 action items 6 & 8. |
| 13 | — | — | *(not yet storied)* Plan approval via UI (HITL) | ☐ | WS-04 | `docs/roadmap.md` Phase 5 — wires the existing "Review plan" button to `POST /plans/{id}/approve` + orchestrator interrupt/resume. Write the story once Phase 4 ships. |
| 14 | — | — | *(not yet storied)* Tasks & Activity on real data | ☐ | WS-04 | `docs/roadmap.md` Phase 6 — replaces `MockTaskApi`/`MockActivityApi`. Write the story once Phase 5 ships. |
| 15 | PG | PG-07 | Verify: eval scenarios for prompt & guardrail | ◐ | PG-02, PG-04, PG-05 | Eval script built; live run **4/6 pass**. Open: (a) commit + redeploy the retuned `UnsafeChemicalUse` topic, then re-verify the safe-IPM case no longer over-blocks; (b) **PII returned `action=NONE`** — run `eval_guardrail.py --debug` and diagnose (region/feature vs input-vs-output masking). |
| 16 | TF | TF-05 | Build with AI setup (Strands MCP + rules) | ◐ | TF-01 | `llms.txt` present, `CLAUDE.md` references it. **Gap:** `.mcp.json` (`uvx strands-agents-mcp-server`) is **missing on disk** — MCP docs server not actually wired. Re-add it. |
| 17 | TF | TF-01 | Repository & monorepo structure | ◐ | — | Structure/config/LICENSE/README present. **Gap:** `.pre-commit-config.yaml` is **missing on disk** — local format/lint/gitleaks hooks not installed. Re-add it. |
| 18 | TF | TF-08 | Secrets & configuration baseline | ◐ | TF-01, TF-03 | gitleaks in **CI** ✅; `.env.example`, `config.get_secret`, CDK context ✅. **Gap:** pre-commit gitleaks missing (same missing file as TF-01). |
| 19 | PG | PG-02 | Prompt-layer safety: scope & refusal guidance | ◐ | PG-01 | Prompt exists; add explicit scope + first-line refusal framing mirroring guardrail denied topics; publish new version. |
| 20 | TF | TF-07 | CI/CD & selective deploy (GitOps) | ◐ | TF-03 | PR lint/test/gitleaks + OIDC + path deploy to dev all working; prompts/guardrails wiring done (PG-06). **Gaps:** no prod deploy path (tag/approval gate); branch protection account-side. |
| 21 | TF | TF-06 | Local development environment | ◐ | TF-01 | Pinned Python/Node, `tasks.ps1`, per-component requirements, setup guides present. `frontend/` is now a scaffolded Angular 19 PWA (design implemented, ADR-0010 deploys it to S3) — no formal feature stories written yet for the individual screens (see `frontend/README.md`). |
| 22 | PG | PG-06 | Decoupled deploy & CI for prompts and guardrails | ✅ | PG-03, TF-07 | `ci.yml` has `prompts/**`+`guardrails/**` filters and deploys `foundation → prompts → guardrails → agentcore` (cold-start order). Committed + pushed; prompts & guardrails deployed standalone. |
| 23 | PG | PG-05 | Deterministic floor: schema validation & fail-safe | ✅ | PG-01, PG-04 | `resolve_user_message` payload validation + lazy init in `agent.py`; 13 unit tests + 7 policy-limit tests + 3 CDK tests, all green. Committed. |
| 24 | TF | TF-02 | AWS account & CLI setup (dev/prod) | ✅ | — | `verify-aws.*` + `developer-setup.md`; account-side items proven by successful deploys + Bedrock responses. |
| 25 | TF | TF-03 | CDK bootstrap & IaC baseline | ✅ | TF-02 | `env_name`-parameterized, env-prefixed stacks, no hardcoded account IDs, synth/deploy succeed. |
| 26 | TF | TF-04 | AgentCore CLI & runtime prerequisites | ✅ | TF-02, TF-03 | Strands + bedrock-agentcore, ARM64 Dockerfile, exec role, OTel; hello agent deployed and returned a greeting. |
| 27 | PG | PG-01 | Externalize the hello agent's system prompt | ✅ | TF-04 | `PromptsStack` reads `prompts/hello-system.md`; runtime resolves by `PROMPT_NAME` with fallback; scoped IAM. Deployed. |
| 28 | PG | PG-03 | Provision the Bedrock Guardrail as IaC (policy-as-data) | ✅ | TF-03 | `guardrails/hello_guardrail.json` + `GuardrailsStack` → `CfnGuardrail`/version; deployed (retune pending, see PG-07). |
| 29 | PG | PG-04 | Attach & resolve the guardrail at runtime | ✅ | PG-03 | Runtime resolves by `GUARDRAIL_NAME`, attaches to `BedrockModel`, fail-open MVP; scoped guardrail IAM. |

## Carried forward (future epics — not yet storied)

Plan approval via UI and Tasks/Activity-on-real-data are now placeholders at ranks 13-14 (above)
rather than buried here — see `docs/roadmap.md` Phases 5-6. Still genuinely future, not yet
storied: full garden/plant editing beyond OB-01/02's create-only scope, a multi-garden switcher,
the remaining 7 specialist agents and 4 tools beyond Agronomy/Weather, the tracker/outcome loop,
the WebSocket push channel + live result rendering, auth (Cognito), Cedar authorization + wider
Interventions/HITL handlers, aggregated-data/learning layer. Add as feature stories when scoped,
then insert into the table with a Rank.

**Explicitly deprioritized:** the **WhatsApp (and email) notification/reply channel** — in scope
per `project-context.md`'s vision, but pushed behind UI-based HITL and notifications (ADR-0001
refinement, 2026-09-12). Don't pick this up before the UI channel is built and working.

## How to use this sheet

- **Reprioritize:** move a row and renumber **Rank** (1 = next). Keep the table sorted by Rank.
- **Status change:** update the Status cell (✅/◐/☐) and trim the Notes gap when closed.
- **New story:** add a row, give it a Rank, link its epic file. Cross-cutting gaps map to existing stories (e.g. the missing pre-commit file → TF-01 + TF-08), so track them there rather than duplicating.
- **Definition of Done** for every story lives in `engineering-best-practices.md` (CI green, no hardcoded secrets/accounts, deploys to dev, only-changed-folders, docs/ADRs updated, human-reviewed).
