# Stories — Epic: Tasks & Activity on Real Data

`docs/roadmap.md` Phase 6. The Tasks and Activity screens have been scaffolded since the frontend
shell (Phase 0) but stayed on `MockTaskApi`/`MockActivityApi` — both hardcoded to always return
empty arrays once the mockup-fixture removal pass ran (there was never a real backend for either).
This epic replaces both with real data.

**Two real gaps surfaced while scoping this, not visible from the mockup alone:**

1. `Task` (`app/orchestrator/orchestrator.py`) has no due-date/schedule concept at all — the
   mockup's Tasks screen grouped into "Today / This week / Later," but a real `Task` only ever
   gets `title`/`detail`/`scope`/`status`/`plantId?`/`mediaId?`/`feedback?`. `data-architecture.md`
   explicitly reserves the `TasksDueIndex` GSI for due-date scheduling and marks it **Phase 7+**
   (tracker/scheduler) work — genuinely out of scope here, not an oversight.
2. The `Event` entity (`data-architecture.md` §2) had been reserved since the original design but
   no code path had ever written one — the Activity screen had literally nothing to show.

**Scope decisions (confirmed with the user before building):**
- Tasks screen groups real tasks by **status** ("To do" / "Done"), across every goal in the
  garden — not by an invented due-date bucket.
- Activity gets a minimal event log covering **goal + task lifecycle milestones**: goal
  submitted, plan proposed/revised, plan approved, task checked in. Not every chat message —
  chat already has its own home in Goal Detail's Conversation section; logging every message too
  would make the timeline noisy without adding new information.

Both screens are read-only. Tasks screen rows navigate to their own goal to actually act on a
task (check it in, discuss it) — no duplicate check-in UI was built here.

---

## TA-01 — List every task across every goal (real data, no due-date grouping yet)

**As a** gardener, **I want** to see every task across all my goals in one place, **so that** I
don't have to open each goal individually to know what's pending.

**Context:** `Task`'s existing key (`pk=GARDEN#{garden_id}`, `sk=TASK#{goal_id}#{task_id}`,
established in PA-01) already makes "every task in this garden" a single `Query` on `pk` +
`sk.begins_with("TASK#")` — no GSI needed for a read-only, ungrouped-by-date list. `getGoalDetail`'s
existing task-shaping logic (media/feedback/plantId resolution) was extracted into a shared
`_task_from_item(table, garden_id, item)` helper so `getGoalDetail` and the new `listTasks` don't
duplicate it.

**Acceptance Criteria**
- [x] New `GET /gardens/{gardenId}/tasks` → every task across every goal in the garden, each
  carrying its own `goalId` (new optional field on the `Task` schema — harmless/redundant on
  `getGoalDetail`, which sets it too now for consistency, but load-bearing here).
- [x] Tasks screen groups results into "To do" (`status !== 'done'`) and "Done" (`status ===
  'done'`) — client-side, mirroring `HomeComponent`'s existing `goalsInProgress`/
  `goalsNeedingAttention` filter pattern exactly.
- [x] Each row navigates to its own goal (`TaskRowComponent`, `[routerLink]="['/goals', task.goalId]"`)
  instead of the old mockup's plain toggle-to-complete — real completion happens via a photo
  check-in (PA-04/05), which already lives on Goal Detail.
- [x] Home's "Today" widget also uses real data now — capped at 5 pending tasks (a quick-glance
  widget, not the full list; there's no real "due today" filter to apply without the due-date
  concept this story deliberately doesn't build).
- [x] Unit tests: `listTasks` across multiple goals, empty, missing `gardenId`, Dynamo failure,
  contract-conforms-to-openapi. Component tests: grouping, navigation, empty state.

**Tasks**
- [x] `garden_handler.py`: extracted `_task_from_item`; new `list_tasks`; routed in `handler()`.
- [x] `openapi.yaml`: new `GET /gardens/{gardenId}/tasks`; `Task.goalId` (optional).
- [x] Frontend: `GardenTaskItem`/`TaskGroup` (`task.model.ts`) replace the mockup-shaped
  `TaskItem`/`TaskTone`; `HttpTaskApi` (`task.service.ts`); `TaskRowComponent` reworked;
  `TasksComponent` groups by status; `HomeComponent`'s `todayTasks` wired to real data.

**Dependencies:** PA-01 (`Task` entity). **Status:** ✅ done — deployed to `dev` and
live-verified: a real garden's tasks across 4 goals all listed correctly in one call.

---

## TA-02 — A real Activity timeline (the `Event` entity, implemented for the first time)

**As a** gardener, **I want** to see a timeline of what's happened with my garden, **so that** I
have a record of what I asked for and what Tendril did, without digging through each goal.

**Context:** `data-architecture.md` §2 reserved `EVENT#{iso_timestamp}#{event_id}` since the
original design, but this is the first story to actually write one. Deliberately UI-agnostic —
`type`/`payload` only, no icon/tone/title server-side — the frontend maps `type` to presentation
(`activity-presentation.util.ts`, mirroring `specialist-icon.util.ts`'s "keep the backend free of
UI concerns" pattern already established in PA-05).

**Acceptance Criteria**
- [x] New `GET /gardens/{gardenId}/activity` → every `Event` in the garden, newest first
  (`ScanIndexForward=False` — DynamoDB does the ordering, not a client-side sort).
- [x] `Event` written **best-effort** (a small `_write_event` helper, its own try/except, never
  fails the primary request — duplicated in `app/api/garden_handler.py` and
  `app/orchestrator/orchestrator.py` since they're separate deployable units, no cross-package
  Python imports in this monorepo) at 4 call sites:
  - `createGoal` → `goal.submitted`
  - `_write_plan_and_tasks` (orchestrator) → `plan.updated` (covers first proposal and every
    revision alike — one event type, not two)
  - `approvePlan` → `plan.approved`
  - `postTaskCheckin` → `task.checkin` (a second, unrelated write from the same handler that
    already publishes the `task.checkin.received` EventBridge event PA-05 added — one's an
    audit-log item, the other an async trigger)
- [x] Activity screen renders each entry's mapped title/detail/icon/tone, links back to the
  entry's goal when one is present, and shows an empty state when there's no activity yet.
- [x] Unit tests: `listActivity` happy path + newest-first ordering + empty + missing `gardenId`
  + Dynamo failure + contract-conforms-to-openapi; best-effort Event writes at each of the 4 call
  sites (asserting the write happens, and that a failure there doesn't fail the request).
  Component tests: presentation mapping (including an unknown-type fallback), goal linking,
  connecting-line rendering.

**Tasks**
- [x] `garden_handler.py`: `_write_event`; calls in `create_goal`/`approve_plan`/
  `post_task_checkin`; new `list_activity`; routed in `handler()`.
- [x] `orchestrator.py`: `_write_event`; call in `_write_plan_and_tasks`.
- [x] `openapi.yaml`: new `GET /gardens/{gardenId}/activity`; new `ActivityEvent` schema.
- [x] Frontend: `ActivityEvent` model rewritten to the real (UI-agnostic) shape, dropping the
  mockup's `quote` concept (WhatsApp integration is explicitly deprioritized, `docs/backlog.md`'s
  carried-forward notes); new `activity-presentation.util.ts`; `HttpActivityApi`;
  `ActivityItemComponent` reworked.

**Dependencies:** TA-01 is independent of this, but both were built and shipped together.
**Status:** ✅ done — deployed to `dev` and live-verified end to end: submitted a goal
(`goal.submitted` appeared immediately), the orchestrator proposed a plan (`plan.updated`
appeared), approved it (`plan.approved`), checked in a task with a photo (`task.checkin`
appeared) — all four, newest-first, in one `GET .../activity` call.

---

### Definition of Done (applies to every story)

Per [`../engineering-best-practices.md`](../engineering-best-practices.md): CI green (lint +
tests + secret scan), **no hardcoded secrets/account identifiers**, deploys cleanly to **dev** via
the pipeline, touches **only the folders it needs**, updates relevant **docs/ADRs** (this epic
touches `data-architecture.md` §2 directly — the `Event` row flips from "not yet implemented" to
implemented), and is **human-reviewed**.

### Carried forward (not in this epic)

Due-date/schedule-aware tasks and the "Today/This week/Later" grouping the original mockup
sketched (needs the `TasksDueIndex` GSI + tracker/scheduler Lambda, Phase 7.5+); logging chat
messages as activity (deliberately excluded — would make the timeline noisy without adding new
information beyond what Goal Detail's Conversation section already shows); any write-side
aggregation/analytics over the Event log (the "future data flywheel" `data-architecture.md`
alludes to — this epic only proves the log itself gets written and read).
