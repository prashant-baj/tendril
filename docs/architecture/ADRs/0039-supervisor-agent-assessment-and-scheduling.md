# ADR-0039: The Supervisor agent, starting with activity assessment and scheduling

**Status:** Accepted (2026-11-24, project owner) and built the same day. Owner decisions:
"Assessment and scheduling can be done by Supervisor agent"; schedule changes need a Confirm
tap; read-only specialist consultation; the daily review at 06:00 IST.
**Date:** 2026-11-24
**Deciders:** Project owner / lead engineer
**Stories:** CP-07 (Supervisor agent + garden-state assessment), first slice; serves CP-06 via
[ADR-0038](./0038-garden-activities-schedule-media-and-assessment.md). Promotes the Supervisor from
*candidate* (`architecture.md` §3) to a decided component.

## Context

`architecture.md` §3 lists the **Supervisor** as a candidate: a new agent **above** the
orchestrator. It owns a garden's standing goals and an agenda of scheduled checks, and reads a
deterministic garden-state rollup. When it decides to act, it submits a garden-scoped goal to the
orchestrator.

ADR-0038 needs something that, for every planned or logged farm activity:

- judges whether it **benefits** the farm or may cause **issues or harm**;
- keeps **schedules** sensible: what to do next, when to move or pause a recurring input.

Both are garden-level judgements. They need the whole picture (soil, recent work, watch-listed
palms, weather), not one plant or one chat. The owner decided they belong to the Supervisor.

**What exists to build on:**

- **The orchestrator** (ADR-0012) is a Lambda-hosted Strands agent, event-triggered, using
  specialists through the registry.
- **Internal registry entries** (ADR-0034's `internal` flag): specialists called by code.
- **Proposals** (ADR-0034): `ACTION#` items, applied only by a deterministic confirm endpoint, but
  scoped to a **goal** (`ACTION#{goal_id}#…`, `/goals/{goalId}/actions/…`).
- **The owner's rule** (ADR-0037): saving information needs no confirm tap; changes do.
- **Weather:** the weather tool and `GET /gardens/{id}/weather`.
- **Structured output:** Nova 2 Lite skips optional nested fields, so required fields are needed
  (2026-11-23 fix).

## Decision

### 1. What the Supervisor is

- **A Lambda-hosted Strands agent,** `app/supervisor/`. It is self-contained (own `Dockerfile` and
  `requirements.txt`), the same hosting pattern as the orchestrator (ADR-0012, ADR-0014). It is
  event-triggered and short-lived. Its state lives in DynamoDB, not in a long agent session.
- **Prompt and guardrail are externalized** (ADR-0006/0008): `prompts/supervisor-system.md`, added to
  `PROMPT_CATALOG`, and `guardrails/supervisor_guardrail.json`.
- **Model:** `global.amazon.nova-2-lite-v1:0` by default, configurable with CDK context
  `supervisor_model_id`. It is reasoning-heavy work; the eval in §6 decides whether to upgrade.
  Explicit `max_tokens`; required-field structured output (no optional nested objects).
- **It sits above the orchestrator and doesn't replace it.** In this slice it never submits goals.
  Submitting garden-scoped goals (CP-07's "standing goals") is a later slice with its own ADR
  amendment.

### 2. What wakes it

| Trigger | Source | Job |
|---|---|---|
| `activity.planned` | Client API (ADR-0038) | Assess the plan; may propose a schedule |
| `activity.logged` | Client API (ADR-0038) | Assess what was done (with photos); may propose the next due date or a change |
| `supervisor.daily` | EventBridge Scheduler, once a day per garden (early morning, garden-local time from context; default 06:00 IST) | **Agenda:** review due and overdue activities against the forecast and recent work; propose moves (e.g. "rain forecast tomorrow: move the spray to Friday?") |

Each run handles one garden. Runs are idempotent: an assessment is keyed to the activity's version
(`assessed_version`), so a retried event doesn't assess twice.

### 3. What it sees: a deterministic rollup, built by code

Code assembles a compact **garden-state rollup**, model-free, before the model is called:

- **Garden context** (ADR-0032), minus personal data such as notification recipients.
- **Plant status:** counts (healthy / ok / watch) and the **watch list**, i.e. palms whose latest
  status is `watch`, with their latest notes.
- **Recent work:** activity logs from the last 60 days, with doses and verdicts.
- **Activities and schedules:** what's due, overdue and upcoming.
- **The forecast** (the weather tool), when the location resolves.
- **For a logged activity:** its photos (analysis copies) and video poster frames.

The model reads this rollup, not 105 plant records. The rollup has a size budget, and the oldest
work is dropped first.

### 4. What it may do

| Action | How | Confirm tap? |
|---|---|---|
| Write an **assessment** on an Activity / activity log (ADR-0038 §4 contract) | Direct write by Supervisor code | **No**: information only, never changes the activity |
| **Propose** a new activity, a schedule (`every_days`, window), moving a due date, pausing or resuming | Garden-level proposal (§5) | **Yes**: these change the schedule |
| Consult specialists (agronomy, irrigation, pest & disease) for depth | Read-only specialist tools, as the orchestrator uses them | No (reading) |
| Change plants, tasks, goals; delete anything; set a plant's status | Never | — |

**Due-date arithmetic stays in code** ("21–28 days after the last done"). The Supervisor decides
*whether* a schedule should exist or change, and code computes the dates.

**One change to the candidate description:** it said the Supervisor doesn't call specialists. This
slice lets it **consult** them read-only. Judging an input's benefit or harm needs agronomy
knowledge, and routing every assessment through a full orchestrator goal would put a chat thread on
every activity.

### 5. Garden-level proposals

ADR-0034's proposals are goal-scoped. The Supervisor's proposals belong to the garden:

- **Stored as** `GARDEN#{g}` / `ACTION#garden#{action_id}`, with the same lifecycle (`proposed` →
  `confirmed` | `rejected` | `superseded`). A newer proposal on the same activity supersedes the older
  one.
- **Confirmed or rejected through new endpoints:**
  - `POST /gardens/{gardenId}/actions/{actionId}/confirm`
  - `POST /gardens/{gardenId}/actions/{actionId}/reject`
- **The confirm endpoint is deterministic,** with no model call (the PA-02/ADR-0034 pattern).
  Appliers: `create_activity`, `set_schedule`, `move_due`, `pause_activity`, `resume_activity`.
- **Validated by code before they are stored:** the activity exists in this garden, dates aren't in
  the past, and intervals are within sane bounds (1–365 days).
- **Listed** on the Farm work screen ("Tendril suggests…"), with Confirm / Not now.

### 6. Measured, not assumed

- **Eval set,** `tests/eval/supervisor/`: scenarios built from the owner's practice, scored on
  verdict and conflict detection. Examples:
  - humic every 3 weeks: beneficial;
  - a foliar spray within 2 days of a drench: caution, with a conflict;
  - heavy watering on a salt-crusted clay basin: caution or harmful;
  - spraying with rain forecast in 6 hours: caution;
  - jivamrut on a waterlogged basin: caution.
- **Gate:** assessments are shown to the owner as soon as this ships, because they are advisory. The
  daily agenda's **proposals** turn on only when the eval's scheduling scenarios pass.

### 7. Cost and limits

- **Model calls:** one per planned or logged activity (a few a week on this farm) and one daily
  agenda run per garden.
- **Specialist consultations** are capped per run (at most 2).
- **Timeouts** are set in `tool.json`-style sizing for the Lambda.
- **A failed run** leaves the assessment as `unknown`; it never blocks the owner.

## Options Considered

| Option | Verdict |
|---|---|
| **A. Supervisor agent: Lambda-hosted, event + daily triggers, rollup by code, direct assessments, proposals for schedule changes (chosen)** | **Chosen**: one owner of garden-level judgement; matches the owner's rule on confirms |
| B. Internal `activity_assessor` specialist called by orchestrator code (ADR-0038's first draft) | Rejected by the owner: scheduling and the daily agenda need an agent that owns the garden, not a per-call helper |
| C. Supervisor submits an orchestrator goal for every activity | Rejected: a chat thread per activity; slower; noisy |
| D. AgentCore runtime with a long-lived session | Not needed: runs are short and state is in DynamoDB; revisit for long multi-step agendas |
| E. Supervisor sets schedules directly (no confirm) | Rejected under the owner's rule: schedules are changes. Revisit if confirming becomes a burden |

## Consequences

- **Easier:** one agent answers "is this good for the farm?" and "what should happen next, and
  when?" with the whole garden in view. CP-07's later slices (standing goals, block assessments)
  build on the same rollup and triggers.
- **Harder:**
  - a new deployable unit, `app/supervisor/`, with its prompt, guardrail and IAM;
  - a daily EventBridge schedule per garden;
  - garden-level proposals and endpoints;
  - an eval set to maintain.
- **ADR-0012:** a second Lambda-hosted agent next to the orchestrator; it uses the registry's
  specialists read-only. **ADR-0034:** proposals gain a garden scope. **ADR-0038:** its §4/§5 point
  here. **`architecture.md` §3:** the Supervisor moves from candidate to decided once this is
  accepted.
- **Security:**
  - the Supervisor's role can read the garden partition and media (presigned, as the orchestrator
    does);
  - it can write only `assessment` fields and `ACTION#garden#` items;
  - personal data (notification recipients) is never sent to the model.

## Action Items

**Build notes (2026-11-24):**

- **Code:** `app/supervisor/supervisor.py` + `supervisor_rollup.py` (pure); CDK `_add_supervisor`
  (rules `tendril-{env}-activity-events` and `tendril-{env}-supervisor-daily`, cron 00:30 UTC);
  `prompts/supervisor-system.md`; `guardrails/supervisor_guardrail.json`.
- **Structured output** uses only required fields (`proposal` / `min_days` / `max_days` /
  `due_on` with empty values) — the 2026-11-23 Nova 2 Lite lesson.
- **Eval, 2026-11-24** (`scripts/eval_supervisor.py`, Nova 2 Lite, the deployed prompt text):
  - assessments **5/5** after two fixes found by the eval:
    - the activity description now spells out "last done … (N days ago)", because the model had
      misread a 32-day gap as 2 days;
    - the agenda item types now describe when to move vs. pause.
  - the daily agenda is **not stable** (0/2 and 1/2 across runs; once "pause" instead of "move",
    once a suggestion where none was needed).
  - So `supervisor_proposals_enabled` stays **off**: daily suggestions are logged, not shown.
    Assessments and event-time schedule proposals (which still need a Confirm) are on.
- **Daily agenda:** it re-assesses any activity never assessed or changed since (at most 10 per
  run), so a missed event heals the next morning.
- **Gardens for the daily run** come from a filtered scan for `ACTIVITY#` items (fine at this scale;
  a sparse index would replace it at thousands of gardens).
- **Weather:** a 3-day Open-Meteo forecast (rain amount and chance). Geocoding tries the whole
  location, then each comma-separated part.

1. [x] Human review: approved 2026-11-24.
   Points that were reviewed:
   - §4: proposals need a confirm, assessments don't;
   - §4: it consults specialists;
   - §2: the daily agenda time;
   - §6: proposals gated on the eval.
2. [x] `app/supervisor/` (handler, rollup builder, structured output, specialist tools); CDK: Lambda,
   EventBridge rules for `activity.planned` / `activity.logged`, the daily schedule, IAM, model context.
3. [x] `prompts/supervisor-system.md` (+ `PROMPT_CATALOG`), `guardrails/supervisor_guardrail.json`.
4. [x] Garden-level proposals: `ACTION#garden#` items, confirm/reject endpoints (OpenAPI first),
   appliers, validation.
5. [x] Eval set and runner (`tests/eval/supervisor/scenarios.json`, `scripts/eval_supervisor.py`),
   scored against the gate. [ ] Daily agenda passing consistently (gate still closed).
6. [x] Docs: `architecture.md` (component table, diagram, events), data-architecture (assessment
   fields, garden actions), stories CP-07/CP-06, backlog.
7. [ ] Dev verification (pending deploy):
   - log a humic drench and see "beneficial";
   - log a spray 2 days later and see "caution" naming the drench;
   - with rain forecast, see the daily agenda propose moving a due spray; confirm it and see the new
     due date.
