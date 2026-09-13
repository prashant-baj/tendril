# Tendril — Backlog

Single tracking sheet for every story across epics. Rows are ordered by **Rank** (1 = do next).
To reprioritize, move a row up/down and renumber the Rank column. Status reflects the repo audit
on the date below — re-verify before starting a story.

**Last updated:** 2026-09-13, latest (built, tested, and deployed **PA-05** — check-in agent
feedback + a real "How this was decided" specialist-trace section. A task check-in
(`postTaskCheckin`) now publishes `task.checkin.received`; the orchestrator treats it as a third
kind of turn (alongside goal submission and chat) — assesses the check-in's own photo against the
plan, writes feedback onto `Task.feedback`, and may revise the plan if warranted, reusing the
existing `_run_turn`/`_apply_turn_result` machinery unchanged. Each specialist call is now recorded
into a shared trace persisted on the `Plan` item, surfaced by `getGoalDetail` and rendered as a
real, collapsible "How this was decided" section (recreated from the very first frontend mockup,
which was 100% fixture data before this). **The real fix underneath both:** `_write_plan_and_tasks`
used to unconditionally wipe and recreate a goal's *entire* task list on every revision (chat- or
now check-in-triggered), silently discarding any completed check-in's status/photo/feedback — a
real, pre-existing correctness gap, now closed by never touching an already-`done` task across a
revision. **Found + fixed a second real bug live:** `getGoalDetail` 500'd the moment a plan
actually had a trace — DynamoDB's resource-layer `Table` deserializes numbers as `decimal.Decimal`,
which `json.dumps` can't serialize; fixed with an explicit `int()` cast, with a regression test
using `Decimal` in its fixture (a plain-`int` fixture couldn't have caught this). Deployed to `dev`
(`client-api` → `agentcore` → frontend) and live-verified end to end, including a genuine
plan-revision-preserves-done-tasks scenario: two specialists consulted and traced on goal
submission, two tasks checked in with photos, one check-in's `vision` call timed out and was
retried by EventBridge (handled gracefully), the other triggered a real plan revision that
correctly preserved both completed tasks' photos/feedback while only the pending tasks were
replaced. See `stories/plan-approval.md`'s PA-05 section for full detail.)

**Earlier, 2026-09-13:** built, tested, and deployed **OB-03** — plant photos now
render for real. `createPlant`/`listPlants` resolve a fresh presigned GET `photoUrl` per plant
with a linked photo, reusing PA-03/PA-04's `_generate_download_url` helper. **Found + fixed a real
gap along the way:** `create_plant` only ever set `Media.plant_id` (one-directional, Media→Plant),
never `Plant.media_id` — so there was actually no way to look a plant's photo back up at all;
added `media_id` onto the `Plant` item itself (mirrors `Goal.media_ids`' role), making it a direct
`GetItem` rather than a scan/GSI. Frontend: `PlantCardComponent`/`PlantRowComponent` render the
real photo with an icon fallback on load error (`photoFailed` signal). Deployed to `dev`
(`client-api` → frontend) and live-verified: created a plant with a real photo, confirmed both
`createPlant`'s response and a subsequent `listPlants` call resolved a working presigned url
(fetched the actual photo bytes, HTTP 200), then cleaned up the smoke-test data. See
`stories/garden-onboarding.md`'s OB-03 section for full detail.)

**Earlier, 2026-09-13:** built, tested, and deployed **PA-04** — closed the
Plant/Goal/Task/Media traceability gap `data-architecture.md` had reserved fields for since the
original design but no code had ever populated (`Goal.plant_id`, `Media.goal_id`), and added a new
real capability: a task check-in photo (`postTaskCheckin`) that attaches a photo to a `Task` and
flips it to `done` in one transaction. `createGoal` now propagates an optional `plantId` onto every
attached `Media` record; the orchestrator stamps that `plant_id` onto every `Task` it proposes for
the goal. Frontend: `CaptureComponent` gets an optional plant picker, `PlanTaskCardComponent` gets
a photo-picker/check-in UI, `GoalDetailComponent` wires the upload→checkin→refetch chain. Deployed
to `dev` (`client-api` → `agentcore` → frontend) and live-verified end to end: a goal created with
a real `plantId` + photo correctly stamped `goal_id`/`plant_id` onto the `Media` record; checking
in its task with a second photo correctly flipped it to `done` and stamped `task_id`/`goal_id`/
`plant_id` onto that `Media` record too. **Found (not caused by this change):** a cold/just-
redeployed AgentCore runtime's first `vision` call can take minutes, long enough to trip the
orchestrator Lambda's 300s timeout — handled gracefully by the orchestrator's existing fail-open
behavior (asks a clarifying question instead of crashing). See `stories/plan-approval.md`'s PA-04
section for full detail.)

**Earlier, 2026-09-13:** built, tested, and deployed **PA-01/PA-02/PA-03 together** —
the orchestrator now proposes a real, structured `Plan`+`Task` list via Strands' verified
`structured_output_model` mechanism (no specialist changes needed), a full chat thread lets the
gardener discuss/revise it or answer a clarifying question, an explicit **Approve** action is a
synchronous, deterministic status flip (not event-routed — a real, documented deviation from the
original design, since approval needs no model reasoning), and goal photos render for real in Goal
Detail. Frontend: `HttpGoalApi` replaces the mocked reads; Home splits into real "Goals in
progress"/"Needs your attention" sections; the old mockup-only trace/follow-up UI and models were
deleted, not repurposed. Deployed to `dev` (`client-api` → `agentcore` → frontend) and smoke-tested
live against a real goal, including a full propose → chat-revise → approve round-trip (the model
correctly incorporated a follow-up correction — "I already water daily" — and revised the plan
accordingly). **Found + fixed a real IAM bug**: `getGoalDetail`'s presigned photo `downloadUrl`
403'd on actual fetch — `client_api_stack.py` only ever granted the garden handler `s3:PutObject`
(OB-02's upload flow), never `s3:GetObject`; `generate_presigned_url()` had signed it successfully
regardless, masking the gap until something really tried to fetch it. Fixed with
`media_bucket.grant_read(garden_handler)`, redeployed, re-verified live (HTTP 200). All three
stories flip to ✅; `data-architecture.md` and `stories/plan-approval.md` updated to match what was
actually built, including the `plan.approval.responded` event's removal. See
`stories/plan-approval.md` for full detail.)

**Earlier, 2026-09-13:** configured + deployed four new specialists —
**agronomy, irrigation, pest_disease, pruning** — each with a real prompt/guardrail/registry
entry matching `vision`'s pattern, ahead of PA-01 per direct instruction. Found + fixed a real
`cdk synth` bug (AgentCore `runtime_name` rejects hyphens — `"pest-disease"` → `"pest_disease"`).
Deployed to dev (`prompts` → `guardrails` → `agentcore`) and smoke-tested live with two real goals:
one correctly consulted both irrigation+agronomy, one correctly consulted both
pest_disease+pruning, both producing real, safe, useful multi-specialist-synthesized answers.
Also updated the orchestrator's own system prompt to explicitly encourage consulting more than one
specialist per goal. **Found a real guardrail false-positive**: `pest_disease`'s
`UnsafeChemicalUse` topic blocked its own legitimate output once (self-healed via the
orchestrator's retry) — extends AF-04's already-open scope from vision/agronomy to also cover
pest_disease. See `stories/agent-factory.md`.)

**Earlier, 2026-09-13:** researched real external data sources — Pl@ntNet,
Plantix, SoilGrids, Agmarknet/data.gov.in — each specialist could call as a tool; recorded as
**AF-06** in `stories/agent-factory.md`, explicitly deferred: every specialist relies on the
foundation model's own reasoning alone for now, revisit once PA-01 + a couple of real specialists
are live and an actual gap shows up. Added at rank 18, below the two new-epic placeholders it
depends on; ranks 18-34 below shifted down by one to make room.)

**Earlier, 2026-09-13:** a live gardener test — delete old plants, add one real plant + issue
report — re-confirmed the pipe works end-to-end with no errors and a correct diagnosis; wrote a
new epic, **`stories/plan-approval.md`** (PA-01/02/03), storying the previously-placeholder
Phases 4.5/5 and adding a new Phase 4.6 (photo display), per a direct request to flesh out "propose
a goal," "approve a goal/plan," and "show uploaded photos back in the app" before building them.
PA-02 makes one real, documented design call: conversational approval is built as stateless
per-turn orchestrator re-invocations, **not** yet the `SnapshotSessionManager`/`AgentStateBucket`
session-resume `data-architecture.md` §3.1 already designed — that's deferred to a new **Phase
7.5+** (tracker/scheduler + multi-day follow-ups), inserted below AF-05, where it's actually
needed. Ranks 13-30 below are renumbered to make room for the three new rows; OB-03's remaining
scope narrowed (its list-plants AC turned out to already be done by the unstoried plant-lifecycle
work) and now shares a presigned-GET helper with PA-03 instead of duplicating it.)

**Previously (2026-09-13):** OB-01/OB-02/WS-01..05 all done and smoke-tested end-to-end in dev —
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
declares `tools: ["weather"]` as the proof; fixed a real quality bug the weather tool surfaced —
`vision` was crediting unrelated weather conditions (light drizzle/humidity) for symptoms that
don't match (dry, curling leaves), so `vision-system.md` now treats weather as corroborating
evidence only, never the primary explanation; then built **AF-05** (Memory & Context) directly on
AF-01/AF-03: a new `infra/stacks/memory_stack.py` (Bedrock Knowledge Base on **S3 Vectors**, a
deliberate choice over OpenSearch Serverless to avoid this project's first continuously-billed
resource — confirmed with the project owner), per-garden `MemoryManager`/`BedrockKnowledgeBaseStore`
scoping in the shared template, `context_manager="auto"` for context-window bounding, and the same
per-specialist IAM scoping pattern AF-03 established, now for memory access too; `vision.json`
declares `memory.enabled: true`. Context pinning (vision/success-criteria) and the manual smoke
test are the two pieces genuinely left open, both requiring a deploy or session-continuity work
not yet built. Also upgraded `strands-agents` from an unpinned, locally-stale 1.5.0 to a pinned
1.55.1 — the version gap had hidden that `MemoryManager`/`ContextInjector`/etc. didn't exist
locally at all)

**Status legend:** ✅ Done · ◐ Partial · ☐ To do
**Epics:** **TF** = Technical Foundation (`stories/technical-foundation.md`) · **PG** = Prompt & Guardrail MVP (`stories/prompt-guardrail-mvp.md`) · **OB** = Garden Onboarding (`stories/garden-onboarding.md`) · **WS** = Walking Skeleton (`stories/walking-skeleton.md`) · **AF** = Agent Factory (`stories/agent-factory.md`) · **PA** = Plan Proposal, Conversational Approval & Photo Review (`stories/plan-approval.md`)

**Active development plan:** [`docs/roadmap.md`](./roadmap.md) sequences OB → WS → AF → PA as a
UI-first, incremental feature track (2026-09-12, extended 2026-09-13) — ranks 1-19 below reflect
that plan; rank 20 (AF-06) is a deliberately-deferred research note, not active work. The
pre-existing `TF`/`PG` housekeeping (ranks 20+) is still real, open work; it's just no longer the
immediate next thing.

## Story board (at a glance)

A quick-glance grouping of the same rows below by status — the ranked table remains the single
source of truth (rank, dependencies, notes); this section is just a faster read. Regenerate it by
eye whenever a Status cell changes below.

**✅ Completed (22)**
OB-01 · OB-02 · OB-03 · WS-01 · WS-02 · WS-03 · WS-04 · WS-05 · AF-01 · PA-01 · PA-02 · PA-03 ·
PA-04 · PA-05 · PG-06 · PG-05 · TF-02 · TF-03 · TF-04 · PG-01 · PG-03 · PG-04

**◐ In progress (10)**
AF-03 (tools — manual dev smoke test pending) · AF-04 (real guardrail content now exists for 4
specialists; confirmed `pest_disease` over-block still needs the eval-scenario retune) · AF-05
(memory — context pinning + smoke test pending) · PG-07 (guardrail eval — 4/6 pass) · TF-05
(`.mcp.json` missing on disk) · TF-01 (`.pre-commit-config.yaml` missing on disk) · TF-08
(pre-commit gitleaks missing) · PG-02 (prompt scope/refusal framing not yet added) · TF-07 (no
prod deploy path yet) · TF-06 (frontend has no formal per-screen stories)

**☐ Backlog (3, ranked)**
1. *(not yet storied)* Tasks & Activity on real data (rank 18)
2. *(not yet storied)* Real session-resume + tracker/scheduler + `TasksDueIndex` (rank 19)
3. **AF-06** — external data-source tools for specialists, researched but deferred (rank 20)

**Not on the board:** AF-02 (rank 9) — deliberately **skipped**, not backlog: a pure rename with
no new capability, superseded by `vision.json` already proving the "one codebase, many
specialists" pattern.

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
| 11 | AF | AF-04 | Guardrails: author the Agronomy specialist's real guardrail policy | ◐ | AF-01 | Real, domain-reworded (not copy-pasted) denied-topic policies now exist for agronomy/irrigation/pest_disease/pruning (2026-09-13). **Confirmed still open:** a live smoke test found `pest_disease`'s `UnsafeChemicalUse` topic over-blocking a legitimate, safe answer — the eval-scenario proof this story calls for ("safe advice isn't over-blocked") is real, necessary follow-up work, not hypothetical. |
| 12 | AF | AF-05 | Memory & Context: per-garden memory for every specialist | ◐ | AF-01, AF-03 | `infra/stacks/memory_stack.py` (Bedrock KB on S3 Vectors — pay-per-use, not OpenSearch Serverless) + per-garden `MemoryManager`/`BedrockKnowledgeBaseStore` in the template + `context_manager="auto"` + per-specialist IAM (mirrors AF-03). Scoped per-garden not per-user (no auth yet). Open: context pinning (needs session continuity, not yet built) and the manual dev smoke test. |
| 13 | PA | PA-01 | The orchestrator proposes a structured Plan | ✅ | WS-04 | `docs/roadmap.md` Phase 4.5. `stories/plan-approval.md`. Real `Plan`/`Task` DynamoDB entities + `GET /gardens/{id}/goals[/{goalId}]`. Structured output via Strands' real `structured_output_model` mechanism (verified against installed source), applied only at the orchestrator's synthesis step — no specialist changes needed. Deployed + smoke-tested live in `dev`. |
| 14 | PA | PA-03 | Show the goal's photo(s) in Goal Detail | ✅ | PA-01 | `docs/roadmap.md` Phase 4.6. `stories/plan-approval.md`. Presigned **GET** URLs for goal-attached media via a shared `_generate_download_url` helper (later reused by OB-03, rank 36, and PA-04, rank 16). Deployed + smoke-tested live. |
| 15 | PA | PA-02 | Conversational plan approval (chat + explicit Approve) | ✅ | PA-01 | `docs/roadmap.md` Phase 5. `stories/plan-approval.md`. Real chat (`Message` entity) + explicit, deterministic approve. **Deviation from the original event-based design:** approve is a fully synchronous Client API operation (no model reasoning needed, so no event/orchestrator round-trip) — `plan.approval.responded` was never implemented. Still does *not* build `SnapshotSessionManager`/`AgentStateBucket` session-resume — each chat turn is a fresh, stateless orchestrator invocation; true session-resume stays deferred to rank 19. Approved goals surface under Home's "Goals in progress"; a real "Needs your attention" section covers goals still awaiting approval. Deployed + smoke-tested live — a real goal correctly consulted multiple specialists and produced a real Plan. |
| 16 | PA | PA-04 | Complete the Plant/Goal/Task/Media traceability chain, add task check-in photos | ✅ | PA-01, PA-03 | `stories/plan-approval.md`. `createGoal` propagates an optional `plantId` onto every attached `Media` record (`goal_id` always, `plant_id` if given); the orchestrator stamps that `plant_id` onto every `Task` it proposes; new `postTaskCheckin` attaches a check-in photo to a `Task` and flips it to `done` in one transaction (one check-in per task, not a repeatable log). Frontend: plant picker on Capture, photo-picker/check-in UI on `PlanTaskCardComponent`. Deployed + live-verified end to end (2026-09-13). |
| 17 | PA | PA-05 | Check-in agent feedback + a real "How this was decided" trace | ✅ | PA-01, PA-04 | `stories/plan-approval.md`. New `task.checkin.received` event — the orchestrator treats a check-in as a third kind of turn, assesses its own photo against the plan, writes `Task.feedback`, and may revise the plan (reuses `_run_turn`/`_apply_turn_result` unchanged). Each specialist call is recorded into a trace persisted on the `Plan` item, surfaced as a real "How this was decided" section (recreated from the original fixture-only mockup). **Real fix underneath both:** `_write_plan_and_tasks` no longer wipes `done` tasks on any revision (chat- or check-in-triggered) — a genuine pre-existing correctness gap, now closed. **Found + fixed live:** `getGoalDetail` 500'd on `Decimal`-typed trace `ms` values (DynamoDB's resource-layer deserialization) — fixed with an explicit `int()` cast. Deployed + live-verified end to end, including a real plan-revision-preserves-done-tasks case (2026-09-13). |
| 18 | — | — | *(not yet storied)* Tasks & Activity on real data | ☐ | WS-04 | `docs/roadmap.md` Phase 6 — replaces `MockTaskApi`/`MockActivityApi`. Write the story once Phase 5 ships. |
| 19 | — | — | *(not yet storied)* Real session-resume + tracker/scheduler + `TasksDueIndex` follow-ups | ☐ | AF-05, PA-02 | `docs/roadmap.md` Phase 7.5+ — builds the `SnapshotSessionManager`/`AgentStateBucket` design PA-02 deliberately deferred, plus the tracker/scheduler Lambda (`app/tracker/`, not yet scaffolded) and `TasksDueIndex` GSI (`data-architecture.md` §2/§9). Prerequisite for using photos to compare progress over time (see "Carried forward" below). |
| 20 | AF | AF-06 | External data-source tools for specialists (researched, deferred) | ☐ | AF-03, PA-01 | `stories/agent-factory.md`. Researched real candidates (Pl@ntNet, Plantix, SoilGrids, Agmarknet/data.gov.in) 2026-09-13 — **deliberately not built**; every specialist relies on the foundation model's own reasoning alone until PA-01 + a couple of real specialists surface an actual gap. Re-verify each candidate's live status/terms before picking it up (Plantix looks commercial; SoilGrids' own docs flag instability). |
| 21 | PG | PG-07 | Verify: eval scenarios for prompt & guardrail | ◐ | PG-02, PG-04, PG-05 | Eval script built; live run **4/6 pass**. Open: (a) commit + redeploy the retuned `UnsafeChemicalUse` topic, then re-verify the safe-IPM case no longer over-blocks; (b) **PII returned `action=NONE`** — run `eval_guardrail.py --debug` and diagnose (region/feature vs input-vs-output masking). |
| 22 | TF | TF-05 | Build with AI setup (Strands MCP + rules) | ◐ | TF-01 | `llms.txt` present, `CLAUDE.md` references it. **Gap:** `.mcp.json` (`uvx strands-agents-mcp-server`) is **missing on disk** — MCP docs server not actually wired. Re-add it. |
| 23 | TF | TF-01 | Repository & monorepo structure | ◐ | — | Structure/config/LICENSE/README present. **Gap:** `.pre-commit-config.yaml` is **missing on disk** — local format/lint/gitleaks hooks not installed. Re-add it. |
| 24 | TF | TF-08 | Secrets & configuration baseline | ◐ | TF-01, TF-03 | gitleaks in **CI** ✅; `.env.example`, `config.get_secret`, CDK context ✅. **Gap:** pre-commit gitleaks missing (same missing file as TF-01). |
| 25 | PG | PG-02 | Prompt-layer safety: scope & refusal guidance | ◐ | PG-01 | Prompt exists; add explicit scope + first-line refusal framing mirroring guardrail denied topics; publish new version. |
| 26 | TF | TF-07 | CI/CD & selective deploy (GitOps) | ◐ | TF-03 | PR lint/test/gitleaks + OIDC + path deploy to dev all working; prompts/guardrails wiring done (PG-06). **Gaps:** no prod deploy path (tag/approval gate); branch protection account-side. |
| 27 | TF | TF-06 | Local development environment | ◐ | TF-01 | Pinned Python/Node, `tasks.ps1`, per-component requirements, setup guides present. `frontend/` is now a scaffolded Angular 19 PWA (design implemented, ADR-0010 deploys it to S3) — no formal feature stories written yet for the individual screens (see `frontend/README.md`). |
| 28 | PG | PG-06 | Decoupled deploy & CI for prompts and guardrails | ✅ | PG-03, TF-07 | `ci.yml` has `prompts/**`+`guardrails/**` filters and deploys `foundation → prompts → guardrails → agentcore` (cold-start order). Committed + pushed; prompts & guardrails deployed standalone. |
| 29 | PG | PG-05 | Deterministic floor: schema validation & fail-safe | ✅ | PG-01, PG-04 | `resolve_user_message` payload validation + lazy init in `agent.py`; 13 unit tests + 7 policy-limit tests + 3 CDK tests, all green. Committed. |
| 30 | TF | TF-02 | AWS account & CLI setup (dev/prod) | ✅ | — | `verify-aws.*` + `developer-setup.md`; account-side items proven by successful deploys + Bedrock responses. |
| 31 | TF | TF-03 | CDK bootstrap & IaC baseline | ✅ | TF-02 | `env_name`-parameterized, env-prefixed stacks, no hardcoded account IDs, synth/deploy succeed. |
| 32 | TF | TF-04 | AgentCore CLI & runtime prerequisites | ✅ | TF-02, TF-03 | Strands + bedrock-agentcore, ARM64 Dockerfile, exec role, OTel; hello agent deployed and returned a greeting. |
| 33 | PG | PG-01 | Externalize the hello agent's system prompt | ✅ | TF-04 | `PromptsStack` reads `prompts/hello-system.md`; runtime resolves by `PROMPT_NAME` with fallback; scoped IAM. Deployed. |
| 34 | PG | PG-03 | Provision the Bedrock Guardrail as IaC (policy-as-data) | ✅ | TF-03 | `guardrails/hello_guardrail.json` + `GuardrailsStack` → `CfnGuardrail`/version; deployed (retune pending, see PG-07). |
| 35 | PG | PG-04 | Attach & resolve the guardrail at runtime | ✅ | PG-03 | Runtime resolves by `GUARDRAIL_NAME`, attaches to `BedrockModel`, fail-open MVP; scoped guardrail IAM. |
| 36 | OB | OB-03 | Show the real plant photo (not a generic icon) | ✅ | OB-02 | `stories/garden-onboarding.md`. `createPlant`/`listPlants` now resolve a fresh presigned **GET** `photoUrl` per plant with a linked photo, reusing PA-03/PA-04's `_generate_download_url` helper rather than duplicating it. **Found + fixed a real gap along the way:** `create_plant` only ever set `Media.plant_id` (one-directional), never `Plant.media_id` — so there was no way to look a plant's photo back up at all; added `media_id` onto the `Plant` item itself (mirrors `Goal.media_ids`). Frontend: `PlantCardComponent`/`PlantRowComponent` render the real photo with an icon fallback on load error. Deployed + live-verified (2026-09-13): created a plant with a real photo, confirmed both `createPlant` and a subsequent `listPlants` resolved a working presigned url. |

## Carried forward (future epics — not yet storied)

Plan proposal, photo review, conversational approval, the Plant/Goal/Task/Media traceability
chain, and check-in feedback + the specialist trace are **now storied** (PA-01/03/02/04/05, ranks
13-17) — no longer placeholders. Tasks/Activity-on-real-data and the session-resume/tracker epic
are still placeholders, at ranks 18-19 — see `docs/roadmap.md` Phases 6, 7.5+. Still genuinely
future, not yet storied: full garden/plant editing beyond OB-01/02's create-only scope, a
multi-garden switcher, the remaining 7 specialist agents and 4 tools beyond Agronomy/Weather, the
WebSocket push channel + live result rendering, auth (Cognito), Cedar authorization + wider
Interventions/HITL handlers, aggregated-data/learning layer, and a repeated-check-in progress *log*
over time (PA-05 already lets one check-in give feedback and optionally revise the plan; a real
log needs rank 19's tracker first — PA-03, rank 14, only builds the *display* mechanism).
Add as feature stories when scoped, then insert into the table with a Rank.

**Explicitly deprioritized:** the **WhatsApp (and email) notification/reply channel** — in scope
per `project-context.md`'s vision, but pushed behind UI-based HITL and notifications (ADR-0001
refinement, 2026-09-12). Don't pick this up before the UI channel is built and working.

## How to use this sheet

- **Reprioritize:** move a row and renumber **Rank** (1 = next). Keep the table sorted by Rank.
- **Status change:** update the Status cell (✅/◐/☐) and trim the Notes gap when closed.
- **New story:** add a row, give it a Rank, link its epic file. Cross-cutting gaps map to existing stories (e.g. the missing pre-commit file → TF-01 + TF-08), so track them there rather than duplicating.
- **Definition of Done** for every story lives in `engineering-best-practices.md` (CI green, no hardcoded secrets/accounts, deploys to dev, only-changed-folders, docs/ADRs updated, human-reviewed).
