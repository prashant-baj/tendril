# ADR-0038: Garden activities: scheduled and ad-hoc work, photos and videos, and an AI benefit/harm check

**Status:** Accepted (2026-11-24, project owner) and built the same day. Owner answers: highlight
harmful verdicts **and** ask "Are you sure?" before logging work flagged harmful; videos up to
100 MB / 60 s; subset-of-palm work appears in those palms' histories; the 15 Nov spray details as
recorded. Revised before acceptance: the owner
decided that **assessment and scheduling are done by the Supervisor agent**. That agent is decided
in [ADR-0039](./0039-supervisor-agent-assessment-and-scheduling.md); this ADR keeps the farm-work
data, due dates, screens and video, and defines what the Supervisor reads and writes.
**Date:** 2026-11-24
**Deciders:** Project owner / lead engineer
**Stories:** CP-06 (garden activity log), extended by the owner on 2026-11-24:

- activities can carry photos **and videos**, optionally;
- there are **recurring scheduled** and **ad-hoc** activities;
- **AI assesses each activity** for benefit to the farm and for possible issues or harm, and the result
  is highlighted.

Builds on ADR-0032 (garden context), ADR-0033 (media ingestion), ADR-0035 (plant history) and the
owner's rule from ADR-0037: *saving information needs no confirm tap; changes do*.

## Context

**What the owner does today (archive, roadmap):** garden-wide work is the main thing that happens on
the farm:

- inputs: humic acid drench (23 Oct), a foliar spray of neem oil 40 ml + Dhartivita 10 g + sticker 5 ml
  (15 Nov), jivamrut (planned), gypsum;
- operations: weekly deep leaching, mulching, weeding the collar rings, moving drippers.

Some of this repeats ("humic every 3–4 weeks", "weekly leaching"). Some is one-off ("mulch #94, #95,
#102"). The owner wants each piece of work recorded (with a photo or a short video when useful) and
**checked**. For example: is spraying four days after a drench fine; is jivamrut wise on waterlogged
clay; does more water make the salt crust worse?

**What exists:**

- **Tasks** exist, but only inside a goal's plan (`TASK#{goal_id}#{task_id}`), approved as part of
  that plan. `scope: garden` exists but has no garden-level home.
- **The Tracker** scans `TasksDueIndex` for one-off due dates. Nothing repeats.
- **Events:** the Activity screen shows `Event`s; `input.applied` exists (one, from the CP-04 migration).
- **Media is photos only:** ingestion makes analysis copies and thumbnails (ADR-0033). Uploads are
  limited to 10 MB, and the Image Tool rejects anything that isn't an image.
- **Nothing assesses an action before or after it's done.** Specialists diagnose plants, not
  interventions.

A new entity, a new media type (video), a new event flow and AI output shown as a warning are
architecturally significant.

## Decision

### 1. Two records: the activity, and each time it's done

| Entity | pk | sk | Holds |
|---|---|---|---|
| **Activity** (what, and when it's due) | `GARDEN#{g}` | `ACTIVITY#{activity_id}` | `title`, `kind` (`input` \| `operation`), `details` (product, dose, method, as text plus optional `product`/`dose`), `targets` (`all` or a list of `plant_id`s), `schedule` (§2), `status` (`active` \| `paused` \| `done` \| `archived`), `next_due_on?`, `last_done_on?`, `assessment?` (§4), `source` (`user` \| `confirmed`), `created_at` |
| **Activity log** (it was done) | `GARDEN#{g}` | `ACTLOG#{done_on}#{log_id}` | `activity_id?`, `title`, `kind`, `details`, `targets`, `done_on`, `note?`, `media_ids?` (photos/videos, ≤ 6), `assessment?`, `created_at` |

**How the two cases map:**

- **Ad-hoc work** ("mulched #94 today") is an **activity log** with no `activity_id`, or a one-off
  Activity that becomes `done` when it is logged.
- **Planned or recurring work** is an **Activity**; each time it's done adds an activity log and moves
  `next_due_on`.

**Why not goal tasks:** garden work is standing, not part of one plan's approval. A goal's plan can
still *propose* an activity (§5). `ACTLOG#` sorts by date, so the activity-log view is one `Query`
in either order.

**Each log also writes:**

- an `Event` (`input.applied` or `activity.done`) for the Activity screen;
- for a **subset of palms only**, a `log` entry in each targeted palm's history (ADR-0035,
  `source: user`). Garden-wide work isn't copied into 105 histories; the plant page shows garden
  activities from the log instead.

### 2. Schedules: counted from the last time it was done

- **`schedule`** is one of:
  - `none` (one-off);
  - `{ every_days: N }`;
  - `{ every_days_min: 21, every_days_max: 28 }`, a window, matching "every 3–4 weeks".
- **Counted from the last `done_on`,** not a fixed calendar: farm inputs are spaced from the last
  application.
- **Due and overdue:**
  - `next_due_on = last_done_on + min`;
  - the activity is **due** from `next_due_on`;
  - it is **overdue** after `last_done_on + max` (or `+ N` for a fixed interval).
- **Before the first time it's done:** `next_due_on` is the start date the owner picks.
- **Fixed calendar schedules** ("1st of every month") are left out for now; they can be added to
  `schedule` later.

### 3. Seeing what's due (before WhatsApp)

- **Due and overdue activities** are computed on read from the `ACTIVITY#` items, which is one
  partition query and small (tens of activities per garden). No index or scheduler is needed yet.
- **Home** gets a **"Farm work due"** card listing overdue first.
- **A new "Farm work" screen** lists upcoming and past activities (the activity-log view), with
  "Log it" on each.
- **The existing Tracker and its index are not used for activities yet.** Push reminders (WhatsApp,
  CP-09) will put activities on `TasksDueIndex` then; that is noted for that ADR.

### 4. AI assessment: done by the Supervisor (ADR-0039), highlighted, never blocking

**When:** each activity is assessed when it's **planned** (created or edited) and when it's
**logged** (done, with its photos).

**How:**

- The API publishes `activity.planned` / `activity.logged` on EventBridge and returns at once.
- The **Supervisor** agent (ADR-0039) assesses the activity. ADR-0039 decides how it works: its
  inputs (garden state, recent work, soil, watch list, forecast, photos), its model and its prompt.

**This ADR fixes the contract**, stored as `assessment` on the Activity or activity log:

```json
{ "verdict": "beneficial | caution | harmful | unknown",
  "summary": "one sentence",
  "benefits": ["…"], "risks": ["…"],
  "conflicts": [{"with": "<activity or input>", "reason": "…"}],
  "suggestion": "optional safer alternative or timing",
  "assessed_at": "…", "assessed_by": "supervisor", "model": "…" }
```

**Rules:**

- **It never changes the activity.** It's written directly with no HITL, because it only adds
  information (ADR-0037 rule).
- It doesn't cancel, reschedule or block anything. A `harmful` verdict is shown **prominently**, and
  logging or scheduling still works.

**How it's shown:**

- `beneficial` gets a green badge; `caution` amber; `harmful` a red banner with the risks and
  suggestion; `unknown` (or not yet assessed) a grey chip.
- The Farm work screen sorts harmful and caution items first.

### 5. Who creates and schedules activities

- **The owner, directly** from the Farm work screen: log something done now (ad-hoc), or plan a
  one-off / recurring activity. This is the owner's own information, so there is no confirm step.
- **The Supervisor (ADR-0039):** it proposes new activities and schedule changes (create, move a due
  date, pause or resume) as garden-level proposals. The owner confirms each with a tap.
- **Due-date arithmetic stays in code (§2):** "21–28 days after the last done" is computed, not
  generated.
- **Tendril, as a proposal:** a goal turn can propose `create_activity`, a new ADR-0034 proposal
  type, for example "Schedule humic every 3–4 weeks?". The owner confirms it with a tap because it
  creates a recurring schedule, which is a change. The plan's `scope: garden` tasks can be offered
  this way.

### 6. Agents see the farm's recent work

The orchestrator's garden context block (ADR-0032) gains a short **recent activities** section: the
last 10 logs with date and verdict, and what's due. Then advice can say "you sprayed 4 days ago;
wait before the next input". The context stays within its size budget, oldest items dropped first.

### 7. Videos (new media type)

**Upload (§7 needs owner confirmation of the limits):**

- `POST /gardens/{id}/media` accepts `video/mp4` and `video/quicktime` (phone videos) **up to
  100 MB and 60 seconds**.
- Photos keep their 10 MB limit.
- The presigned PUT is unchanged (single PUT works up to 5 GB).

**Processing:**

- Ingestion makes a **poster frame** (thumbnail) and records duration and dimensions.
- This needs `ffmpeg`, a **new dependency**. It is packaged in a separate **Media Tool** (video),
  next to the Image Tool, so the Image Tool stays small; same `tool.json` sizing pattern.
- No transcoding: the browser plays MP4/MOV as uploaded, through a presigned URL.

**Not in this ADR: AI on video.** The assessor uses the poster frame plus the text. Full video
understanding (Nova models accept video input) is a later decision with its own cost check.

**Where videos can go:** on activity logs, chat messages and plant history entries, the same as
photos.

## Options Considered

| Decision | Options | Choice |
|---|---|---|
| Where activities live | A. New `ACTIVITY#` / `ACTLOG#` entities · B. A standing "Garden work" goal holding garden tasks · C. Events only | **A**: B forces plan-approval semantics on standing work; C has no schedule or state |
| Recurrence | From last done (window) · Fixed calendar · Both | **From last done**, calendar later |
| Reminders now | Computed on read (Home + screen) · Tracker + index now | **On read**; Tracker with CP-09 push |
| Assessment and scheduling | The Supervisor agent (ADR-0039) · An internal specialist called by orchestrator code · An orchestrator goal per activity | **Supervisor** (owner decision, 2026-11-24): garden-level judgement belongs with the agent that watches the whole garden |
| Harmful verdict | Highlight only · Require confirm to proceed · Block | **Highlight only**: AI informs, the owner decides |
| Video | Store + poster frame (no AI) · Full video AI now · No video | **Store + poster frame**; video AI later |

## Trade-off Analysis

**Two new entities** keep garden work out of goal plans and make schedules simple. The cost is a new
screen and a new proposal type.

**Assessment adds one Supervisor call per planned or logged activity** (a few a week on this farm).
It is advisory only, so a wrong verdict costs attention, not data. ADR-0039 covers its cost, model
and evals.

**Video brings a heavier dependency (`ffmpeg`) and larger storage.** It is kept in its own tool, with
no AI cost until a later decision.

## Consequences

- **Easier:** every input and operation is recorded, dated and checked; recurring inputs are never
  missed; agents advise with the farm's recent work in view.
- **Harder:** new screen, entities, specialist, Media Tool and proposal type; a video size limit
  well above the photo limit; prompt and eval upkeep for the assessor.
- **ADR-0033:** extended (video types and limits, poster frames, the Media Tool). **ADR-0034:** a
  `create_activity` proposal type. **ADR-0035:** activity logs for a subset of palms write plant
  history entries. **ADR-0039:** the Supervisor consumes `activity.planned` / `activity.logged` and writes
`assessment` and garden-level proposals.
- **Migration (CP-04):** the archive's inputs (humic 23 Oct, and the 15 Nov spray once confirmed)
  can be re-imported as activity logs; a "humic every 21–28 days" schedule can be seeded.
- **CP-07 (Supervisor)** can later read activity logs and assessments to propose the next garden
  activity.

## Action Items

**Build notes (2026-11-24):**

- **Code:** `app/api/activities.py` (pure rules) + handlers in `garden_handler.py`; `app/tools/media/`
  (ffmpeg via the `imageio-ffmpeg` ARM64 wheel); ingestion routes `.mp4/.mov/.m4v` to it; the Farm
  work screen (`/farm-work`) and Home's "Farm work due"; `scripts/seed_farm_work.py`.
- **"Are you sure?":** asked only for a planned activity already assessed harmful. Ad-hoc work is
  assessed after it is logged, so there is nothing to warn about beforehand.
- **Video upload:** supported from the Farm work screen. Chat messages still take photos only,
  because the orchestrator's vision path expects images. Videos display wherever history is shown.
- **The Media Tool** is not offered to any specialist; only Ingestion invokes it.

1. [x] Human review: approved 2026-11-24 (answers above).
   Points that were reviewed:
   - §4: highlight-only, never block (assessment by the Supervisor, ADR-0039);
   - §7: video limits of 100 MB / 60 s, and video kept out of AI for now;
   - §1: subset-of-palm activities writing plant history.
2. [x] Entities + API (OpenAPI first):
   - create/list/update activities;
   - log an activity;
   - list activity logs (paged).
3. [x] Publish `activity.planned` / `activity.logged`; render `assessment` (written by the
   Supervisor, ADR-0039).
4. [x] Media: video upload types and limits; the Media Tool (poster frame, duration); ingestion
   routing by type; display and playback in the UI.
5. [x] Frontend: Farm work screen (upcoming, past, log, plan, repeat); Home "Farm work due";
   assessment badges and banners; videos in history and chat.
6. [x] Garden-level proposal confirm/reject for activity changes (shared with ADR-0039); recent
   activities in the orchestrator's garden context.
7. [x] Seeded in dev (Coconut Plot): the humic log + 21–28-day schedule; the 15 Nov spray.
8. [x] Docs: data-architecture (Activity, ActivityLog, Media video fields), architecture (endpoints,
   screens, specialist), stories CP-06, backlog.
9. [ ] Dev verification (pending deploy):
   - log "humic drench" and see it assessed;
   - schedule it every 21–28 days and see it due;
   - log a spray two days after a drench and see a caution with a conflict;
   - attach a short video and see its poster frame.
