# Stories — Epic: Walking Skeleton (photo → orchestrator → response)

The first end-to-end vertical slice of the real application, referenced in
`docs/project-context.md`'s "Carried forward" epics: a user **uploads a photo** (device camera
or a local file) and **writes an issue** (a goal/concern in plain language); the Client API
persists it and hands off asynchronously; the **orchestrator Lambda** wakes, runs a Strands
agent loop, and calls at least one registered specialist. Nothing here builds real domain
expertise yet — the goal is to prove the **pipe**, the same way `PG-*` proved prompts/guardrails
end-to-end on the `hello` agent before specialists multiplied. The migrated `hello` agent stands
in as the orchestrator's only registered specialist for this epic.

Each story is scoped to **one architectural layer** and is build-ready for AI-assisted
development: explicit acceptance criteria, conforming to the
[ADRs](../architecture/ADRs/) — principally **[ADR-0011](../architecture/ADRs/0011-openapi-contract-first-client-api.md)**
(contract-first API) and **[ADR-0012](../architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md)**
(orchestrator Lambda + declarative agent registry) — and the
[engineering best-practices checklist](../engineering-best-practices.md).

**Story format:** `As a <role>, I want <capability>, so that <benefit>` + Acceptance Criteria +
Tasks + Dependencies + Status.

**Explicitly out of scope for this epic** (carried forward to later epics): garden/plant CRUD
beyond what a goal needs to attach to, plan proposal/approval (HITL), real domain specialist
agents (agronomy/pest/etc. — only the `hello` stand-in is wired here), the tracker/outcome loop,
and the WebSocket push channel (ADR-0004) — this epic proves submission + orchestration trigger,
not the full result round-trip back to the user in real time.

**Suggested order:** WS-01 → WS-02 → WS-03 → WS-04 → WS-05.

**Status legend:** ✅ done · ◐ partially done · ☐ to do.

> **Resequencing note (2026-09-12):** per [`docs/roadmap.md`](../roadmap.md), this epic is now
> picked up **after** the new Garden Onboarding epic (`docs/stories/garden-onboarding.md`,
> OB-01/OB-02), not immediately after PG-*. Two scope adjustments follow: the presigned
> media-upload operation (`POST /gardens/{id}/media`) and the camera/file-picker UI component are
> now built in **OB-02**, reused here rather than re-specified — WS-01 only needs to add the
> goal-intake operation by the time it's picked up. `ClientApiStack` is stood up in **OB-01**, so
> WS-02 adds to an existing stack rather than creating one. The acceptance criteria below are
> otherwise unchanged and still the right detail to build from.

---

## WS-01 — API: contract-first OpenAPI for photo upload + goal intake

**As a** developer (backend and frontend both build against this), **I want** the "upload a
photo" and "submit a goal" operations defined in OpenAPI 3.x before either side is implemented,
**so that** both layers build against one agreed shape from day one (ADR-0011).

**Acceptance Criteria**
- [x] `app/api/openapi.yaml` (OpenAPI 3.0.x — API Gateway's `SpecRestApi` import rejects 3.1,
  ADR-0011's 2026-09-12 refinement) exists with `info`/`servers` populated per environment.
- [x] `POST /gardens/{gardenId}/media` — request `{contentType, fileName}` → response
  `{uploadUrl, mediaId}` (a presigned S3 **PUT** URL, per ADR-0004's direct-to-S3 upload flow).
  **Already existed** — OB-02 built this before WS-01 was picked up (roadmap resequencing).
- [x] `POST /gardens/{gardenId}/goals` — request `{description, mediaIds?: string[]}` (the
  issue/prompt text plus optional photo references) → response `{goalId, status}`, **202
  Accepted** (orchestration is async — ADR-0004/ADR-0012, not a synchronous result).
- [x] `components.schemas` defines `Goal`/`GoalCreateRequest`/`GoalCreateResponse`, matching the
  GOAL entity in `architecture.md` §2 (`goal_id`, `description`, `type`, `status`) plus
  `mediaIds`. (`Media` schemas — `MediaUploadRequest`/`MediaUploadResponse` — already existed
  from OB-02.)
- [x] Each operation carries an `x-amazon-apigateway-integration` block (`type: aws_proxy`)
  with the Lambda URI left as a placeholder token for infra (WS-02) to patch at synth time.
- [x] A spec-lint step is wired into `ci.yml`'s `quality` job (`openapi-spec-validator`, not
  Spectral — lighter-weight, Python-native, no Node/npm needed in that job). It caught a real
  gap while implementing this: `gardenId` wasn't declared for every operation under paths that
  use it (including the shared CORS `options` block) — fixed by moving path parameters to the
  path-item level (the correct OpenAPI idiom) instead of repeating them per operation.
- [x] No account IDs/ARNs in the file — placeholder tokens only.

**Tasks**
- [x] Author `app/api/openapi.yaml` scoped to just the goal-intake operation (media already
  existed from OB-02; the full ADR-0004 endpoint table is future work, not this epic).
- [x] Add `components.schemas` for `Goal`/`GoalCreateRequest`/`GoalCreateResponse`.
- [x] Wire spec linting into `ci.yml`.

**Dependencies:** none — this is the design-first artifact everything else builds against.
**Status:** ✅ done.

---

## WS-02 — Infra: Client API, EventBridge trigger, and the declarative agent registry

**As a** developer, **I want** CDK stacks that provision the Client API by importing the OpenAPI
contract, an EventBridge rule linking ingestion to the orchestrator, and a declarative agent
registry (migrating `hello_agent` into it) that both `AgentCoreStack` and the orchestrator read,
**so that** the photo→goal→orchestrator pipe has real, GitOps-deployed infrastructure to run on
(ADR-0011, ADR-0012).

**Acceptance Criteria**
- [x] A new `ClientApiStack` loads `app/api/openapi.yaml`, patches each operation's
  `x-amazon-apigateway-integration.uri` with the real Lambda ARN, and constructs
  `apigateway.SpecRestApi` — `cdk synth` produces an `AWS::ApiGateway::RestApi` with both
  operations wired. **Already existed** — stood up in OB-01, extended here with the goals path.
- [x] An EventBridge rule routes a `goal.submitted` event from the Client API Lambda to the
  orchestrator Lambda, asynchronously — no synchronous coupling between them.
- [x] `agents/registry/hello.json` exists (per ADR-0012's format, using field name `template`
  per the registry's own README rather than the ADR's original `folder` name — same concept,
  and `template` is what `agents/registry/README.md` already anchored); `AgentCoreStack` is
  refactored to **loop over `agents/registry/*.json`** instead of the single hardcoded
  `_agent_runtime()` call — `hello_agent` is migrated, not duplicated.
- [x] The orchestrator Lambda's execution role grants `bedrock-agentcore:InvokeAgentRuntime`
  scoped to the registered runtime ARN(s) only — no wildcards. Verified directly in the
  synthesized template (`Resource: Fn::GetAtt HelloRuntime.AgentRuntimeArn`, not `"*"`).
- [x] The orchestrator Lambda's environment carries the deploy-time invocation manifest (agent
  name → runtime ARN + description) built from the same registry, via `Stack.to_json_string(...)`
  so the still-unresolved `agent_runtime_arn` CDK token embeds correctly into the env var.
- [x] All new/changed resources are env-prefixed; `cdk synth` succeeds (verified, including a
  real Docker build of both the `hello` agent image and the new orchestrator image — not yet a
  real `cdk deploy` to `dev`); no hardcoded account IDs/secrets.
- [x] CDK assertion tests cover: one runtime provisioned per registry file (this one actually
  caught a real bug — `registry_entries` was keyed by filename stem but looked up by the JSON's
  internal `name` field, which silently broke the moment a fixture file's name didn't match its
  declared `name`), the orchestrator role's `InvokeAgentRuntime` resource is scoped (not `*`),
  and `SpecRestApi` synthesizes with all operations present.

**Implementation note (architecture):** the orchestrator Lambda and its EventBridge rule live in
`AgentCoreStack` itself, not a separate `OrchestratorStack` — confirmed with the project owner
before implementing. AgentCore Runtime ARNs (unlike table/bucket names) include an
AWS-generated id and aren't predictable across stacks the "stable name" way ADR-0008 established
for prompts/guardrails/tables; keeping the orchestrator in the same stack lets it reference
`runtime.agent_runtime_arn` as an in-memory CDK token, avoiding a CFN cross-stack export/import
(exactly the "exported-value-in-use" trap ADR-0008 rejected).

**Tasks**
- [x] Implement the OpenAPI YAML-patch + `SpecRestApi` construction in `ClientApiStack`.
- [x] Add the EventBridge rule wiring ingestion → orchestrator.
- [x] Refactor `AgentCoreStack` to the registry loop; author `agents/registry/hello.json`.
- [x] Scope the orchestrator's execution role IAM to registered runtime ARNs.
- [x] Add CDK assertion tests for all of the above.

**Dependencies:** WS-01 (the contract to import); ADR-0005 (existing AgentCore pattern).
**Status:** ✅ done (implemented, `cdk synth` verified with real Docker builds; not yet deployed
to dev).

---

## WS-03 — Application (Lambda): Client API handlers — photo upload + goal intake

**As a** developer, **I want** the Client API Lambda(s) implementing both OpenAPI operations —
issuing a presigned S3 upload URL, and persisting a submitted goal with its event hand-off —
**so that** a user's photo and issue description are actually stored and trigger the
orchestrator, matching ADR-0004's async design.

**Acceptance Criteria**
- [x] `POST /gardens/{gardenId}/media` returns a presigned **PUT** URL scoped to the existing
  media bucket (`FoundationStack`) plus a generated `mediaId`; the Lambda never proxies file
  bytes. **Already existed** — OB-02.
- [x] `POST /gardens/{gardenId}/goals` validates the payload (non-empty `description`; any
  `mediaIds` are well-formed), persists a `GOAL` record to the `AppTable` (status `Intake`, per
  `architecture.md` §7.3), and **publishes the `goal.submitted` event before returning** — the
  202 response reflects real persisted state, not an in-memory acknowledgment. **Known,
  documented trade-off:** the DynamoDB write and the EventBridge publish aren't atomic (no
  cross-service transaction exists for this); if the write succeeds but the publish fails, the
  Goal record persists with no event ever fired — acceptable for an epic explicitly scoped to
  "prove the pipe," not production-hardened delivery; an outbox/saga pattern is future work if
  this gap ever matters in practice.
- [x] Handler responses conform exactly to the OpenAPI response schemas (WS-01), checked by a
  contract test (`jsonschema.validate()` against the spec's `components.schemas` loaded directly
  from `openapi.yaml` — not hand-copied fixtures, so the test breaks the moment the two diverge).
- [x] Malformed/missing input is rejected with a typed 4xx error — never an unhandled exception
  (same deterministic-floor posture as PG-05).
- [x] Unit tests cover: a valid upload request (OB-02), a valid goal submission (with and without
  `mediaIds`), and at least one validation-failure case per handler.
- [x] No hardcoded bucket names/ARNs/account IDs — read from CDK-injected env vars.

**Tasks**
- [x] Implement the media presigned-URL handler. (OB-02.)
- [x] Implement the goal-intake handler (validate → persist → publish event → respond).
- [x] Add unit tests + a contract test against `openapi.yaml`.

**Dependencies:** WS-01, WS-02. **Status:** ✅ done.

> **Found while implementing this story (2026-09-12):** `ci.yml`'s `quality` job's `pytest -q ||
> echo "no tests yet"` step had been silently masking a real, pre-existing failure — `boto3`
> (and every other test-only dependency: `strands-agents`, `bedrock-agentcore`, `aws-cdk-lib`,
> `jsonschema`) was never installed in that job, so `pytest` failed to even *collect*
> `app/api/tests` (and likely `agents/hello_agent/tests`) on **every** run since those test
> suites were added — the `|| echo` swallowed the failure and reported success regardless. Fixed
> by installing each deployable unit's own `requirements.txt` in the `quality` job and removing
> the fallback, so a real test failure now actually fails CI. This means "CI green" for any
> story between whenever `app/api/tests` was introduced and this fix did **not** mean the test
> suite ran — only lint/format/gitleaks were ever real gates in that window.

---

## WS-04 — Application (Lambda): Orchestrator — Strands agent loop triggered by the event

**As a** developer, **I want** an orchestrator Lambda that wakes on the `goal.submitted` event,
runs a Strands agent loop, and calls the registered `hello` specialist via
`InvokeAgentRuntime`, **so that** the full pipe — photo/issue in, orchestrator triggered, a
specialist actually invoked — is proven end-to-end before real domain specialists exist.

**Acceptance Criteria**
- [x] The orchestrator Lambda is invoked **asynchronously** by the EventBridge rule (WS-02/WS-03)
  — it is not reachable from the Client API's synchronous request path.
- [x] On invocation it loads the goal from DynamoDB, builds a Strands agent with the registered
  specialist(s) from its deploy-time invocation manifest wrapped as agents-as-tools, and runs at
  least one turn that calls the `hello` specialist via `InvokeAgentRuntime`. (Referenced media is
  not yet loaded/passed to the agent — the `hello` stand-in doesn't do anything with a photo;
  wiring that through is real work for whichever specialist first needs to *see* an image,
  matching data-architecture.md §4's two-option design, deliberately left open there.)
- [x] The specialist's response is persisted back onto the goal's DynamoDB record
  (`orchestrator_result`, status moves to `PlanProposed`), so a future status endpoint has
  something real to read.
- [x] Failures (specialist unreachable, malformed response) are caught and recorded on the goal
  record (`orchestrator_error`) — never an unhandled Lambda crash with no trace. **Implementation
  note:** the documented Goal lifecycle (architecture.md §7.3) has no dedicated "Failed" state,
  so status reverts to `Intake` (readable as "needs re-triggering") with the error field attached,
  rather than inventing a new lifecycle state not in the architecture doc.
- [x] The turn is traced via structured logging (`specialist_call name=... duration_ms=...
  outcome=...`) and Strands' own `trace_attributes={"session.id": goal_id, ...}` correlation
  mechanism (`.claude/skills/strands-agents/SKILL.md`'s documented pattern for this exact
  purpose). **Scope note:** a full OTel exporter/collector pipeline for a Lambda-hosted agent
  (as opposed to the AgentCore-hosted specialists, which already have one via
  `aws-opentelemetry-distro`) isn't wired — no established precedent for that exists yet
  elsewhere in this repo's Lambda tier, and misconfiguring one carries more risk than value
  right now; revisit if/when real distributed tracing is needed to debug a production issue.
- [x] Unit tests cover the agent-loop wiring with a **mocked** `InvokeAgentRuntime` call (no live
  AgentCore call in CI) — 8 tests: the tool-wrapping logic against a real (mocked-client)
  `invoke_agent_runtime` call, and the goal-lifecycle wiring (load → status transitions →
  success/failure paths) with `Agent`/`BedrockModel` themselves mocked (matching
  `agents/hello_agent/tests`' existing convention), so no live Bedrock/AgentCore call happens in
  CI either.
- [x] **Live smoke test, run 2026-09-12 against `dev`:** created a garden, then
  `POST /gardens/{id}/goals` with a real issue description → `202 {goalId, status: Intake}`.
  Polled the Goal record ~15s later: `status: PlanProposed`, a real `orchestrator_result` full
  of agronomy-flavored advice. **First attempt surfaced a real bug the mocked unit tests
  structurally cannot catch:** the specialist tool call failed with
  `AccessDeniedException: ... InvokeAgentRuntime ... on resource
  .../runtime/<id>/runtime-endpoint/DEFAULT` — AgentCore authorizes this action against the
  runtime's **endpoint** sub-resource, not the bare runtime ARN the IAM policy had granted. The
  orchestrator's own model handled the tool failure gracefully (Strands: tool errors return to
  the model as an error result, not an exception) and answered from general knowledge instead —
  which is *why* this didn't show up as a crash, only as a suspiciously permission-flavored
  answer. Fixed by granting both the bare ARN and `{arn}/runtime-endpoint/*`; redeploy + re-run
  pending to confirm the tool call itself now succeeds (the pipe and error-handling path were
  already proven correct by this same run).

**Tasks**
- [x] Scaffold `app/orchestrator/` (Strands agent; agents-as-tools built from the registry
  manifest; the `InvokeAgentRuntime` tool implementation).
- [x] Wire the DynamoDB read (goal) and write-back (result/failure state).
- [x] Add structured logging + Strands `trace_attributes` for the turn (see OTel scope note above).
- [x] Unit tests with a mocked AgentCore call.
- [x] Document the manual dev smoke test (above) — including the real bug it found.

**Dependencies:** WS-02, WS-03. **Status:** ✅ done — the redeploy + confirmation run this entry
was waiting on has since happened: multiple live smoke tests since (most recently 2026-09-13,
after a gardener deleted old plants and reported a real issue on a new one) show the specialist
tool call succeeding cleanly with no IAM error, no retry, and a correct diagnosis.

---

## WS-05 — Frontend: real photo capture (camera + local file) and goal submission

**As a** gardener using the app, **I want** to take a photo with my device camera or pick one
from local storage, describe my issue in plain language, and submit it, **so that** the Capture
screen becomes a real entry point into the pipeline instead of a scripted mock — closing the
seam the frontend's `Mock*Api` layer was deliberately built to be swapped at.

**Acceptance Criteria**
- [x] The Capture screen (`frontend/src/app/features/capture/`) offers a photo picker **and**
  falls back gracefully when no photo is given. **Implementation deviation from the original
  wording (per `docs/roadmap.md`'s resequencing note, §3):** reuses OB-02's shared
  `PhotoPickerComponent` (a plain `<input type="file" capture>`) instead of building a second,
  separate `getUserMedia`-based live camera preview — `getUserMedia` requires a secure context
  (HTTPS/localhost) and this app is deployed over plain HTTP (ADR-0010), the same class of bug
  already found and fixed for `crypto.randomUUID()`. The `capture` attribute still gets the
  native camera-or-gallery chooser on mobile without touching that API. A photo is optional here
  (not required to submit an issue), unlike the original wording's implication of always
  capturing one.
- [x] Confirming a photo requests a presigned upload URL from the real Client API
  (`POST /gardens/{id}/media`, WS-01/WS-03) and uploads **directly to S3** — never through the
  app's own backend.
- [x] The user enters their issue/goal as free text; submitting calls the real
  `POST /gardens/{id}/goals` (referencing the uploaded `mediaId`), replacing `MockGoalApi`'s
  in-memory fixture for this flow.
- [x] Request/response types mirror `app/api/openapi.yaml`'s schemas (`CreateGoalRequest` in
  `core/models/goal.model.ts`) — **not** generated by tooling (no codegen step exists yet, see
  ADR-0011's still-open action item on this); hand-written to match, same as every other
  request/response type this session (`CreateGardenRequest`, `CreatePlantRequest`, etc.).
- [x] After submission, the UI shows a clear "submitted — your garden agent is on it" pending
  state. The simulated analyzing/found timeline (`CaptureService` + its fabricated
  findings/goal-panel UI) is **removed**, not repurposed — `CaptureService`,
  `capture.model.ts`, `shared/components/finding-card/`, and `GoalApi.getFindings()`/
  `getGoalFacts()` (plus the now-orphaned `Finding`/`GoalFact` types) are all deleted, since
  nothing else used them and they existed only to serve the mock this story replaces.
- [x] Upload/submission failures show a real, recoverable error state — never a silent failure.
  (Camera-permission-denied doesn't apply here — see the file-picker note above; there's no
  separate JS-catchable permission path to handle since the OS/native chooser handles that
  itself, same reasoning as OB-02's photo picker.)
- [x] Component tests cover: the file-picker fallback path (no photo required to submit), the
  successful upload+submit happy path (mocked `GardenApi`, matching this session's established
  spy-based pattern for `GardenSetupComponent`/`AddPlantComponent` rather than raw `HttpClient`
  mocking — functionally equivalent, since `GardenApi` is itself a thin `HttpClient` wrapper),
  and a failure path (with a recoverable retry proven to work).

**Tasks**
- [x] Reuse OB-02's `PhotoPickerComponent` (see AC deviation note — no new camera UI built).
- [x] Implement the real `GardenApi.requestMediaUpload`/`uploadMedia`/`createGoal` calls.
- [x] Replace the capture screen's simulated analyzing/found timeline with a real pending state
  (and delete the now-dead mock code it depended on).
- [x] Component tests for the new flows.

**Dependencies:** WS-01, WS-03. **Carried forward:** live push of the orchestrator's actual
result (WebSocket, ADR-0004) — this story proves submission + a pending state only; a full
result view (polling or push) is a separate future story.
**Status:** ✅ done.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint +
tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev**
via the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**, and is
**human-reviewed**.

### Carried forward (not in this epic)

Full garden/plant CRUD; plan proposal & HITL approval; real domain specialist agents beyond the
`hello` stand-in; the tracker/outcome loop (EventBridge-scheduled follow-ups); the WebSocket push
channel and live result rendering in the frontend; auth (Cognito seam, ADR-0004); prod deploy
gating for any new stack introduced here.
