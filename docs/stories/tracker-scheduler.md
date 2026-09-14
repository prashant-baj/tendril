# Stories — Epic: Tracker/Scheduler & Real Session-Resume (Phase 7.5+)

`docs/roadmap.md` Phase 7.5+ / `docs/backlog.md` rank 20. The first genuinely **not-yet-storied**
epic since this session started — every prior story extended an already-shipped one; this builds
two pieces `ADR-0002` and `PA-02` already designed and explicitly deferred:

1. **The outcome loop's tracker/scheduler.** `Task` had no due-date/schedule concept at all (not
   reserved-but-unused — genuinely absent from both the real DynamoDB item and this doc's own
   Task row until this epic). Nothing proactively followed up with the gardener days after a plan
   was approved.
2. **Real Strands session-resume.** `data-architecture.md` §3.1 already designed
   `SnapshotSessionManager` + a dedicated `tendril-{env}-agent-state` S3 bucket, and `ADR-0002`'s
   action item 1 already accepted it, but `PA-02`'s Context note deliberately deferred building
   it: every orchestrator turn reconstructs the *entire* goal + plan + task list + full message
   history from DynamoDB into a **fresh** `Agent` every invocation. PA-02 named this epic as
   where that finally gets built.

Both pieces are verified against ground truth, not assumed: real `strands-agents==1.55.1` has
exactly the `SnapshotSessionManager`/`S3Storage` API this doc sketched; real `aws-cdk-lib` ships
`aws_scheduler`/`aws_scheduler_targets` as GA (not `_alpha`) L2 constructs; no
`strip_trailing_tool_use()` function exists in the installed SDK (a documented mitigation
*pattern*, hand-rolled here, not an SDK guarantee).

---

## TR-01 — `TasksDueIndex` + due-date stamping at approve/checkin time

**As a** gardener, **I want** my approved plan's tasks to carry a follow-up due-date, **so that**
something can later notice if I never got back to one.

**Context:** `Task`'s key (`GARDEN#{garden_id}` / `TASK#{goal_id}#{task_id}`, PA-01) stays
unchanged — the new sparse GSI (`gsi1pk="TASK_STATUS#pending"`, `gsi1sk=due_date`) is added
*alongside* it, not instead of it (data-architecture.md §2 had left this exact choice open).

**Acceptance Criteria**
- [x] New GSI `TasksDueIndex` on `tendril-{env}-app` (`FoundationStack`).
- [x] `approvePlan` stamps `due_date`/`gsi1pk`/`gsi1sk` on every currently-`pending` task for the
  goal (fixed 3-day offset — no per-task-type duration logic, MVP simplicity). Best-effort: a
  stamping failure never turns a successful approval into a 500.
- [x] Revision-then-reapprove self-corrects with no special-casing: `_write_plan_and_tasks`
  deletes non-`done` tasks and creates fresh ones on any revision, forcing re-approval through the
  same `approvePlan` endpoint — which re-stamps whatever pending tasks exist at that later call,
  since the query is against the current task set, not a cached snapshot.
- [x] `postTaskCheckin` removes `gsi1pk`/`gsi1sk`/`due_date` in the same transaction that flips a
  task to `done` — keeps the GSI sparse; a checked-in task must never still read as "overdue."
- [x] Unit tests: stamps only pending (not done) tasks; stamping failure still returns 200;
  revision-then-reapprove stamps only the current task set; check-in's transaction asserted to
  include the `REMOVE` clause.

**Tasks**
- [x] `infra/stacks/foundation_stack.py`: `add_global_secondary_index("TasksDueIndex", ...)`.
- [x] `app/api/garden_handler.py`: `approve_plan` stamping step; `post_task_checkin`'s
  `UpdateExpression` gains `REMOVE gsi1pk, gsi1sk, due_date`.

**Dependencies:** PA-01 (`Task`/`Plan` entities). **Status:** ✅ done — deployed to `dev` and
live-verified: approving a real 3-task plan stamped `due_date`/`gsi1pk`/`gsi1sk` on all three
pending tasks; checking one in removed all three attributes from that item.

---

## TR-02 — Tracker/Scheduler Lambda

**As the** system, **I want** a scheduled process that finds overdue pending tasks, **so that**
the gardener gets a nudge without anyone manually checking.

**Context:** `app/tracker/` didn't exist at all before this story — a full new self-contained
folder (own `Dockerfile`/`requirements.txt`, `boto3` only, no `strands-agents` — this is a
deterministic query + event-emit, not an agent).

**Acceptance Criteria**
- [x] EventBridge Scheduler (15-minute rate) invokes the tracker Lambda.
- [x] Tracker queries `TasksDueIndex` (`gsi1pk="TASK_STATUS#pending" AND gsi1sk<=now`), paginated
  — a truncated page must never silently drop an overdue task.
- [x] One `followup.due` event published per due task (`gardenId`/`goalId`/`taskId`, parsed off
  the item's own `pk`/`sk` — no extra projected attributes needed), each in its own try/except so
  one bad item never aborts the run.
- [x] Known, accepted MVP gap (not fixed here): the tracker doesn't itself dedupe against
  EventBridge Scheduler's at-least-once semantics — a double-invocation before the orchestrator's
  due-date bump lands could double-fire the same task. Low severity (an extra chat message),
  narrow window at this cadence.
- [x] Unit tests: query shape, one-event-per-task, per-item failure isolation, pagination,
  no-due-tasks-is-a-no-op.

**Tasks**
- [x] `app/tracker/{Dockerfile,.dockerignore,requirements.txt,tracker.py,tests/}`.
- [x] `infra/stacks/agentcore_stack.py`: tracker Lambda, `scheduler.Schedule`, `FollowupDueRule`.

**Found + fixed a real bug live:** `app_table.grant_read_data(self.tracker)` 403'd on
`dynamodb:Query` against `TasksDueIndex` — `app_table` is `Table.from_table_name(...)`, an
imported table with no index metadata, so its `grant_read_data()` only grants base-table actions,
never `.../index/*`. Fixed with an explicit `iam.PolicyStatement` naming the index ARN directly;
added a regression-guarding CDK test (`test_tracker_lambda_and_schedule_provisioned`) asserting a
`dynamodb:Query` grant scoped to `TasksDueIndex` specifically exists.

**Dependencies:** TR-01 (the GSI it queries). **Status:** ✅ done — deployed to `dev` and
live-verified end to end: approved a real plan (3 tasks stamped with due-dates), manually
invoked the tracker (correctly found 0 due tasks, since none were due yet), backdated one task's
`due_date` to simulate it being overdue, re-invoked the tracker (found it, published
`followup.due`), confirmed the orchestrator's `handle_followup_due` wrote a deterministic nudge
message + `task.followup_due` Activity event + bumped the due-date forward (with **no**
`bedrock:InvokeModel` call, confirmed via CloudWatch logs) — then checked the task in with a real
photo and confirmed `gsi1pk`/`gsi1sk`/`due_date` were all removed from the item.

---

## TR-03 — Orchestrator's `followup.due` handler

**As a** gardener, **I want** a proactive nudge in my goal's chat when a task's follow-up is due,
**so that** I don't have to remember to check back in myself.

**Context:** Unlike the other three orchestrator entry points, this is deliberately **not** "just
a turn" — no `Agent`/model call at all. A scheduled nudge is a deterministic action: write a chat
message, log an Activity event, and push the due-date forward so the tracker's next tick doesn't
re-fire the same task before the gardener responds.

**Acceptance Criteria**
- [x] New `followup.due` handler: loads the task directly; if missing or no longer `pending` (a
  real race if the gardener checked in between the tracker's query and this invocation), returns
  early without writing anything or bumping the due-date (avoids resurrecting an already-done task
  into the sparse index).
- [x] Writes a deterministic (non-model) nudge `Message` + a best-effort `task.followup_due`
  Activity `Event`.
- [x] Bumps `due_date`/`gsi1sk` forward by the same offset — this is the one step whose failure
  must be loud in logs (silence here directly causes "refires forever"), but never re-raised (a
  raise would trigger EventBridge's automatic retry, re-running the message/event writes too).
- [x] Unit tests: happy path (and proves no `Agent` construction happened); skip-when-done;
  skip-when-not-found; one write failing doesn't block the due-date bump; `handler()` routing.

**Tasks**
- [x] `app/orchestrator/orchestrator.py`: `_bump_task_due_date`, `handle_followup_due`, routed in
  `handler()`.

**Dependencies:** TR-02 (the event it handles). **Status:** ✅ done.

---

## SR-01 — `AgentStateBucket` provisioning + IAM

**As the** orchestrator, **I need** a session-state bucket only I can reach, **so that**
`SnapshotSessionManager` has somewhere durable to persist across Lambda invocations.

**Acceptance Criteria**
- [x] New `tendril-{env}-agent-state` S3 bucket (`FoundationStack`) — no CORS (nothing outside the
  orchestrator's own Lambda ever touches it, ADR-0013).
- [x] Orchestrator's execution role gets read/write on it; no specialist role gets any grant on it
  (enforced by the existing `test_no_specialist_role_has_dynamodb_or_s3_iam_actions` CDK test).
- [x] `AGENT_STATE_BUCKET_NAME` env var on the orchestrator Lambda.

**Tasks**
- [x] `infra/stacks/foundation_stack.py`, `infra/stacks/agentcore_stack.py`.

**Dependencies:** none. **Status:** ✅ done — deployed to `dev`.

---

## SR-02 — Wire `SnapshotSessionManager` into the orchestrator

**As the** orchestrator, **I want** to resume the same live Strands session across invocations,
**so that** I stop re-reconstructing the entire conversation from DynamoDB on every single turn.

**Context:** Verified against the installed SDK — `SnapshotSessionManager(session_id=goal_id,
storage=S3Storage(bucket=..., prefix="orchestrator-sessions/"))` auto-restores the latest snapshot
on `Agent(...)` construction and auto-persists after each invocation. `session_id=goal_id` per
`strands-capability-mapping.md`'s explicit recommendation. Deliberate simplification vs. the
originally-sketched prefix: no `{env_name}` segment inside the S3 key prefix — the bucket *name*
already physically separates environments, so repeating it in the prefix is redundant nesting
with zero isolation benefit.

**Acceptance Criteria**
- [x] `_build_orchestrator_agent` constructs and attaches a `SnapshotSessionManager` per call.
- [x] `_strip_trailing_unresolved_tool_use(agent)` runs immediately after `Agent(...)` construction
  — a hand-rolled implementation of a documented mitigation pattern (no such function exists in
  `strands-agents==1.55.1`), removing a trailing unresolved `toolUse` block from restored history.
- [x] Unit tests assert the wiring chain directly against the REAL `SnapshotSessionManager`/
  `S3Storage` classes (no fakes needed — their constructors do no I/O, just store attributes):
  `session_manager.session_id == goal_id`, and the real `S3Storage` nested inside the manager's
  `_NamespacedStorage` wrapper (`session_manager._storage._storage`) has the right bucket/prefix.

**Tasks**
- [x] `app/orchestrator/orchestrator.py`.

**Dependencies:** SR-01. **Status:** ✅ done — deployed to `dev` as an isolated intermediate step
(session-resume wired, prompts not yet minimized — SR-03) and live-verified: submitted a goal,
confirmed a session snapshot appeared in `tendril-dev-agent-state` keyed by `goal_id`; sent a
follow-up chat message and confirmed the snapshot was updated (`LastModified` advanced) and the
reply stayed coherent.

---

## SR-03 — Per-handler prompt minimization

**As the** orchestrator, **I want** each turn to send only what's genuinely new, **so that** a
resumed session's own history isn't redundantly (and confusingly) re-stated every turn.

**Acceptance Criteria**
- [x] `handle_goal_submitted`'s prompt is unchanged (turn 1 always starts a fresh session
  regardless — the safest, smallest first slice to verify resume works before touching the other
  two handlers).
- [x] `handle_goal_message_received` sends only the newest message's text (a new
  `_load_latest_message` helper, single-item query) instead of the old full-transcript rebuild;
  `_load_messages`/`_format_transcript` deleted once nothing calls them.
- [x] `handle_task_checkin_received` sends the existing check-in framing text (extracted verbatim
  from the old `_format_transcript`'s `checkin_task` branch into its own small
  `_build_checkin_prompt` function) — no full message-history query for this handler at all.
- [x] Unit tests prove each handler's new, minimal prompt shape, and that a full message-history
  read no longer happens for the check-in handler.
- [x] **Live verification**: a fresh goal submission, a follow-up chat message answered coherently
  without transcript restatement (the resumed session already had the plan/history — the reply
  correctly referenced the prior turn's suggestion).

**Tasks**
- [x] `app/orchestrator/orchestrator.py`: `_load_latest_message`, `_build_checkin_prompt`; deleted
  `_format_transcript`/`_load_messages`.

**Dependencies:** SR-02. **Status:** ✅ done — deployed to `dev` and live-verified end to end with
two real goals: **Goal A** ("my mint plant looks droopy and wilted") — turn 1 proposed a plan
(water/monitor/drainage tasks); a turn-2 chat message carrying only NEW information ("actually
it's in full direct sun all day") produced a specialist tool-call combining it with turn 1's
original symptom ("droopy and wilted and is in full direct sun all day," confirmed in CloudWatch
logs) — proof the resumed session retained turn 1's context correctly with no transcript restated
this turn; the reply correctly synthesized both watering *and* sun-exposure causes and revised
the plan (mulch/water/adjust-sunlight tasks). Checking in the "Water deeply" task wrote real
`feedback` and flipped it to `done`. **Goal B** ("white powdery spots on my cucumber leaves," a
different garden/goal) produced a plan entirely about powdery mildew with zero mention of mint,
sun, or Goal A's watering schedule — confirming `session_id=goal_id` genuinely isolates sessions,
not just coincidentally. Confirmed via `aws s3api list-objects-v2` that each goal has its own,
separately-keyed snapshot object in `tendril-dev-agent-state`.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint + tests
+ secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev** via the
pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs**
(`data-architecture.md` §2/§3.1/§6.1/§6.5/§9 for this epic), and is **human-reviewed**.

### Carried forward (not in this epic)

- The tracker's at-least-once/no-dedupe gap (TR-02) — a real, accepted MVP simplification, not
  fixed here.
- `_strip_trailing_unresolved_tool_use` (SR-02) stays a hand-rolled implementation of a documented
  pattern, not an SDK guarantee — revisit if a future `strands-agents` release ships an equivalent
  built-in.
- `ContextOffloader`/`ContextInjector` pinning and `MemoryManager`'s second Knowledge Base
  (Horticultural Reference) — `ADR-0002`'s action items 2-3, still open, unrelated to session
  *persistence* specifically.
- `UserConnectionsIndex` (on `ConnectionsTable`) — a separate, still-open GSI, not part of this
  epic's scope even though `data-architecture.md` §9 originally bundled it with `TasksDueIndex`.
- A real progress *log* (multiple check-ins per task, before/after photo comparison) — PA-04/05
  only support one one-shot check-in per task; this epic's tracker is the prerequisite, not the
  log itself (`docs/roadmap.md`'s "8+ Carried forward" already names this explicitly).
- The WebSocket push channel — follow-ups still land via polling (Goal Detail's existing chat
  thread), not a live push.
