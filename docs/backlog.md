# Tendril — Backlog

Single tracking sheet for every story across epics. Rows are ordered by **Rank** (1 = do next).
To reprioritize, move a row up/down and renumber the Rank column. Status reflects the repo audit
on the date below — re-verify before starting a story.

**Last updated:** 2026-09-13 (OB-01/OB-02/WS-01..05 all done and smoke-tested end-to-end in dev —
the full photo/issue → orchestrator → specialist pipe is proven live, including finding
and fixing a real IAM gap along the way (WS-04); added OB-03, a deprioritized follow-up story to
show real plant photos instead of a generic icon; fixed a real CI gap —
`pytest -q || echo "no tests yet"` had been silently masking every test suite failing to even
collect, since `boto3`/`strands-agents`/etc. were never installed in the `quality` job; built a
real second specialist — **vision**, a plant-photo-identification agent, later switched from
`google.gemma-3-27b-it` to `qwen.qwen3-vl-235b-a22b` for answer quality — substantially completing
AF-01's registry/prompts/guardrails generalization ahead of schedule, using a real need instead
of the originally-planned Agronomy; decommissioned `hello` as a live registry entry now that
`vision` is real — `agents/hello_agent/` remains only as the shared template code every
specialist's `template` field points at, no longer separately deployed or callable by the
orchestrator; found + fixed two real production guardrail false-positives on `vision`'s output
and input (Bedrock's `NonGardeningAdvice` topic misclassifying legitimate plant diagnoses —
disabled for `vision` pending a real retune later); fixed a real Bedrock request-size limit crash
(a 2.79MB photo 400'd the model) by lowering the image cap to 5MB (doesn't fully close the gap —
the real limit is somewhere below that); **skipped AF-02** (pure rename, no new capability) and
built **AF-03** (Tool APIs) directly on AF-01: `app/tools/weather/` (Lambda + IAM-authenticated
Function URL, real Open-Meteo forecast), a tool-agnostic HTTP-tool binding in the shared template
(`agents/hello_agent/agent.py`'s `build_tools`/`make_tool`/`call_tool_endpoint`, SigV4-signed),
and — closing AF-01's deferred AC — **per-specialist IAM execution roles** (was one shared role)
so tool-invoke grants are scoped to only the tools each registry entry declares; `vision.json` now
declares `tools: ["weather"]` as the proof)

**Status legend:** ✅ Done · ◐ Partial · ☐ To do
**Epics:** **TF** = Technical Foundation (`stories/technical-foundation.md`) · **PG** = Prompt & Guardrail MVP (`stories/prompt-guardrail-mvp.md`) · **OB** = Garden Onboarding (`stories/garden-onboarding.md`) · **WS** = Walking Skeleton (`stories/walking-skeleton.md`) · **AF** = Agent Factory (`stories/agent-factory.md`)

**Active development plan:** [`docs/roadmap.md`](./roadmap.md) sequences OB → WS → AF as a
UI-first, incremental feature track (2026-09-12) — ranks 1-14 below reflect that plan. The
pre-existing `TF`/`PG` housekeeping (ranks 15+) is still real, open work; it's just no longer the
immediate next thing.

## Known drift / housekeeping (clear these first)

- **`guardrails/hello_guardrail.json` is deleted (2026-09-12)** — `hello` was decommissioned as a
  live registry entry; its over-block retune history no longer applies. `scripts/eval_guardrail.py`
  now defaults to `vision-guardrail`. Re-verify the deployed `hello-guardrail` Bedrock resource is
  actually torn down after the next `cdk deploy tendril-<env>-guardrails`.
- **`.mcp.json` and `.pre-commit-config.yaml` are missing on disk** — they were never added (the
  device bridge can't write those protected filenames). This regresses TF-05 / TF-01 / TF-08.
- **Stale `.git/index.lock`** is present and blocks `git add`/`commit`. Remove it first
  (`del ".git\index.lock"`); the bridge can't delete it.

| Rank | Epic | ID | Story | Status | Depends on | Notes / gaps |
|-----:|------|----|-------|:------:|------------|--------------|
| 1 | OB | OB-01 | Setup My Garden | ✅ | — | New screen + `POST /gardens`/`GET /gardens/{id}` + first `ClientApiStack` + transactional Garden write. Implemented, unit/CDK/component-tested, verified end-to-end against real API Gateway/Lambda/DynamoDB in dev. Not yet re-deployed after the latest fixes (CORS, OpenAPI 3.0.x, container packaging) — those are already committed. |
| 2 | OB | OB-02 | Add a Plant (with a photo) | ✅ | OB-01 | `POST /gardens/{id}/media` (presigned upload) + `POST /gardens/{id}/plants`; camera/file-picker component built (plain `<input capture>`, not `getUserMedia` — HTTP-only deployment, ADR-0010). Verified end-to-end in dev: real photo landed in S3, Plant+Media records linked correctly. |
| 3 | WS | WS-01 | API: contract-first OpenAPI for photo upload + goal intake | ✅ | OB-02 | Added `createGoal` + `Goal` schemas. Caught + fixed a real gap: `gardenId` wasn't declared per-operation on the shared CORS block — moved to path-level params (correct OpenAPI idiom). Spec-lint (`openapi-spec-validator`) wired into `ci.yml`. |
| 4 | WS | WS-02 | Infra: Client API, EventBridge trigger, declarative agent registry | ✅ | WS-01 | `agents/registry/hello.json` + `AgentCoreStack` registry loop (replacing the hardcoded call); orchestrator Lambda + EventBridge rule added to `AgentCoreStack` itself, not a separate stack (Runtime ARNs aren't cross-stack-name-predictable — confirmed with project owner). `cdk synth` verified with real Docker builds for both images. Not yet deployed. |
| 5 | WS | WS-03 | Application: Client API Lambda handlers | ✅ | WS-01, WS-02 | `create_goal` in `garden_handler.py`: persists Goal (status Intake), publishes `goal.submitted`. Contract tests validate responses against `openapi.yaml`'s schemas directly (`jsonschema`). Known trade-off: DynamoDB write + EventBridge publish aren't atomic (documented, accepted for this epic). |
| 6 | WS | WS-04 | Application: Orchestrator Lambda | ✅ | WS-02, WS-03 | `app/orchestrator/orchestrator.py`: Strands agent loop, agents-as-tools wrapping `InvokeAgentRuntime`, DynamoDB read/write-back, structured logging + `trace_attributes`. Smoke-tested end-to-end against dev: found + fixed a real IAM gap (AgentCore authorizes against the runtime-*endpoint* sub-resource, not the bare runtime ARN). |
| 7 | WS | WS-05 | Frontend: real photo capture + goal submission | ✅ | WS-01, WS-03 | Reuses OB-02's `PhotoPickerComponent` (no separate `getUserMedia` build — same HTTP-only-deployment reasoning as OB-02). Wires the Capture screen to real `createGoal`/media-upload calls; deleted the scripted `CaptureService` timeline + fabricated findings UI it replaced (not repurposed). `docs/roadmap.md` Phase 4. |
| 8 | AF | AF-01 | Infra: generalize Prompts/Guardrails/AgentCore to N specialists | ✅ | WS-02 | All three stacks loop over their data folders; second real specialist (**vision**) deploys with zero stack-code changes (same built image, verified via `cdk synth`). Per-specialist tool IAM scoping (the AC that was deferred) is now done — see AF-03. |
| 9 | AF | AF-02 | Template agent: one config-driven codebase for every specialist | — | AF-01 | **Skipped (2026-09-13):** pure rename (`hello_agent/` → `template_agent/`), no new capability — the "one codebase, many specialists" proof already happened via `vision.json`. AF-03 built directly on AF-01 instead. |
| 10 | AF | AF-03 | Tools: Lambda-backed Tool APIs + agent-side binding | ◐ | AF-01 | `app/tools/weather/` (Lambda + IAM Function URL, real Open-Meteo forecast) + tool-agnostic HTTP binding in `agents/hello_agent/agent.py` (SigV4-signed) + per-specialist IAM execution roles (`_make_agent_role`, replacing one shared role). `vision.json` declares `tools: ["weather"]`. Unit + CDK tests green; real `cdk synth` confirms scoped IAM. Only gap: manual dev smoke test not yet run (needs deploy). |
| 11 | AF | AF-04 | Guardrails: author the Agronomy specialist's real guardrail policy | ☐ | AF-01 | Real agronomy-specific denied topics/PII policy, replacing AF-01's placeholder stub. `vision_guardrail.json` (rank 8) reuses `hello`'s topics verbatim — the domain-tuning + eval-scenario work this story calls for is still genuinely open. |
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
| 30 | OB | OB-03 | Show the real plant photo (not a generic icon) | ☐ | OB-02 | Explicitly deprioritized ("will do later") — not blocking Phase 3. Presigned **GET** URL (never a public bucket — that was considered and rejected, see the story's Context) returned from `createPlant` immediately, plus a real `GET /gardens/{id}/plants` list operation so photos survive a page reload. |

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
