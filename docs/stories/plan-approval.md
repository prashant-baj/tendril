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
- [ ] New DynamoDB entities on `AppTable`, matching `data-architecture.md` §2 exactly: `Plan`
  (`pk=GARDEN#{garden_id}`, `sk=PLAN#{plan_id}`: `goal_id`, `success_criteria`, `status`) and
  `Task` (`pk=GARDEN#{garden_id}`, `sk=TASK#{task_id}`: `plan_id`, `title`, `detail`, `scope`
  (`plant`\|`garden`), `plant_id?`, `due_date?`, `status`). `due_date` is a proposed date only —
  no scheduler reads it yet (`TasksDueIndex` and its GSI attributes are Phase 7, out of scope
  here).
- [ ] The orchestrator's system prompt/tool-use is changed so its proposal is **structured**, not
  prose: at minimum a `success_criteria` string and 1+ tasks, each with a short `title`, a
  one-sentence `detail`, and a `scope`. This is the riskiest part of this story — it requires the
  model to reliably return parseable structure (a tool call or a constrained JSON response), not
  just fluent text; if the model's output doesn't parse, fall back to writing a single task titled
  from the raw text rather than failing the whole turn (same fail-open posture as PA-01's
  neighboring stories in this codebase, e.g. AF-05's memory fail-open).
  On a successful proposal: writes one `Plan` (`status=PlanProposed`) + its `Task`s, and updates
  `Goal.status=PlanProposed` (replacing today's free-text write). Still records the model's own
  prose as `Plan.rationale` (renamed from `orchestrator_result`) so the "why" isn't lost.
- [ ] `GET /gardens/{gardenId}/goals` — new operation, returns `Goal[]` (list, for Home's "Goals
  in progress" and any future goal list).
- [ ] `GET /gardens/{gardenId}/goals/{goalId}` — new operation, returns a `GoalDetail`: `{goal,
  plan?, tasks: Task[], media: [{mediaId, downloadUrl}]}` (`media`/`downloadUrl` land fully in
  PA-03; this story only needs the shape to exist so PA-03 doesn't have to re-version the
  response). `plan`/`tasks` are absent/empty until the orchestrator has actually proposed
  something (`Goal.status` still `Intake`/`Decomposing`).
- [ ] `openapi.yaml`: `Plan`, `Task` schemas; both new operations + CORS preflight; spec-lint
  stays green.
- [ ] `frontend`: `GoalApi`/`HttpGardenApi`… **`GoalApi`'s `MockGoalApi`** is replaced by a real
  `HttpGoalApi` (mirrors the `HttpGardenApi`/`MockGardenApi` split already established for
  Garden/Plant) backed by the two new operations; `GoalDetailComponent` renders the real `Plan`
  (success criteria, task cards) instead of `MockGoalApi`'s deleted fixture; Home's goal-card grid
  reads the real list (still shows nothing "in progress" until PA-02 ships approval — that's
  expected, not a bug in this story).
- [ ] Unit tests: structured-output parsing (success case + the prose-fallback case), the two new
  handlers (contract-tested against `openapi.yaml` like every other operation this session), CDK
  assertion test for no new IAM beyond what the orchestrator/Client API already hold (`AppTable`
  read/write — no new grant needed, same table).
- [ ] Component tests: Goal Detail renders a real `Plan`/`Task[]`; Home renders the real (now
  possibly non-empty) goal list.
- [ ] Manual smoke test against `dev` recorded in this story before flipping to ✅ (matching
  WS-04/AF's convention): submit a real issue, confirm a `Plan` + `Task`s actually land in
  DynamoDB and render in Goal Detail.

**Tasks**
- [ ] Add `Plan`/`Task` to `data-architecture.md` §2 as *implemented* (they're currently
  documented as designed-not-built) once this ships.
- [ ] Change the orchestrator's prompt + parsing to produce structured output; write `Plan` +
  `Task` records; rename `orchestrator_result` → `Plan.rationale`.
- [ ] `openapi.yaml` + `garden_handler.py`: `listGoals`, `getGoalDetail`.
- [ ] Frontend: `HttpGoalApi`, wire `GoalDetailComponent`/Home to it, delete the now-fully-dead
  parts of `MockGoalApi` (keep the class only as the offline/no-garden fallback, same pattern
  `MockGardenApi` already follows).
- [ ] Unit + component tests; manual dev smoke test.

**Dependencies:** WS-04 (orchestrator loop already exists — this restructures its output, doesn't
replace the loop). **Status:** ☐ to do.

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
- [ ] New entity: chat `Message` (`pk=GARDEN#{garden_id}`, `sk=GOALMSG#{goal_id}#{iso_timestamp}#{message_id}`:
  `goal_id`, `role` (`user`\|`assistant`), `content`) — added to `data-architecture.md` §2.
- [ ] `POST /gardens/{gardenId}/goals/{goalId}/messages` — body `{content: string}` → **202
  Accepted** (async, same posture as `createGoal`). Persists the user's `Message`, publishes a new
  event `goal.message.received` (`garden_id`, `goal_id`) — added to `data-architecture.md` §6.4's
  event table alongside the already-named `plan.approval.responded`.
- [ ] `POST /gardens/{gardenId}/plans/{planId}/approve` — no body needed → **202 Accepted**.
  Publishes `plan.approval.responded` (`garden_id`, `goal_id`, `plan_id`, `decision=approve`).
  Deliberately a **separate, deterministic** operation from the chat endpoint (not "detect the
  word 'approve' in a chat message") — approval is a real state transition and shouldn't depend on
  the model correctly interpreting free text, matching this repo's existing "deterministic floor"
  posture (PG-05).
- [ ] Orchestrator handles `goal.message.received`: loads the `Goal` + its full `Message` history +
  current `Plan`/`Task`s, runs one fresh turn with all of that as input, and either (a) writes a
  revised `Plan`/`Task` set (same write path PA-01 built) plus an assistant `Message` explaining
  what changed, or (b) writes only an assistant `Message` asking the user something (no `Plan`
  mutation that turn). `Goal.status` stays `PlanProposed` either way — no new lifecycle state is
  invented; `architecture.md` §7.3's existing diagram is unchanged.
- [ ] Orchestrator handles `plan.approval.responded` (`decision=approve`) **without invoking the
  model at all** — a pure, cheap DynamoDB write: `Plan.status=Approved`. (`Approved → InProgress`
  per §7.3's diagram is the scheduler's job, Phase 7+ — out of scope here; a goal sitting at
  `Approved` already reads as "in progress" from the user's side, see the frontend AC below.)
- [ ] `GoalDetail` (PA-01's response shape) gains a `messages: [{role, content, createdAt}]` array.
- [ ] Frontend: Goal Detail renders the message thread + a reply box (posts to the messages
  endpoint) + an explicit **Approve** button, enabled once a `Plan` exists, calling the approve
  endpoint. Both actions poll/refetch `GoalDetail` afterward to pick up the orchestrator's
  (asynchronous) response — no WebSocket, matching this epic's stated scope.
- [ ] Home's "Goals in progress" section now shows goals whose `Plan.status` is `Approved` (or
  later). Goals still at `PlanProposed` (proposed, not yet approved) show a real **"Waiting on
  you"** indicator linking to Goal Detail — this is the same banner concept deleted as fully
  fictitious/hardcoded during the earlier UI-data-cleanup pass; reviving it here is intentional,
  now backed by a real status instead of a static string.
- [ ] Unit tests: both new handlers (contract-tested), the orchestrator's message-turn branch
  (revise vs. ask-a-question, both mocked-model paths) and its approve branch (no model call
  made — assert the mock `Agent`/`BedrockModel` is never constructed for a pure approve).
- [ ] Component tests: message thread renders in order; Approve button disabled with no `Plan`;
  Home shows the real "Waiting on you" state vs. the "in progress" state correctly.
- [ ] Manual smoke test against `dev`: propose a plan, send a revision request, confirm the model
  responds and/or updates the plan, then approve — confirm it shows under "Goals in progress."

**Tasks**
- [ ] `data-architecture.md`: add the `Message` entity (§2) and `goal.message.received` event
  (§6.4).
- [ ] `openapi.yaml` + `garden_handler.py`: `postGoalMessage`, `approvePlan`.
- [ ] Orchestrator: split `handle_goal_submitted`'s logic so the message-turn and approve paths
  share the existing DynamoDB read/write helpers; new EventBridge rules for the two events.
- [ ] Frontend: chat UI + Approve button in `GoalDetailComponent`; real "Waiting on you" banner on
  Home, replacing nothing (the fictitious one was already deleted, not repurposed).
- [ ] Unit + component tests; manual dev smoke test.

**Dependencies:** PA-01 (`Plan`/`Task` entities, `GoalDetail` read). **Status:** ☐ to do.

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
- [ ] `GoalDetail` (PA-01) — `media: [{mediaId, downloadUrl}]` is populated for real: one
  freshly-generated, short-lived presigned **GET** URL per `Media` record referenced by the
  goal's `mediaIds`. Generated per-request (never stored/reused — presigned URLs expire), same
  posture OB-03 already specified for plant photos.
- [ ] A goal with no attached media returns an empty `media` array — never an error.
- [ ] Frontend: Goal Detail renders each photo (`<img>`) above/alongside the diagnosis and plan;
  an image load error falls back to a plain placeholder (not a broken-image glyph) — same
  fallback behavior OB-03 specifies, implemented once and reused.
- [ ] Unit tests: presigned GET generation (reuse whatever helper OB-03 introduces first, or
  extract one now if PA-03 ships first — see Dependencies) with and without attached media.
- [ ] Component tests: photo renders when present; fallback renders on load error; nothing renders
  (no broken layout) when a goal has no media.

**Tasks**
- [ ] Extract (or add, if built first) a shared `generate_download_url(s3_key)` helper in
  `garden_handler.py` so OB-03 and this story call the same code, not two copies.
- [ ] Wire it into `getGoalDetail` (PA-01)'s response.
- [ ] Frontend: photo display + fallback in `GoalDetailComponent`.
- [ ] Unit + component tests.

**Dependencies:** PA-01 (`GoalDetail`'s `media` field shape). **Not** dependent on PA-02 — can
ship before or in parallel with it. **Loosely coupled to OB-03** — whichever of the two is
implemented first should extract the shared presigned-GET helper; the second reuses it rather
than duplicating it. **Status:** ☐ to do.

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
