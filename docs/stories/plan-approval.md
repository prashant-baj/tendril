# Stories — Epic: Plan Proposal, Conversational Approval & Photo Review

Everything up through the Walking Skeleton epic proves the **pipe**: a photo/issue goes in, the
orchestrator wakes, calls a specialist, and writes one free-text paragraph back onto the `Goal`
record (`orchestrator_result`). Nothing downstream of that exists yet — there's no structured
`Plan`/`Task` the user can actually review, no way to approve one, and no way to see the photo
that was diagnosed. This epic closes that gap: `docs/roadmap.md` Phases **4.5**, **4.6**, and
**5**.

This is the first epic where the orchestrator's output becomes something the user *acts on*
(reviews, discusses, approves) rather than just reads — and the first time a user reply has to
route back into a **second** orchestrator invocation for the *same* goal. Each story is scoped to
keep that new surface area small and demoable on its own:

- **PA-01** — the orchestrator proposes a real, structured `Plan` + `Task` list (not a paragraph),
  and the frontend reads it for real (closes roadmap Phase 4.5).
- **PA-02** — the user can discuss a proposed plan with Tendril (ask questions, request changes)
  and the model can ask *them* for missing information, before an explicit, deterministic Approve
  action (closes roadmap Phase 5 — the "Goal Approval" ask).
- **PA-03** — the photo(s) attached to a goal are actually viewable in Goal Detail, not just
  referenced by an opaque `mediaId` (closes roadmap Phase 4.6 — the "show uploaded images" ask;
  also the shared mechanism [`OB-03`](./garden-onboarding.md#ob-03--show-the-real-plant-photo-not-a-generic-icon)
  reuses for plant thumbnails).

**Explicitly out of scope for this epic** (carried forward): the tracker/scheduler Lambda and
`TasksDueIndex`-driven follow-up automation (Task due dates are proposed by the model but nothing
*schedules* a reminder yet — that's `docs/roadmap.md` Phase 7+); the WebSocket push channel
(ADR-0004) — this epic's chat/approval UI **polls**, matching the precedent WS-05 already set for
goal submission, not a live push; using photos for *progress comparison over time* (the
"tracker/outcome loop" — Task/Tracking check-ins don't exist yet, so there's nothing to compare
against). PA-03 makes the *display* mechanism real now so that future work has something to plug
into; it doesn't build the comparison itself.

**Story format:** `As a <role>, I want <capability>, so that <benefit>` + Acceptance Criteria +
Tasks + Dependencies + Status.

**Suggested order:** PA-01 → PA-03 → PA-02 (PA-03 only needs PA-01's read endpoint and can be
built in parallel with/before the harder conversational-approval work; PA-02 is the largest and
riskiest piece — see its Context note on the session-continuity decision it makes).

**Update (2026-09-13): all three stories were built and deployed together**, not sequentially —
per direct instruction, since they form one coherent vertical slice (propose → discuss →
approve) rather than three independently-demoable increments. A few real design decisions were
made during implementation, beyond what was originally scoped here — each noted inline below:
structured output is extracted via Strands' real (verified against the installed 1.55.1 source)
`agent(structured_output_model=...)` mechanism, applied only at the **orchestrator's** own final
synthesis step — the 5 specialist agents needed zero changes; and "initial proposal" and "chat
message" turned out to be the *same* code path (`_run_turn`), not two separate mechanisms.

**Status legend:** ✅ done · ◐ partially done · ☐ to do.

---

## PA-01 — The orchestrator proposes a structured Plan (not a paragraph)

**As a** gardener who just reported a plant issue, **I want** Tendril to propose a concrete plan —
a short list of specific tasks, not just a paragraph of diagnosis — and **see** that plan on the
Goal Detail screen, **so that** I understand exactly what's being suggested and can decide whether
it's right before anything happens.

**Context:** `data-architecture.md` §2 already specifies `Plan` (1:1 with its `Goal`,
`success_criteria`, `status`) and `Task` (`plan_id`, `scope`, `plant_id?`, `status`, `due_date`)
entities — neither exists in DynamoDB yet. Today, [`orchestrator.py`](../../app/orchestrator/orchestrator.py#L246-L252)
only ever writes one free-text field (`orchestrator_result`) and flips `Goal.status` straight to
`PlanProposed` — the status name is currently aspirational, not backed by a real `Plan`. There is
also no read endpoint at all for goals beyond `createGoal`'s 202 response — `GoalDetailComponent`
still renders `MockGoalApi`'s fixture, and Home's "Goals in progress" list is empty since that
mock was blanked out (rather than deleted) when fictitious data was removed from the frontend.

**Acceptance Criteria**
- [x] New DynamoDB entities on `AppTable`: `Plan` (`pk=GARDEN#{garden_id}`, `sk=PLAN#{goal_id}`:
  `plan_id`(=`goal_id`), `goal_id`, `success_criteria`, `status`) and `Task` (`pk=GARDEN#{garden_id}`,
  `sk=TASK#{goal_id}#{task_id}`: `task_id`, `plan_id`, `goal_id`, `title`, `detail`, `scope`
  (`plant`\|`garden`), `status`). **Two deviations from this AC's original wording**, both
  documented in `data-architecture.md` §2: `plan_id` reuses `goal_id` directly (always 1:1, no
  reason for a separate id), and `Task`'s `sk` is goal-scoped (`TASK#{goal_id}#{task_id}`, not the
  flat `TASK#{task_id}`) so a plan revision can cheaply replace just this goal's tasks with one
  prefix `Query` — no `due_date`/`plant_id` fields yet, since neither the scheduler nor per-plant
  scoping exist to consume them.
- [x] The orchestrator's synthesis is **structured**, not prose — but via a different, verified
  mechanism than originally scoped: `agent(structured_output_model=ChatTurnResult)`, Strands'
  real (non-deprecated) structured-output API, called on the *same* agent instance right after its
  normal tool-calling turn, with no new prompt (reuses conversation history — confirmed in the
  installed `strands-agents==1.55.1` source, not assumed). `ChatTurnResult = {reply: str,
  updated_plan: PlanProposal | None}` — **this one schema also covers PA-02's "ask a clarifying
  question" behavior**, since `updated_plan` can legitimately be absent on success. Fail-open
  exactly as scoped: a structuring *failure* (not a legitimate "no plan yet") wraps the raw reply
  text into one fallback `Task`, but only when no `Plan` exists yet for this goal — see PA-02's
  `_run_turn` for why a transient failure must never clobber an *existing* approved-or-proposed
  plan.
- [x] On a successful proposal: writes one `Plan` (`status=PlanProposed`) + its `Task`s, updates
  `Goal.status=PlanProposed`. **Deviation:** the model's rationale/reply isn't a separate
  `Plan.rationale` field (renamed from `orchestrator_result`) as originally scoped — it's written
  as the first assistant `Message` in the goal's chat thread (PA-02) instead, which turned out to
  be a better fit: one place for "everything Tendril has said about this goal," not two.
- [x] `GET /gardens/{gardenId}/goals` — `listGoals`.
- [x] `GET /gardens/{gardenId}/goals/{goalId}` — `getGoalDetail`, returning the full `GoalDetail`
  shape (`goal`, `plan?`, `tasks`, `media`, `messages` — PA-02/PA-03's fields were added to this
  same response from the start rather than versioned in later, since all three stories shipped
  together).
- [x] `openapi.yaml`: `Plan`, `Task`, `Message`, `GoalMedia`, `GoalDetail` schemas; all new
  operations (this story's + PA-02/PA-03's) + CORS preflight; spec-lint stays green.
- [x] `frontend`: `GoalApi`'s `MockGoalApi` is now only the pre-onboarding fallback; `HttpGoalApi`
  (mirrors `HttpGardenApi`/`MockGardenApi`) backs the real reads. `GoalDetailComponent` renders the
  real `Plan`/`Task[]`. Home's goal grid reads the real list, split into "Goals in progress"
  (`Approved`+) and a new real "Needs your attention" section (see PA-02) — not empty-until-PA-02
  as originally expected, since PA-02 shipped in the same pass.
- [x] Unit tests: `_run_turn`'s structured-success and fail-open-fallback branches, the new
  handlers (contract-tested against `openapi.yaml`'s `GoalDetail` schema via a `RefResolver`,
  since unlike every prior contract test this schema `$ref`s others). No new IAM was needed —
  confirmed, not just assumed (same `AppTable` grant already covers Plan/Task/Message).
- [x] Component tests: Goal Detail renders a real `Plan`/`Task[]`; Home renders the real goal
  list, split correctly.
- [x] Manual smoke test against `dev`, recorded: submitted a real issue against the existing
  curry-leaf plant with a photo attached — `vision` identified it, its identification was chained
  into `irrigation`/`agronomy`, and a real `Plan` + `Task` landed in DynamoDB and rendered
  correctly in Goal Detail, correctly matching the actual plant (no cross-contamination).

**Real bug found and fixed during the smoke test (belongs to PA-03, caught here):** the first
presigned `downloadUrl` generated actually 403'd when fetched — `generate_presigned_url()`
succeeds regardless of IAM (it only signs a request), so the gap only surfaces the moment
something really tries to fetch the URL. `client_api_stack.py` only ever granted the garden
handler's role `media_bucket.grant_put` (for upload URLs, OB-02) — never `grant_read`. Its own
adjacent comment already warned about exactly this class of bug for PUT; the same lesson wasn't
carried over to GET when PA-03 added it. Fixed with `media_bucket.grant_read(garden_handler)`;
re-verified live afterward (`curl` on a freshly-generated URL: HTTP 200, photo bytes returned).

**Tasks**
- [x] `data-architecture.md` §2 updated to mark `Plan`/`Task` implemented, including both key
  deviations above.
- [x] Orchestrator: `_run_turn`/`ChatTurnResult`/`PlanProposal`/`TaskProposal`; `_write_plan_and_tasks`
  writes `Plan`+`Task`s.
- [x] `openapi.yaml` + `garden_handler.py`: `listGoals`, `getGoalDetail`.
- [x] Frontend: `HttpGoalApi`; `GoalDetailComponent`/Home wired to it; the old mockup-only
  `PlanTask`/`FollowUp`/`SpecialistTraceEntry` models and their components (`trace-entry`,
  `follow-up-row`) deleted, not repurposed — `plan-task-card` simplified to the real `Task` shape.
- [x] Unit + component tests; manual dev smoke test recorded above.

**Dependencies:** WS-04 (orchestrator loop already existed — this restructures its output, doesn't
replace the loop). **Status:** ✅ done — deployed to `dev` and smoke-tested live.

---

## PA-02 — Conversational plan approval (chat, clarifying questions, explicit Approve)

**As a** gardener reviewing a proposed plan, **I want** to talk it over with Tendril — ask why,
request a change, or answer a question it has for me — and only then approve it, **so that** the
plan I commit to actually fits my situation, instead of a one-shot take-it-or-leave-it proposal.

**Context — a real design decision, not a given:** `data-architecture.md` §3.1/§7 already
specifies a `SnapshotSessionManager` + a new `tendril-{env}-agent-state` S3 bucket so the
orchestrator can pause on a `HumanInTheLoop` interrupt and resume the *same* live agent session
days later. That bucket doesn't exist yet (`data-architecture.md` §9, item 1) and building it is
real, standalone infrastructure work. **This story deliberately does not build it.** Instead, each
chat turn is a **fresh, stateless orchestrator invocation** (the same pattern WS-04 already uses
for the very first turn) that reconstructs context by reading the goal's persisted message
history + current `Plan`/`Task`s back out of DynamoDB — no live Strands session survives between
messages. This is the same class of scope call other stories in this codebase have made and
documented rather than silently diverged on (e.g. WS-05 choosing a file-picker over
`getUserMedia`, AF-05 deferring `ContextInjector` pinning): **it trades a slightly larger prompt
per turn for not having to stand up and IAM-scope a new S3 bucket + session-resume mechanism just
to ship a chat box.** True session-resume (needed for the multi-day tracker/follow-up loop, where
reconstructing full context from scratch every time gets expensive) stays exactly as
`data-architecture.md` designed it, deferred to Phase 7+.

**Acceptance Criteria**
- [x] New entity: chat `Message` (`pk=GARDEN#{garden_id}`, `sk=GOALMSG#{goal_id}#{iso_timestamp}#{message_id}`:
  `message_id`, `goal_id`, `role` (`user`\|`assistant`), `content`, `created_at`) — added to
  `data-architecture.md` §2.
- [x] `POST /gardens/{gardenId}/goals/{goalId}/messages` — body `{content: string}` → **202
  Accepted**. Persists the user's `Message`, publishes `goal.message.received` (`garden_id`,
  `goal_id` only — no `content`, matching `goal.submitted`'s existing minimal-detail convention;
  the orchestrator reloads the full message history, which already includes it) — added to
  `data-architecture.md` §6.4.
- [x] `POST /gardens/{gardenId}/plans/{planId}/approve` — **deviation from this AC's original
  wording**: instead of publishing `plan.approval.responded` for the orchestrator to handle, this
  is a fully **synchronous** Client API operation (`garden_handler.py::approve_plan`) — a direct
  `update_item` flipping `Plan.status`/`Goal.status` to `Approved`, no event, no Lambda-to-Lambda
  hop at all. Approving genuinely needs no model reasoning, so the event-based design this AC
  originally called for was more machinery than the problem needed; `plan.approval.responded` is
  removed from `data-architecture.md` §6.4 accordingly. The *intent* this AC cared about —
  approval never depends on the model correctly interpreting free text — still holds exactly:
  Approve is a dedicated button/action, never inferred from a chat message.
- [x] Orchestrator handles `goal.message.received` (`handle_goal_message_received`): loads the
  `Goal` + `Plan`/`Task`s + full `Message` history, formats it into one transcript
  (`_format_transcript`), and runs the *same* `_run_turn` PA-01 built — either (a) writes a
  revised `Plan`/`Task` set + an assistant `Message`, or (b) writes only an assistant `Message`
  asking a question. `Goal.status` stays `PlanProposed` either way, matching architecture.md
  §7.3's existing diagram unchanged, exactly as scoped.
- [x] `GoalDetail` gains `messages: [{role, content, createdAt}]` (built into PA-01's response
  from the start, since all three stories shipped together — see the epic-level update note).
- [x] Frontend: Goal Detail renders the message thread + a reactive-form reply box (matching this
  codebase's established `ReactiveFormsModule`/`FormBuilder` convention, not a new pattern) + an
  explicit **Approve** button, enabled only once a `Plan` exists and isn't already `Approved`.
  Both actions refetch `GoalDetail` via a `refresh$` trigger afterward (the same pattern
  `GardenComponent`'s plant-delete already established) — no WebSocket, matching this epic's
  stated scope.
- [x] Home's "Goals in progress" section shows goals whose status is `Approved`/`InProgress`.
  Goals still earlier in the lifecycle (`Intake`/`Decomposing`/`PlanProposed`) surface under a
  real **"Needs your attention"** section — the intentional revival of the "Waiting on you"
  concept deleted as fictitious earlier this session, now backed by real `Goal.status`.
- [x] Unit tests: `handle_goal_message_received`'s revise-plan and ask-a-question branches, the
  new handlers (contract-tested), and `approve_plan`'s handler tested directly — confirming it's
  a pure `update_item` with no `Agent`/`BedrockModel` construction anywhere in its call path
  (there's no orchestrator involvement to mock in the first place, since approval never reaches
  it).
- [x] Component tests: message thread renders; Approve button visibility/disabled state; Home's
  in-progress vs. needs-attention split.
- [x] Manual smoke test against `dev`, recorded: a live goal (irrigation+agronomy consulted)
  produced a real `Plan`; the orchestrator's multi-specialist chaining and structured-output
  extraction both worked correctly with real Bedrock calls, no mocks.

**Tasks**
- [x] `data-architecture.md`: added the `Message` entity (§2) and `goal.message.received` event
  (§6.4); removed `plan.approval.responded` (never implemented — see the AC deviation above) and
  rewrote §7's data-flow diagram to match what's actually built.
- [x] `openapi.yaml` + `garden_handler.py`: `postGoalMessage`, `approvePlan`.
- [x] Orchestrator: `handle_goal_message_received`, `_format_transcript`; new
  `GoalMessageReceivedRule` in `agentcore_stack.py` (mirrors `GoalSubmittedRule` exactly, no new
  IAM needed).
- [x] Frontend: chat UI + Approve button in `GoalDetailComponent`; real "Needs your attention"
  section on Home.
- [x] Unit + component tests; manual dev smoke test recorded above.

**Dependencies:** PA-01 (`Plan`/`Task` entities, `GoalDetail` read). **Status:** ✅ done — deployed
to `dev` and smoke-tested live.

---

## PA-03 — Show the goal's photo(s) in Goal Detail

**As a** gardener reviewing what Tendril diagnosed, **I want** to actually see the photo I
submitted next to its diagnosis, **so that** I can judge for myself whether the plan matches what
I photographed, instead of trusting a text description of my own picture.

**Context:** Exactly the OB-03 problem (`MediaBucket` is private, only presigned **upload** URLs
exist today, no presigned **download** path), but for goal-attached media instead of plant media.
Rather than solving this twice, this story builds the one reusable server-side helper (presigned
GET generation) and frontend pattern (`<img>` with a generic-icon fallback on load error) that
both this story and OB-03 use — OB-03's remaining scope (once this ships) shrinks to just calling
the same helper for `Plant`'s linked `Media` instead of `Goal`'s.

**Acceptance Criteria**
- [x] `GoalDetail` — `media: [{mediaId, downloadUrl}]` populated for real: one freshly-generated,
  short-lived presigned **GET** URL per `Media` record referenced by the goal's `mediaIds`,
  generated per-request (never stored/reused), same posture OB-03 already specified for plant
  photos.
- [x] A goal with no attached media returns an empty `media` array — never an error.
- [x] Frontend: Goal Detail renders each photo (`<img>`) above the diagnosis/plan; an image load
  error (`(error)` handler) hides the broken image rather than showing a broken-image glyph.
- [x] Unit tests: `_generate_download_url` exercised via `get_goal_detail`'s tests, with and
  without attached media.
- [x] Component tests: photo renders when present (asserted via the rendered `<img src>`).

**Tasks**
- [x] Added `_generate_download_url(s3_key)` in `garden_handler.py` — built here (PA-01/02/03
  shipped together, so there was no "whichever ships first" race with OB-03 in practice); OB-03
  (still open, backlog rank 34) should call this same helper rather than duplicating it when it's
  picked up.
- [x] Wired into `getGoalDetail`'s response.
- [x] Frontend: photo display + error-fallback in `GoalDetailComponent`.
- [x] Unit + component tests.

**Dependencies:** PA-01 (`GoalDetail`'s `media` field shape). **Not** dependent on PA-02.
**Loosely coupled to OB-03** — this shipped first, so OB-03 (rank 34, still open) is the one that
will reuse `_generate_download_url` when it's picked up. **Status:** ✅ done — deployed to `dev`.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint +
tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev** via
the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs** (this epic
touches `data-architecture.md` §2/§6.4 directly — keep it in sync as each story ships), and is
**human-reviewed**.

### Carried forward (not in this epic)

The tracker/scheduler Lambda and `TasksDueIndex`-driven follow-ups (Phase 7+); true
`SnapshotSessionManager`/`AgentStateBucket` session-resume (deferred by PA-02's Context note —
still the right design for the multi-day follow-up loop when that's built); the WebSocket push
channel; using photos for progress comparison over time (needs the Task/Tracking check-in loop,
which needs the tracker first).
