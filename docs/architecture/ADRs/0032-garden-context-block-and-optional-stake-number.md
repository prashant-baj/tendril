# ADR-0016: Garden `context` block, its injection into agent turns, and optional per-garden stake numbers

**Status:** Accepted (2026-11-23, project owner; out-of-range stake numbers accepted but flagged)
**Date:** 2026-11-23
**Deciders:** Project owner / lead engineer
**Stories:** CP-01, CP-02 (`docs/stories/coconut-plantation.md`, roadmap Phase A)

## Context

Tendril is onboarding a real 105-palm coconut plantation (`docs/coconut-plantation-roadmap.md`).
Two gaps block Phase A:

1. **Whole-farm facts have nowhere to live, and no agent sees them.** The Garden item
   (`GARDEN#{id}/METADATA`) holds only `name`, free-text `geolocation`, optional `vision` and
   `media_id`. Soil (heavy calcareous/saline clay), irrigation (16 mm drip), intercrop (veg strip,
   chana in the alleys), layout (25×25 ft, 1.5 acre) and the team's notification numbers are
   missing. Worse, **no garden facts reach any agent today**: the orchestrator's prompt is just
   the goal text, and `_make_specialist_tool` sends specialists only `{prompt, gardenId,
   imageUrl?}`. `climate_zone` is documented in `data-architecture.md` but never written.
2. **Palms are identified by a numbered wooden stake, but Plant has no such field.** Plant
   identity is a random `plant_id`. The owner has decided the stake number is **optional** (a
   palm may have none), is the farm's own label (Tendril never generates it), and must be unique
   **within a garden** when present (owner review, 2026-11-23).

Both are architecturally significant under CLAUDE.md: a new data shape on a core entity, a new
cross-cutting pattern (garden context in every agent turn), a new uniqueness constraint, and
public-contract changes to the Client API.

## Decision

### 1. `context` is a structured map on the Garden item

Add an optional `context` attribute (a DynamoDB map) to `GARDEN#{id}/METADATA`, plus an optional
top-level `climate_zone` string. Shape (API camelCase / storage snake_case):

| Field | Type | Notes |
|---|---|---|
| `soil` | string | e.g. "Heavy black calcareous/saline clay; lime-locked Fe/Mg/N chlorosis" |
| `irrigation` | string | e.g. "16 mm drip off well water, emitter ~1.5 ft off collar" |
| `intercrop` | string | e.g. "Veg strip (brinjal, marigold) north edge; chana planned in alleys" |
| `layout` | string | e.g. "25×25 ft square, 1.5 acre west block; 23 Tall on east fence (border)" |
| `notes` | string | anything else the agents should know |
| `stakeRange` | `{min: int, max: int}` | optional valid stake-number range for the main block (e.g. 1–105) |
| `notificationRecipients` | `[{name: string, e164: string}]` | E.164-validated; stored now, linked to users in CP-11, messaged in CP-09 |

- All fields are optional; text fields are capped at 1,000 chars each, at most 10 recipients,
  so the item stays well under DynamoDB's 400 KB limit and the injected prompt stays small.
- **Write paths:** `createGarden` accepts optional `context` and `climateZone`; a new
  **`PATCH /gardens/{gardenId}`** (`updateGarden`) replaces `context` as a whole and/or sets
  `climateZone`/`vision`. Partial-field merge is deliberately not supported (one shape, one write).
- **Read path:** `getGarden` returns `context` and `climateZone` when set.

### 2. The orchestrator injects garden context into every turn

On each turn the orchestrator reads the Garden item (it already has direct `AppTable` IAM,
ADR-0013) and renders a compact `<garden_context>` text block (name, location, climate zone,
vision, soil, irrigation, intercrop, layout, notes, stake range). It is used two ways:

- **Appended to the orchestrator's system prompt**, which is rebuilt on every invocation. Because
  it is not a message, it is never written into the `SnapshotSessionManager` history, so it is
  always current and never duplicated across resumed turns.
- **Prefixed onto every specialist prompt** inside `_make_specialist_tool`. Specialists need no
  code or template change: the context arrives as part of the prompt, like any other input
  (ADR-0013's "data arrives as tool-call input").

**`notificationRecipients` are never injected.** Phone numbers are personal data that no model
needs to reason with. They stay in DynamoDB (encrypted at rest) and only the future notifier reads
them.

A missing or unreadable Garden item degrades to no context block (logged, never fails the turn).

### 3. Optional per-garden stake number, enforced by a lookup item

- `Plant.stake_no` is an optional positive integer. `plant_id` stays the identity and the Plant
  key (`GARDEN#{id}/PLANT#{plant_id}`) is unchanged.
- Uniqueness is enforced with a **lookup item** `GARDEN#{id}/STAKE#{stake_no:05d}` (holding
  `plant_id`), written in the **same transaction** as the Plant with
  `attribute_not_exists(pk)`. A duplicate makes the transaction fail, mapped to **409 Conflict**.
- `deletePlant` deletes the lookup item in the same transaction as the Plant.
- `createPlant` also accepts an optional `stage` (default `"new"`, replacing today's hardcode)
  and `stakeNo`.
- If `context.stakeRange` is set, a stake number outside it is **accepted but flagged**
  (`outOfRange: true` in the response). Rejecting it would block real new plantings, and CP-08
  (auto-map) owns the stricter guarded-create policy.

### 4. Bulk-add endpoint

New **`POST /gardens/{gardenId}/plants/bulk`** (`bulkCreatePlants`): `{species, variety?, stage?,
stakeFrom, stakeTo}` (max 200 per call). For each stake number it runs the same transactional
write as `createPlant`. **Idempotent:** a stake number that already exists is reported under
`skipped`, never duplicated or overwritten. Response: `{created: [{plantId, stakeNo}], skipped:
[stakeNo]}`. The CP-04 migration script and the UI can both use it.

### 5. `listPlants` at plantation scale

`listPlants` returns `stakeNo` (when set), orders by stake number ascending with unnumbered plants
last, follows `LastEvaluatedKey` so it never silently truncates, and resolves hero photos with
one `BatchGetItem` instead of one `GetItem` per plant. Client-facing pagination is deferred: 105
plants is one small response.

## Options Considered

**Where context lives**
- **A. Structured map on the Garden item (chosen):** one read the orchestrator already makes,
  atomic with the garden, typed fields the API can validate.
- B. A separate `GARDEN#{id}/CONTEXT` item: isolates churn, but adds a second read per turn and
  a second write on create, for data that is small and changes rarely.
- C. Keep it as free-text inside `vision`: no schema change, but nothing can validate phone
  numbers or the stake range, and agents can't tell facts from aspirations.

**How agents receive it**
- **A. System-prompt suffix for the orchestrator + prompt prefix for specialists (chosen):** no
  specialist code change, works with every model and guardrail, never pollutes session history.
- B. A new `gardenContext` field in the specialist payload: cleaner separation, but every
  specialist template must change and redeploy to read it.
- C. Rely on Garden Memory (KB retrieval) only: retrieval is similarity-based and not
  guaranteed to surface the soil profile on every turn; the facts are too important to be
  best-effort. (CP-04 still seeds the KB as a complement.)

**How stake uniqueness is enforced**
- **A. Lookup item in the same transaction (chosen):** strongly consistent, atomic, single
  partition, standard DynamoDB uniqueness pattern.
- B. A GSI on `stake_no`: GSIs are eventually consistent and cannot enforce uniqueness, so a race
  could create duplicates.
- C. Deterministic key `PLANT#{stake_no}`: breaks the "optional" decision (plants without a
  stake need another key scheme) and makes renumbering a stake rewrite the plant's history.

## Trade-off Analysis

The chosen options add no new service, datastore or index. The cost is one extra item per
numbered plant (`STAKE#`) and a slightly longer prompt per agent call (a few hundred tokens of
garden context). That token cost is the point: it's what lets advice account for calcareous clay
and drip irrigation instead of generic hobby-garden answers. Whole-block replace on `PATCH` is
less flexible than field-level merge but avoids partial-update ambiguity, and the block is small
enough to resend.

## Consequences

- **Easier:** every agent reasons with the real farm; plants can be addressed by stake number;
  105 palms onboard in one call; CP-08 auto-map and CP-04 migration get a ready identity lookup.
- **Harder:** `createPlant`/`deletePlant` become transactions whenever a stake number (or, as
  before, a photo) is involved; an unnumbered plant without a photo is still a single write. The
  orchestrator does one extra `GetItem` per turn; `deletePlant` does one extra `GetItem` to find
  the plant's stake number.
- **Contract:** additive changes to `Garden`, `GardenCreateRequest`, `Plant`, `PlantCreateRequest`;
  new `updateGarden`, `bulkCreatePlants`; `createPlant` gains a 409 response; CORS preflight
  allows `PATCH`.
- **Privacy:** notification numbers are personal data stored on the Garden item, never sent to a
  model, never logged.

## Action Items

1. [x] `app/api/openapi.yaml`: new schemas/operations above (+ `PATCH` in CORS).
2. [x] `app/api/garden_handler.py`: context validation, `updateGarden`, stake lookup item,
   `bulkCreatePlants`, `listPlants` ordering/pagination/batch photo lookup, transactional delete.
   Client API Lambda timeout raised 10s → 29s for bulk create (`client_api_stack.py`).
3. [x] `app/orchestrator/orchestrator.py`: load + render garden context; system-prompt suffix and
   specialist prompt prefix; recipients excluded. (Verified: Strands' snapshot `"session"`
   preset does not persist `system_prompt`, so the suffix is never duplicated on resume.)
4. [x] Frontend models: `Garden.context`/`climateZone`, `Plant.stakeNo`; a numbered plant's name
   reads "#74 Coconut" on the existing card/row.
5. [x] Tests: handler (contract-validated), orchestrator injection (incl. recipients excluded),
   frontend service specs; infra tests unchanged and green.
6. [x] Docs: `data-architecture.md` §2 (Garden, Plant, Stake lookup), `architecture.md` §4.1
   endpoint table; CP-01/CP-02 marked in stories + backlog.
7. [ ] Dev smoke test: create/patch the Bramhangaon garden, bulk-add #1–105, confirm a specialist
   prompt contains the garden context in CloudWatch. **Pending deploy.**
