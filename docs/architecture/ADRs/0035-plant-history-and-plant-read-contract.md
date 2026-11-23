# ADR-0035: Plant history (dated photos + logs) and the plant read contract

**Status:** Accepted (2026-11-23, project owner) and built the same day (see Action Items for
build notes).
**Date:** 2026-11-23
**Deciders:** Project owner / lead engineer
**Stories:** CP-03 (plant history: model + write path), CP-05 (plant endpoint + plant-detail UI).
Shapes CP-04 (migration writes into it). Builds on ADR-0032 (`stake_no`), ADR-0033 (thumbnails,
analysis copies) and ADR-0034 (confirmed links and check-ins).

## Context

**What the owner expects:** a palm keeps a date-wise history of its photos and notes, so "how did
#13 change since August?" can be answered by opening #13.

**What exists today:**

- `Plant` has one hero photo (`Plant.media_id`). There is **no history and no plant page**: tapping a
  plant in the list opens "Check a plant", and the only route on `/plants/{plantId}` is `DELETE`.
- `Media` is keyed `MEDIA#{media_id}` (a random id), so there is no "photos of #13, by date" query.
  `Media.plant_id` records only the photo's *current* plant.
- ADR-0034 links photos to plants on confirm, and records completed check-ins on the task and as a
  garden `Event`. Nothing is recorded per plant.
- **Observed in `dev` on 2026-11-23:** a goal's photo was read as #13 and the link was confirmed;
  the data was right (`Goal.plant_id`, `Media.plant_id`, `plant_link_source: confirmed`), but the
  owner found nothing when opening #13. A stopgap (built with this draft) sets `Plant.media_id` to
  the newly confirmed photo, so the plant list's thumbnail changes. It does not give a history.
- **The archive to migrate (CP-04):** about 137 dated photos across 105 palms (`YYYY-MM-DD[_HHMM]`
  in the filename) and a per-plant status log `(date, healthy|ok|watch, note)`. Dates in the past
  must land in the right place, not at "upload time".
- `AppTable` is single-table with **no LSI** (only the `TasksDueIndex` GSI), so a garden partition
  has no item-collection size limit.

Adding an entity and access pattern, a new endpoint pair and a change to what a confirm writes is
architecturally significant.

## Decision

### 1. `PlantLog` entry: append-only, one per observation

New item in the garden partition:

| pk | sk | Attributes |
|---|---|---|
| `GARDEN#{garden_id}` | `PLANTLOG#{plant_id}#{observed_on}#{entry_id}` | `entry_id`, `plant_id`, `observed_on` (`YYYY-MM-DD`), `observed_at?` (ISO time when known), `kind`, `media_ids?` (≤ 4), `status?` (`healthy`\|`ok`\|`watch`), `note?` (≤ 1,000 chars), `source`, `goal_id?`, `task_id?`, `action_id?`, `retracts?`, `created_at` |

- **`kind`:** `photo` (a photo filed to the plant), `checkin` (a confirmed task check-in, with or
  without a photo), `log` (a note and/or status the gardener wrote), `retraction` (see §3).
- **`source`:** `user` | `confirmed` | `auto` | `migration`. This mirrors `Media.plant_link_source`,
  so every entry says how it got there.
- **Dated by observation, not upload:** `observed_on` comes from, in order of preference:
  1. a date the gardener gives;
  2. the photo's own capture time (§5);
  3. the archive filename (migration);
  4. the date of the write.
- **Keyed by `plant_id`, not the stake number:** renumbering a stake (ADR-0032) keeps the history.
- **Access pattern:** `Query pk = GARDEN#{g} AND begins_with(sk, PLANTLOG#{plant_id}#)` with
  `ScanIndexForward=False` returns newest first, oldest first otherwise. No scan and no new index.
- **Deterministic ids where the source has one,** so retries and re-runs don't duplicate entries.
  The Put is `attribute_not_exists(pk)`:
  - a confirmed action uses `entry_id = action_id`;
  - migration uses a hash of the archive path;
  - an auto/match link uses the `media_id`;
  - free-form logs get a uuid.

### 2. Who writes history: the same deterministic write paths as today

No model writes history directly. Entries are appended by:

| Write path | Entry |
|---|---|
| `confirmAction` → `complete_task` (task has a plant) | `checkin` with the photo (if any), `task_id`, the note; **in the same transaction** as the task update |
| `confirmAction` → `set_plant`, `set_goal_plant` with a photo, `create_plant` with a photo | `photo`, `source: confirmed`; same transaction |
| Orchestrator identity step: a `match` (the gardener's chosen plant, nothing contradicts it) or an auto-link (when `AUTO_LINK_ENABLED`) | `photo`, `source: user` / `auto` |
| `createGoal` with a `plantId` and photos | `photo`, `source: user` |
| **New** `POST /gardens/{gardenId}/plants/{plantId}/history` | `log` (`note` and/or `status`, optional `mediaIds`, optional `observedOn` not in the future) |
| CP-04 migration script | `photo` / `log`, `source: migration`, dated from the archive |

`Event` stays the **garden** activity feed. A check-in writes both an Event and a PlantLog entry,
because they answer different questions ("what happened in the garden today" vs "what happened to
#13").

### 3. Corrections are recorded, not rewritten

Moving a misfiled photo (`set_plant`, ADR-0034) appends a `photo` entry on the new plant and a
`retraction` entry on the old plant (`retracts: <entry_id>`, `media_ids: [...]`), in the same
transaction. Nothing is deleted. By default the read contract hides retracted entries and the
retraction markers; `?includeRetracted=true` shows the full audit trail. The dev case (the #10 photo
on #1's task) is fixed this way once shipped.

### 4. The plant's current state is denormalized onto `Plant`

Every history write also updates the `Plant` item, in the same transaction:
`latest_observed_on`, `latest_status?`, `media_id` (the latest photo; it stays the hero/thumbnail
field `listPlants` already reads), and `history_count`. The update is **conditional on
`observed_on >= latest_observed_on`** (or no latest yet), so backfilling an old photo never replaces
a newer one. This turns the stopgap rule ("the newest confirmed photo becomes the plant's photo")
into a date-aware rule. `listPlants` keeps reading only `PLANT#` items and starts returning
`latestStatus` and `latestObservedOn`.

### 5. Capture time from the photo itself

The Ingestion Lambda (ADR-0033) already opens every original. It also reads EXIF `DateTimeOriginal`
(when present and plausible: not in the future, not before 2000) into `Media.taken_at`, *before*
the analysis copy strips metadata. A history write uses it as `observed_on`. Photos without it
(screenshots, the PNG-renamed archive files) fall back as in §1.

### 6. Read contract: `GET /gardens/{gardenId}/plants/{plantId}`

```
200 {
  plant:   { plantId, species, variety, stage, stakeNo?, latestStatus?, latestObservedOn?,
             photoUrl?, historyCount },
  history: { entries: [ { entryId, observedOn, kind, status?, note?, source,
                          photos: [ { mediaId, thumbUrl, url } ], goalId?, taskId? } ],
             nextCursor? }
}
```

- Query parameters:
  - `limit`: default 20, max 50;
  - `cursor`: opaque; a base64 of the `LastEvaluatedKey`, rejected (`400`) unless its `sk` is under
    this plant's `PLANTLOG#{plant_id}#` prefix;
  - `order`: `newest` (default) | `oldest`;
  - `includeRetracted`: default false.
- Photo URLs are presigned per read: `thumbUrl` is the 512 px thumbnail, and `url` is the analysis
  copy, or the original when not processed (ADR-0033). Media for one page comes from a single
  `BatchGetItem`.
- `404` when the plant is not in this garden. Contract-first in `openapi.yaml` (ADR-0011); the
  same API-key tier as the rest of the client API (ADR-0021).

### 7. Plant-detail screen

A new route `/plants/:plantId`; tapping a plant in the list opens it, and "Check this plant" moves
there. The screen shows:

- **Header:** number, species, stage, latest status.
- **Timeline:** grouped by date, with photo thumbnails, notes and status chips, and "load more"
  via `nextCursor`.
- **Add a note / status:** posts to the history endpoint.
- **Before → after:** the oldest and the newest photo side by side.
- **Link back:** each check-in or goal entry links to its goal.

### 8. Deleting a plant

`deletePlant` keeps its meaning ("this plant should not exist", for example a mistaken entry) and
deletes the plant's history entries with it, in batches after the Plant/STAKE transaction. Media
items and S3 objects stay; they are garden-level. **A palm that died is not deleted:** its `stage`
becomes `removed` and its history is kept. The plant-detail screen offers that instead of delete.

## Options Considered

| Option | Access pattern | Fit | Verdict |
|---|---|---|---|
| **A. `PLANTLOG#{plant_id}#{date}#{id}` in the garden partition (chosen)** | One `Query` per plant, both orders, no index | Same partition and garden boundary as everything else (ADR-0013); no LSI, so no collection limit; transactional with Plant/Task/Action writes | **Chosen** |
| B. Separate partition `PLANT#{plant_id}` | Same `Query` | Spreads load, but breaks "everything about a garden is under `GARDEN#`" (garden-scoped reads, deletes, future tenancy checks) | Rejected |
| C. Sparse GSI on `Media` (`gsi2pk = PLANT#{id}`, `gsi2sk = date`) | One GSI query | Photos only, no log-only entries; GSI is eventually consistent; moving a photo rewrites the index silently (no audit) | Rejected |
| D. Derive history from `Event` + `Media` at read time | Filter the garden feed | A scan of the garden's events per plant view; no observation dates for migrated photos | Rejected |
| E. Mutable "plant timeline" document (one item per plant, list attribute) | `GetItem` | 400 KB item limit, lost-update races, no append-only audit | Rejected |

## Trade-off Analysis

A keeps one partition per garden. At this farm's scale (105 palms, a few photos a week) that is far
below DynamoDB's per-partition throughput, and with no LSI there is no size ceiling. The cost is
that every write which links a photo or completes a check-in now also writes a PlantLog entry and
updates the Plant, inside the transaction it already runs (2–4 more items, far below the
100-item limit). Append-only with retractions costs a little read-side filtering in exchange for a
full audit trail, which the owner's experience with OCR misreads asks for.

## Consequences

- **Easier:**
  - opening a palm shows everything that happened to it;
  - the migration has a real destination;
  - the CP-07 Supervisor can read a plant's recent entries without scanning;
  - every photo-plant link and check-in is traceable per plant.
- **Harder:**
  - confirm appliers, identity linking and goal creation each gain a history write;
  - `deletePlant` gains a batched cascade;
  - the Ingestion Lambda gains an EXIF read.
- **ADR-0033:** extended (`Media.taken_at`). **ADR-0034:** confirm writes now append history (as its
  "CP-03" consequence anticipated). **ADR-0032:** unchanged; history is keyed by `plant_id`.
- **The stopgap** (`Plant.media_id` set on confirm or link, built 2026-11-23) is replaced by §4's
  date-aware rule.
- **Existing dev data:** the few photos already linked (`Media.plant_id`) get history entries from a
  one-off, dry-run-first backfill (`scripts/backfill_plant_history.py`), dated from `taken_at` or
  `uploaded_at`.

## Action Items

**Amended by ADR-0037 (2026-11-23):** §2's "no model writes history directly" gains one
exception: Tendril's reply for a saved goal turn is written by code as a `log` entry with
`source: tendril`. It is clearly labelled and never sets a `status`.

**Build notes (2026-11-23), where the code refines the text above:**

- **§4, latest state:** the entry and `history_count` are written in the transaction; the
  date-guarded `latest_*` updates run right after it, each as its own conditional update
  (`latest_observed_on`, `latest_photo_on` + `media_id`, `latest_status_on` + `latest_status`).
  A "newer than" condition inside the transaction would cancel the whole write when an older
  photo is backfilled.
- **§3, retractions:** besides the `retraction` entry, the original entry gets a
  `retracted_media_ids` marker; its own content is untouched. This lets a history page hide moved
  photos without reading other pages.
- **Within one day,** entries sort by `entry_id`, not by time; `observedAt` is returned for
  display.
- **A check-in photo** joins the history once, as the confirmed `checkin` entry. The
  orchestrator's identity match does not also add a `photo` entry for it. A `checkin` entry's note
  is the gardener's note, else the task title.
- **Photos filed at goal creation** are dated by upload if ingestion has not yet read their
  capture time (ingestion runs asynchronously, usually within seconds).
- **`ok` status:** the plant list shows the `ok` status as the existing "Healthy" chip and `watch`
  as "Watch"; the plant page shows the raw status.

1. [x] Human review: approved 2026-11-23.
2. [x] `PlantLog` writes: a shared helper used by the confirm appliers, `createGoal`, the orchestrator's
   identity links and the history endpoint; the Plant denormalization (§4).
3. [x] `GET /gardens/{gardenId}/plants/{plantId}` and `POST …/history` (OpenAPI first).
4. [x] Ingestion: `Media.taken_at` from EXIF (the Image Tool returns `takenAt`).
5. [x] Frontend: plant-detail screen, route, list navigation, add-note form, before→after.
6. [x] `deletePlant` cascade; `stage: removed` (`PATCH /gardens/{gardenId}/plants/{plantId}`).
7. [x] Backfill script for already-linked dev photos (`scripts/backfill_plant_history.py`, dry run
   by default). [ ] CP-04 migration writes `source: migration`.
8. [x] Docs: `data-architecture.md` (PlantLog, Plant, Media), `architecture.md` endpoint table, stories
   CP-03/CP-05, backlog.
9. [ ] Dev verification (pending deploy + backfill): #13's page shows the "tracking" photo; moving the #10 photo off #1 shows a
   retraction on #1 and a photo on #10.
