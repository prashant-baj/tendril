# ADR-0034: Plant identity from photos, and check-ins as a confirmed chat turn

**Status:** Accepted (2026-11-23, project owner). Owner decisions: auto-link only when wrong
high-confidence reads are **below 1%** on the archive evals (§5); a typed "yes" is **not** consent,
only the Confirm tap changes data (§3); the per-task check-in returns `202` with a proposal instead
of `200 done`, frontend updated in the same release (§3). Built after ADR-0033.
**Date:** 2026-11-23
**Deciders:** Project owner / lead engineer
**Stories:** CP-08 (upload → auto-map to a plant), extended with conversational check-in (owner
request, 2026-11-23). Depends on ADR-0033 (analysis copies) and ADR-0032 (per-garden `stake_no`).

## Context

**What the owner expects:** when a photo arrives without a plant selected, the system reads the
number on the plant's stake and files it to the right plant, and check-ins happen in the goal's chat,
where questions like "Is this #10?" can be asked and answered.

**What exists today:**

- **Check-in** (`POST …/tasks/{taskId}/checkins`) requires a photo and marks the task `done`
  **immediately**, in the same write. There is no identity check and no way to confirm or correct.
- **Chat** (`POST …/goals/{goalId}/messages`) is text only; a message cannot carry a photo or
  refer to a task.
- **Nothing reads stake numbers.** Vision diagnoses; it never identifies which plant a photo shows.
  A goal's plant is whatever the user picked, or nothing.
- **Plan approval** (PA-02) set a useful precedent: the model *proposes*, and the human's approval is
  a separate, synchronous, deterministic write (`approvePlan`), with no model call.
- Strands' native human-in-the-loop interrupt only gates a tool call (`beforeToolCall`, ADR-0015).
  Nothing here is a tool call worth gating today.

**Observed in `dev` on 2026-11-23 (garden "Coconut Baug"):**

- A goal was submitted with the #10 photo and **no plant selected**; it could not be linked to any
  plant.
- On palm **#1**'s task "Check drip irrigation", the check-in photo attached was the **#10** photo.
  It was accepted and the task marked done, so a #10 photo now sits in #1's history. Nothing
  noticed.

**Lessons from the owner's existing tracker:** stake-number reads from photos are unreliable
(earlier OCR mis-mappings), so a read must never silently assign identity on doubt. With a fixed set
of 105 plants, an unknown number is more often a misread than a new plant. A cheap "move this photo
to #NN" correction must always exist.

Changing the chat and check-in contracts, adding a confirmation entity and endpoint, and letting a
model's reading influence data identity are architecturally significant.

## Decision

### 1. A dedicated, deterministic stake-reading step

- A new registry specialist **`plant_identifier`** (`agents/registry/plant_identifier.json`, same
  template, the vision-capable model, its own externalized prompt and guardrail). It returns strict
  JSON: `{ "stake_visible": bool, "stake_no": int | null, "confidence": "high" | "low",
  "alternatives": [int], "reason": str }`.
- It is marked **`"internal": true`** in the registry: `AgentCoreStack` still deploys it, but the
  orchestrator does **not** offer it to its model as a tool. Orchestrator **code** calls it whenever a
  photo arrives (goal intake, chat message, check-in), before the model turn, so identification is
  never left to the model's discretion.
- It reads the **analysis copy** (ADR-0033). The result is stored on the `Media` item:
  `stake_read: {stake_no, confidence, alternatives}`.

### 2. Identity policy (the data-integrity control)

The orchestrator turns the read into an identity decision with plain rules, using the garden's
`STAKE#` lookup (ADR-0032) and stake range (`context.stakeRange`):

| Situation | Result |
|---|---|
| High confidence, number exists in this garden, **no conflicting plant** (none selected, or the selected/task plant matches) | **Auto-link** the photo (and a goal without a plant) to that plant. The reply says so, with a **Change** option: "Filed under #10." |
| Low confidence, or alternatives (for example #71 or #7) | **Ask:** a proposed `set_plant` action listing the candidates. Nothing is linked until confirmed. |
| Read number **differs** from the selected plant or the task's plant | **Never auto.** Ask: "This photo shows #10, but the task is for #1. Is it #10's photo?" |
| Number not in this garden, or outside the stake range | **Never auto-create.** Ask whether it's a misread (offer the closest existing numbers) or a new plant (a flagged `create_plant` proposal). |
| No stake visible | Ask the user to pick the plant, or proceed without one if the goal is garden-wide. |

Every photo-to-plant link records its source on the `Media` item, `plant_link_source`:
`user` | `auto` | `confirmed`. A **move photo to #NN** action is always available, from the chat and
from the photo itself.

### 3. Check-ins become a chat turn, confirmed with a tap

- **Chat messages carry photos and an optional task:** `POST …/goals/{goalId}/messages` accepts
  optional `mediaIds` (up to 4) and an optional `taskId`. Text is optional when there is a photo. A
  message with a `taskId` is a **check-in**; a text-only check-in ("mulching done today") is allowed,
  so a photo is no longer required.
- **The orchestrator proposes, never mutates.** For a check-in or a photo it runs the identity step
  (§1–2), then its normal turn. The turn's structured output gains an optional `proposed_action`:
  - `complete_task {taskId, mediaId?, note?}`
  - `set_plant {mediaId, plantId, candidates?}` (also used for "move photo")
  - `set_goal_plant {plantId}`
  - `create_plant {stakeNo}` (flagged; only after an out-of-range or unknown read)
- **Proposals are validated by code before they are stored:** the task exists in this goal and is
  `pending`, the plant and media belong to this garden, and the stake number really is free. An
  invalid proposal is dropped and logged, never shown.
- **Stored as an `Action` item** `GARDEN#{id}/ACTION#{goal_id}#{action_id}` with `type`, `params`,
  `summary` (the sentence shown to the user), `status` (`proposed` | `confirmed` | `rejected` |
  `superseded`), and `message_id`. A newer proposal supersedes older open ones on the same goal.
- **Confirming is a separate, deterministic endpoint** (the PA-02 pattern):
  `POST /gardens/{gardenId}/goals/{goalId}/actions/{actionId}/confirm` and `…/reject`. It is
  synchronous, makes no model call, only acts on `proposed` actions, and is idempotent. `confirm`
  applies the write: for `complete_task`, the task is set to `done`, the media is linked and the
  `TasksDueIndex` attributes are cleared (exactly today's check-in write), and a `task.checkin`
  Event is recorded; for `set_plant`, `Media.plant_id` is set with `plant_link_source: confirmed`.
- **Free-text answers** ("no, it's #12") start a new turn and produce a new proposal. **Only the
  confirm endpoint changes identity or task state.** A typed "yes" gets a reply pointing at the
  Confirm button rather than being interpreted as consent. This is a deliberate trade-off: one extra
  tap in exchange for a model never changing data on its own reading of a sentence.
- **The per-task photo button stays as a shortcut** and goes through the same flow (it posts a
  check-in message with `taskId` + `mediaId`). `postTaskCheckin` keeps its route but becomes a thin
  wrapper: it returns `202` with the proposal instead of `200 done`. The frontend changes in the same
  release.

### 4. Goal intake without a plant

`handle_goal_submitted` runs the same identity step on the goal's first photo. Auto-link (§2)
sets `Goal.plant_id`; tasks the orchestrator then proposes inherit it, as today. Otherwise the first
reply carries a `set_goal_plant` proposal.

### 5. Measured, not assumed

Stake-reading quality is checked with **eval scenarios built from the owner's archive**, where the
folder name of each photo is the human-verified plant number. Track exact-match accuracy, how often
a high-confidence read is wrong (the number that matters for auto-link), and how often a number is
missed. The auto-link rule ships only when **wrong high-confidence reads are below 1%** on that set
(owner decision, 2026-11-23); until then every read goes through confirmation.

## Options Considered

| Option | Integrity | UX | Verdict |
|---|---|---|---|
| **A. Model proposes, deterministic confirm endpoint; code-run identity step (chosen)** | Identity and task state change only through validated, confirmed actions | One tap to confirm; questions asked in the chat | **Chosen** |
| B. Strands `HumanInTheLoop` interrupts | Strong, but needs mutating tools to gate and a cross-invocation interrupt/resume flow | Same chat feel | Rejected for now; revisit with ADR-0015 if mutating tools appear |
| C. The model mutates directly via tools ("mark done", "move photo") | A misread or misunderstood sentence changes data | Fewest taps | Rejected |
| D. Separate identity screen outside the chat | Strong | Breaks the conversation; two places to answer questions | Rejected |
| E. Always auto-assign the read number | Misreads silently corrupt plant histories (seen in the owner's tracker) | No questions | Rejected |
| F. Let the orchestrator's model decide when to call stake reading (as a normal tool) | Identification can be skipped | Same | Rejected: identity must not depend on model discretion |

## Trade-off Analysis

A adds an entity (`Action`), two endpoints, one internal specialist and a contract change to
messages and check-ins. It keeps the property that matters most for a plant-by-plant history:
**no model output changes identity or task state without a validated, human-confirmed action**,
except auto-links that pass the measured high-confidence rule and stay one tap from correction.
The cost is an extra tap per check-in and a slower check-in (the identity step adds one specialist
call, roughly 5–10 s), acceptable because check-ins are asynchronous chat turns anyway.

## Consequences

- **Easier:** photos without a selected plant get filed correctly; mismatches like #10-on-#1 are
  caught before they're saved; check-ins work with or without a photo; every link has an audit
  trail (`plant_link_source`, `Action` history).
- **Harder:** more moving parts in the orchestrator turn; a proposal lifecycle (supersede, expiry) to
  maintain; the frontend chat gains attachments, task selection and Confirm/Change buttons; the
  check-in API response changes from `200 done` to `202 proposed`.
- **ADR-0012:** registry entries gain an optional `internal` flag (deployed, not offered as a tool).
- **ADR-0013:** unchanged; `plant_identifier` gets photo content only through a presigned URL.
- **ADR-0015:** unchanged; this is not a tool-call interrupt. If mutating tools ever appear, gating
  them with Strands interrupts is the natural next step.
- **CP-03 (plant history):** once it exists, a confirmed `set_plant` / `complete_task` also appends a
  history entry; until then the link lives on `Media.plant_id` and the task.
- **Existing data:** the #10 photo on #1's task can be corrected with the new move action once
  shipped.

## Action Items

**Amended by ADR-0037 (2026-11-23):** for goals that end as `InformationSaved`
(information only, no plan), a confident read of an existing stake number files the goal and
photo directly, even while `AUTO_LINK_ENABLED` is off. Goals with a plan still ask (§5).

Built 2026-11-23. Notes from the build: identity rules live in `app/orchestrator/plant_identity.py`
(pure, unit-tested); confident reads still become a `set_goal_plant` proposal until the CDK
context flag `auto_link_enabled=true` sets `AUTO_LINK_ENABLED` on the orchestrator (only after the
eval passes); the model's `proposed_action` covers text-only cases ("mulching done", "it's #12")
and is validated by code like every other proposal; `create_plant` copies species/variety/stage
from an existing numbered plant in the garden.


1. [x] Human review: approved 2026-11-23; auto-link accuracy target set to below 1% wrong
   high-confidence reads (§5).
2. [x] Registry `internal` flag (AgentCoreStack manifest + orchestrator tool building); the
   `plant_identifier` entry, prompt and guardrail.
3. [x] Orchestrator: identity step on goal intake, chat photos and check-ins; identity policy;
   `proposed_action` in the structured output; proposal validation; `Action` writes.
4. [x] Client API + OpenAPI: messages with `mediaIds`/`taskId`; `confirm`/`reject` endpoints;
   `postTaskCheckin` → `202` proposal; `getGoalDetail` returns open actions.
5. [x] Frontend: chat attachments + task picker; Confirm/Change on proposals; "move photo" action.
6. [x] Eval script from the archive (`scripts/eval_plant_identifier.py`, folder names as ground
   truth; reports wrong-high-confidence rate vs the 1% gate); identity decisions logged
   (`stake_read`, `identity_decision`). [ ] Run it against the deployed runtime.
7. [x] Docs: `data-architecture.md` (Message, Media, Action), `architecture.md` §4.1/§7, stories
   (CP-08 rewritten), backlog.
8. [ ] Dev verification (pending deploy): the #10 photo without a plant gets "Filed under #10" (or a confirm
   prompt); the #10 photo on #1's task triggers the mismatch question; confirming completes the task.
