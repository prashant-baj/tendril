# Tendril — Development Roadmap (UI-first, incremental)

**Approach (decided 2026-09-12):** ship one **complete, demoable, tested vertical slice**
(UI → API → Lambda → data) at a time, starting from the simplest possible feature and adding
complexity only once the previous slice works end-to-end in `dev`. This is a **feature-first**
sequencing, not the **layer-first** sequencing the Walking Skeleton epic (`docs/stories/walking-skeleton.md`)
was originally written in (contract for everything → infra for everything → Lambda for
everything → orchestrator → frontend last). WS-01..WS-05's acceptance criteria and tasks are
still the right detail to build from — they're just consumed in a different order, with a couple
of scope adjustments noted below (§3).

This map exists to answer "what do we build next, and what does 'done' mean for it" at a glance.
It doesn't replace the story files — it sequences them.

---

## 1. Phase map

| Phase | Feature | New or existing story | UI | API operation(s) | Lambda | Data touched | Orchestrator/Agents involved? |
|---|---|---|---|---|---|---|---|
| **0** | Frontend shell + all 6 screens (mocked), architecture/ADRs, story epics | — | ✅ done | — | — | — | No |
| **1** | **Setup My Garden** | **OB-01** — ✅ done, verified end-to-end in dev | New `/garden-setup` screen | `POST /gardens`, `GET /gardens/{id}` | First `ClientApiStack` + garden handler | `AppTable`: Garden (canonical + ownership index) | No |
| **2** | **Add a Plant (with a photo)** | **OB-02** — ✅ done, verified end-to-end in dev (real photo landed in S3, Plant+Media linked) | Garden screen gains "Add plant"; new camera/file-picker component | `POST /gardens/{id}/media`, `POST /gardens/{id}/plants` | Presigned-upload + plant-create handlers | `AppTable`: Plant, Media; `MediaBucket` | No |
| **3** | **Capture an issue → orchestrator triggered** | **WS-01..WS-04** — ✅ done, smoke-tested against dev (found + fixed a real IAM gap — see WS-04) | Capture screen submits goal text — built in Phase 4 (WS-05) below | `POST /gardens/{id}/goals` (media op already existed from Phase 2) | Goal-intake handler; **Orchestrator** Lambda (lives in `AgentCoreStack`, not a separate stack — see WS-02's implementation note) | `AppTable`: Goal; EventBridge `goal.submitted`; `agents/registry/hello.json` | **Yes** — first orchestrator invocation, calling the `hello` stand-in specialist |
| **4** | **Wire Capture to real goal submission** | **WS-05** — ✅ done | Capture screen (photo optional, reusing OB-02's `PhotoPickerComponent`) submits a real issue via `createGoal`; shows a real "submitted" pending state, not the old simulated analyzing/found timeline (deleted, not repurposed) | `POST /gardens/{id}/goals` (already exists) | — | writes `AppTable`: Goal | Indirectly (triggers Phase 3's orchestrator) |
| **4.5** | **The orchestrator proposes a structured Plan** | **PA-01** (new, `stories/plan-approval.md`) | Goal Detail shows a real `Plan`/`Task` list once the orchestrator has produced one, instead of `MockGoalApi`'s fixture | `GET /gardens/{id}/goals`, `GET /gardens/{id}/goals/{goalId}` (new) | Read handlers | `AppTable`: new `Plan`, `Task` entities | Yes — orchestrator's output becomes structured, not free text |
| **4.6** | **Show the goal's photo(s) in Goal Detail** | **PA-03** (new, `stories/plan-approval.md`) | Goal Detail renders the actual submitted photo(s) next to the diagnosis | `GET /gardens/{id}/goals/{goalId}` (extends 4.5's response with presigned `downloadUrl`s) | extends 4.5's read handler | reads `AppTable` (`Media`), presigned GET | No |
| **5** | **Conversational plan approval (HITL)** | **PA-02** (new, `stories/plan-approval.md`) | Goal Detail gains a chat thread (ask/adjust, model can ask back) + an explicit Approve button | `POST /gardens/{id}/goals/{goalId}/messages`, `POST /gardens/{id}/plans/{planId}/approve` (new) | Message/approve handlers; **Orchestrator** re-invoked per turn (stateless — see the story's Context note on why this doesn't build `SnapshotSessionManager`/`AgentStateBucket` yet) | `AppTable`: `Plan`/`Task` revisions, new `Message` entity | Yes — first multi-turn round-trip on the same goal |
| **6** | **Tasks & Activity on real data** | *not yet storied* | Tasks/Activity screens replace `MockTaskApi`/`MockActivityApi` | `GET /gardens/{id}/tasks`, event-log read | Read handlers | `AppTable`: Task, Event | No |
| **7** | **Agent Factory** — second specialist + first real Tool API + guardrail + memory | **AF-01..AF-05** (existing) | none (backend-only) | — | `AgentCoreStack` registry loop; `tools/weather/` | `agents/registry/agronomy.json`; two Bedrock Knowledge Bases (data-architecture.md §3.2) | Yes — proves the factory scales past one agent |
| **7.5+** | Real `SnapshotSessionManager`/`AgentStateBucket` session-resume, tracker/scheduler Lambda + `TasksDueIndex` follow-ups | *not yet storied* | — | — | Tracker/Scheduler Lambda (`app/tracker/`, not yet scaffolded) | `AgentStateBucket` (new S3), `TasksDueIndex` GSI | Yes — needed once follow-ups span days, not one chat session |
| **8+** | Carried forward | WebSocket live push, remaining 7 specialists + 4 tools, WhatsApp (deprioritized), Cognito auth, aggregated-data flywheel, using photos for progress comparison over time (needs 7.5's tracker) | — | — | — | — | — |

```mermaid
flowchart TD
  P0["Phase 0 — Frontend shell (mocked)<br/>✅ done"] --> P1
  P1["Phase 1 — OB-01<br/>Setup My Garden<br/>✅ done"] --> P2
  P2["Phase 2 — OB-02<br/>Add a Plant (+ photo)<br/>✅ done"] --> P3
  P3["Phase 3 — WS-01..04<br/>Capture issue → Orchestrator → hello<br/>✅ done, smoke-tested"] --> P4
  P4["Phase 4 — WS-05<br/>Wire Capture to real goal submission<br/>✅ done"] --> P45
  P45["Phase 4.5 — PA-01<br/>Orchestrator proposes a structured Plan"] --> P46
  P46["Phase 4.6 — PA-03<br/>Show the goal's photo(s) in Goal Detail"] --> P5
  P45 --> P5
  P5["Phase 5 — PA-02<br/>Conversational plan approval (chat + Approve)"] --> P6
  P6["Phase 6 — (new epic)<br/>Tasks + Activity on real data"] --> P7
  P7["Phase 7 — AF-01..05<br/>Agent Factory: 2nd specialist, tools, guardrail, memory"] --> P75
  P75["Phase 7.5+ — (new epic)<br/>Real session-resume + tracker/scheduler + TasksDueIndex"] --> P8
  P8["Phase 8+ — carried forward<br/>WebSocket push, remaining agents/tools,<br/>WhatsApp, auth, data flywheel, photo progress comparison"]
```

## 2. Definition of "increment done" (applies to every phase)

- **Frontend:** the real UI is wired to a real endpoint for that flow — no `Mock*Api` left in the
  path being demoed; component tests pass.
- **Backend:** Lambda + its CDK stack change deploy cleanly to `dev`; unit tests + CDK assertion
  tests pass.
- **End-to-end:** a manual smoke test against `dev` is performed and its result recorded in the
  story (matching the pattern already used in WS-04/AF's "manual smoke test recorded" criteria).
- **Docs:** the story's checkboxes are ticked; `docs/backlog.md`'s rank/status is updated.
- The full [Definition of Done](../engineering-best-practices.md) applies throughout (CI green,
  no hardcoded secrets, selective deploy, docs updated, human-reviewed) — this map doesn't relax
  it, just says *when* each piece of it gets exercised.

## 3. Resequencing notes (how this reorders what's already written)

- **`ClientApiStack` now stands up in Phase 1 (OB-01)**, not WS-02. Every later phase that adds a
  Client API operation (OB-02, WS-01, Phase 4.5, 4.6, 5, 6) is adding to that same stack, never
  creating a new one.
- **The presigned media-upload operation (`POST /gardens/{id}/media`) now belongs to Phase 2
  (OB-02)**, not WS-01. WS-01's scope shrinks to just the goal-intake contract
  (`POST /gardens/{id}/goals`) when Phase 3 is picked up — the media op already exists by then.
- **The camera-capture-or-file-picker UI component is built once, in Phase 2 (OB-02)**, and
  reused by WS-05's Capture screen in Phase 3/4 — not built twice.
- WS-02's scope (EventBridge trigger, orchestrator infra, the declarative agent registry) is
  unchanged — it's still the right detailed story for Phase 3, just picked up after OB-01/OB-02
  instead of right after WS-01.
- **Phases 4.5, 4.6, and 5 are now storied** — [`docs/stories/plan-approval.md`](./stories/plan-approval.md)
  (PA-01, PA-03, PA-02 respectively, written 2026-09-13). Phase 5's design makes one explicit,
  documented scope call: it does **not** build the `SnapshotSessionManager`/`AgentStateBucket`
  session-resume `data-architecture.md` §3.1 designs — each chat turn is a fresh, stateless
  orchestrator invocation instead (see the story's Context note). True session-resume is deferred
  to the new **Phase 7.5+** (tracker/scheduler + multi-day follow-ups), where it's actually needed.
- Phase 6 (Tasks & Activity on real data) is still genuinely new — flagged as "not yet storied"
  rather than invented in detail here. Write it (matching the existing `As a/I want/so that` + AC
  + Tasks format) once Phase 5 is picked up.

## 4. What to build right now

**(2026-09-13 update)** Phases 1-4 (OB-01/02, WS-01..05) are done and smoke-tested against `dev`;
a live gardener test (delete old plants, add one real plant + issue report) confirmed the pipe
still works end-to-end with no errors and a correct diagnosis. Phases 4.5/4.6/5 are now storied
(`stories/plan-approval.md`) — **PA-01 is next up**: it's the dependency both PA-03 (photo
display) and PA-02 (conversational approval) build on, so it isn't optional to sequence around.
Recommended order: **PA-01 → PA-03 → PA-02**, per the epic's own "Suggested order." Phase 7
(AF-01..05, Agent Factory) remains a reasonable alternative next pick if backend-only work is
preferred over this frontend-heavy epic; ask before assuming which.

## 5. Related documents

- [`docs/stories/garden-onboarding.md`](./stories/garden-onboarding.md) — OB-01, OB-02, OB-03 (Phases 1-2, 4.6)
- [`docs/stories/walking-skeleton.md`](./stories/walking-skeleton.md) — WS-01..05 (Phases 3-4, scope-adjusted per §3)
- [`docs/stories/plan-approval.md`](./stories/plan-approval.md) — PA-01, PA-02, PA-03 (Phases 4.5, 5, 4.6)
- [`docs/stories/agent-factory.md`](./stories/agent-factory.md) — AF-01..05 (Phase 7)
- [`docs/architecture/data-architecture.md`](./architecture/data-architecture.md) — entity/key design every phase writes against
- [`docs/backlog.md`](./backlog.md) — the ranked, single source of truth this map sequences
