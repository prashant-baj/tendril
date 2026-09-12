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
- [ ] `app/api/openapi.yaml` (OpenAPI 3.0.x — API Gateway's `SpecRestApi` import rejects 3.1,
  ADR-0011's 2026-09-12 refinement) exists with `info`/`servers` populated per environment.
- [ ] `POST /gardens/{gardenId}/media` — request `{contentType, fileName}` → response
  `{uploadUrl, mediaId}` (a presigned S3 **PUT** URL, per ADR-0004's direct-to-S3 upload flow).
- [ ] `POST /gardens/{gardenId}/goals` — request `{description, mediaIds?: string[]}` (the
  issue/prompt text plus optional photo references) → response `{goalId, status}`, **202
  Accepted** (orchestration is async — ADR-0004/ADR-0012, not a synchronous result).
- [ ] `components.schemas` defines `Media` and `Goal`, matching the GOAL entity in
  `architecture.md` §2 (`goal_id`, `description`, `type`, `status`) plus `mediaIds`.
- [ ] Each operation carries an `x-amazon-apigateway-integration` block (`type: aws_proxy`)
  with the Lambda URI left as a placeholder token for infra (WS-02) to patch at synth time.
- [ ] A spec-lint step (e.g. Spectral, or at minimum schema validity) is wired into `ci.yml`'s
  `quality` job.
- [ ] No account IDs/ARNs in the file — placeholder tokens only.

**Tasks**
- [ ] Author `app/api/openapi.yaml` scoped to just these two operations (the full ADR-0004
  endpoint table is future work, not this epic).
- [ ] Add `components.schemas` for `Media`/`Goal`.
- [ ] Wire spec linting into `ci.yml`.

**Dependencies:** none — this is the design-first artifact everything else builds against.
**Status:** ☐ to do.

---

## WS-02 — Infra: Client API, EventBridge trigger, and the declarative agent registry

**As a** developer, **I want** CDK stacks that provision the Client API by importing the OpenAPI
contract, an EventBridge rule linking ingestion to the orchestrator, and a declarative agent
registry (migrating `hello_agent` into it) that both `AgentCoreStack` and the orchestrator read,
**so that** the photo→goal→orchestrator pipe has real, GitOps-deployed infrastructure to run on
(ADR-0011, ADR-0012).

**Acceptance Criteria**
- [ ] A new `ClientApiStack` loads `app/api/openapi.yaml`, patches each operation's
  `x-amazon-apigateway-integration.uri` with the real Lambda ARN, and constructs
  `apigateway.SpecRestApi` — `cdk synth` produces an `AWS::ApiGateway::RestApi` with both
  operations wired.
- [ ] An EventBridge rule routes a `goal.submitted` event from the Client API Lambda to the
  orchestrator Lambda, asynchronously — no synchronous coupling between them.
- [ ] `agents/registry/hello.json` exists (per ADR-0012's format); `AgentCoreStack` is
  refactored to **loop over `agents/registry/*.json`** instead of the single hardcoded
  `_agent_runtime()` call — `hello_agent` is migrated, not duplicated.
- [ ] The orchestrator Lambda's execution role grants `bedrock-agentcore:InvokeAgentRuntime`
  scoped to the registered runtime ARN(s) only — no wildcards.
- [ ] The orchestrator Lambda's environment carries the deploy-time invocation manifest (agent
  name → runtime ARN + description) built from the same registry.
- [ ] All new/changed resources are env-prefixed; `cdk synth` and a `dev` deploy succeed; no
  hardcoded account IDs/secrets.
- [ ] CDK assertion tests cover: one runtime provisioned per registry file, the orchestrator
  role's `InvokeAgentRuntime` resource is scoped (not `*`), and `SpecRestApi` synthesizes with
  both operations present.

**Tasks**
- [ ] Implement the OpenAPI YAML-patch + `SpecRestApi` construction in `ClientApiStack`.
- [ ] Add the EventBridge rule wiring ingestion → orchestrator.
- [ ] Refactor `AgentCoreStack` to the registry loop; author `agents/registry/hello.json`.
- [ ] Scope the orchestrator's execution role IAM to registered runtime ARNs.
- [ ] Add CDK assertion tests for all of the above.

**Dependencies:** WS-01 (the contract to import); ADR-0005 (existing AgentCore pattern).
**Status:** ☐ to do.

---

## WS-03 — Application (Lambda): Client API handlers — photo upload + goal intake

**As a** developer, **I want** the Client API Lambda(s) implementing both OpenAPI operations —
issuing a presigned S3 upload URL, and persisting a submitted goal with its event hand-off —
**so that** a user's photo and issue description are actually stored and trigger the
orchestrator, matching ADR-0004's async design.

**Acceptance Criteria**
- [ ] `POST /gardens/{gardenId}/media` returns a presigned **PUT** URL scoped to the existing
  media bucket (`FoundationStack`) plus a generated `mediaId`; the Lambda never proxies file
  bytes.
- [ ] `POST /gardens/{gardenId}/goals` validates the payload (non-empty `description`; any
  `mediaIds` are well-formed), persists a `GOAL` record to the `AppTable` (status `Intake`, per
  `architecture.md` §7.3), and **publishes the `goal.submitted` event before returning** — the
  202 response reflects real persisted state, not an in-memory acknowledgment.
- [ ] Handler responses conform exactly to the OpenAPI response schemas (WS-01), checked by a
  contract test (request/response fixtures validated against the spec's JSON Schema).
- [ ] Malformed/missing input is rejected with a typed 4xx error — never an unhandled exception
  (same deterministic-floor posture as PG-05).
- [ ] Unit tests cover: a valid upload request, a valid goal submission (with and without
  `mediaIds`), and at least one validation-failure case per handler.
- [ ] No hardcoded bucket names/ARNs/account IDs — read from CDK-injected env vars.

**Tasks**
- [ ] Implement the media presigned-URL handler.
- [ ] Implement the goal-intake handler (validate → persist → publish event → respond).
- [ ] Add unit tests + a contract test against `openapi.yaml`.

**Dependencies:** WS-01, WS-02. **Status:** ☐ to do.

---

## WS-04 — Application (Lambda): Orchestrator — Strands agent loop triggered by the event

**As a** developer, **I want** an orchestrator Lambda that wakes on the `goal.submitted` event,
runs a Strands agent loop, and calls the registered `hello` specialist via
`InvokeAgentRuntime`, **so that** the full pipe — photo/issue in, orchestrator triggered, a
specialist actually invoked — is proven end-to-end before real domain specialists exist.

**Acceptance Criteria**
- [ ] The orchestrator Lambda is invoked **asynchronously** by the EventBridge rule (WS-02/WS-03)
  — it is not reachable from the Client API's synchronous request path.
- [ ] On invocation it loads the goal (and any referenced media) from DynamoDB, builds a Strands
  agent with the registered specialist(s) from its deploy-time invocation manifest wrapped as
  agents-as-tools, and runs at least one turn that calls the `hello` specialist via
  `InvokeAgentRuntime`.
- [ ] The specialist's response is persisted back onto the goal's DynamoDB record (a
  tracking/result field), so a future status endpoint has something real to read.
- [ ] Failures (specialist unreachable, guardrail block, malformed response) are caught and
  recorded on the goal record as a failed/needs-retry state — never an unhandled Lambda crash
  with no trace.
- [ ] The turn is traced (which specialist(s) called, latency, outcome) via structured logging
  and OTel, per ADR-0001's observability NFR.
- [ ] Unit tests cover the agent-loop wiring with a **mocked** `InvokeAgentRuntime` call (no live
  AgentCore call in CI); one live smoke-test invoke against `dev` is run manually and its result
  recorded in the PR.

**Tasks**
- [ ] Scaffold `app/orchestrator/` (Strands agent; agents-as-tools built from the registry
  manifest; the `InvokeAgentRuntime` tool implementation).
- [ ] Wire the DynamoDB read (goal + media) and write-back (result/failure state).
- [ ] Add OTel/structured logging for the turn.
- [ ] Unit tests with a mocked AgentCore call; document the manual dev smoke test.

**Dependencies:** WS-02, WS-03. **Status:** ☐ to do.

---

## WS-05 — Frontend: real photo capture (camera + local file) and goal submission

**As a** gardener using the app, **I want** to take a photo with my device camera or pick one
from local storage, describe my issue in plain language, and submit it, **so that** the Capture
screen becomes a real entry point into the pipeline instead of a scripted mock — closing the
seam the frontend's `Mock*Api` layer was deliberately built to be swapped at.

**Acceptance Criteria**
- [ ] The Capture screen (`frontend/src/app/features/capture/`) offers **both** live camera
  capture (`getUserMedia` + a captured-frame preview) **and** a local file picker
  (`<input type="file" accept="image/*">`), falling back gracefully to the file picker when
  camera permission is denied or unavailable.
- [ ] Confirming a photo requests a presigned upload URL from the real Client API
  (`POST /gardens/{id}/media`, WS-01/WS-03) and uploads **directly to S3** — never through the
  app's own backend.
- [ ] The user enters their issue/goal as free text; submitting calls the real
  `POST /gardens/{id}/goals` (referencing the uploaded `mediaId`), replacing `MockGoalApi`'s
  in-memory fixture for this flow.
- [ ] Request/response types are generated from `app/api/openapi.yaml` (WS-01) — no
  hand-maintained duplicate interfaces for these two calls.
- [ ] After submission, the UI shows a clear "submitted — your garden agent is on it" pending
  state; the simulated analyzing/found timeline in `CaptureService` is repurposed or removed —
  it no longer fakes a specialist reveal once wired to something real.
- [ ] Upload/submission failures (network, camera permission denied, S3 upload error) show a
  real, recoverable error state — never a silent failure.
- [ ] Component tests cover: the file-picker fallback path, the successful upload+submit happy
  path (mocked `HttpClient`), and at least one failure path.

**Tasks**
- [ ] Add camera-capture UI (`getUserMedia`) with file-picker fallback.
- [ ] Implement the real `Http*Api` presigned-upload + goal-submission calls, using types
  generated from the OpenAPI spec.
- [ ] Replace the capture screen's simulated analyzing/found timeline with a real pending state.
- [ ] Component tests for the new flows.

**Dependencies:** WS-01, WS-03. **Carried forward:** live push of the orchestrator's actual
result (WebSocket, ADR-0004) — this story proves submission + a pending state only; a full
result view (polling or push) is a separate future story.
**Status:** ☐ to do.

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
