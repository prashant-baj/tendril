# ADR-0037: Saved information goes into the plant's history, without a confirm tap

**Status:** Accepted (2026-11-23, project owner) and built the same day. Owner direction: "Saved
information should appear in the plant history" and "Saving just information shouldn't need a
HITL".
**Date:** 2026-11-23
**Deciders:** Project owner / lead engineer
**Amends:** ADR-0035 §2 ("No model writes history directly") and, for information-only goals,
ADR-0034 §5 (the auto-link gate). Builds on ADR-0036 (`InformationSaved`).

## Context

**What happened in `dev` on 2026-11-23:** a "Track this" goal with a photo of palm #8 ended as
`InformationSaved`.

- The stake was read as #8 with high confidence, but auto-link is off (ADR-0034 §5: on only after the
  archive eval shows under 1% wrong confident reads). So a "Link this goal to #8?" card was shown and
  is still unconfirmed.
- The photo is therefore not filed to #8, and #8's history shows nothing.
- Even after confirming, the history would show only the photo, not what Tendril concluded about it.

**What the owner expects:** information that is only being *saved* (nothing to approve, nothing
changes on a task) lands in the plant's history directly. A confirmation step is for changes, not
for recording observations.

**Why the earlier ADRs blocked this:**

- **ADR-0035 §2:** entries come only from deterministic write paths, and no model writes history.
- **ADR-0034 §5:** a photo is never filed to a plant on a model's stake read alone until the
  accuracy eval passes.

Both were set to keep misreads out of plant histories. Relaxing them is a data-integrity decision,
hence this ADR.

## Decision

### 1. A saved turn writes a note to the plant's history

When a turn ends with no plan and the goal has none (`InformationSaved`, ADR-0036), and the goal
has a plant, the orchestrator appends a `log` entry to that plant's history:

- **`note`:** Tendril's reply for that turn (≤ 1,000 characters).
- **`source`:** a new value, **`tendril`**, so it is always visibly Tendril's words, not the
  gardener's.
- **`media_ids`:** the turn's photo.
- **Date:** the photo's date (ADR-0035 §1), else today.
- **`goal_id`:** the goal.
- **`entry_id`:** the assistant message id, so each saved turn adds one entry, never overwrites one,
  and a retry is a no-op.
- **It never sets `status`** (healthy/ok/watch). Only the gardener sets a plant's status, so a model
  reading never changes it.
- **One entry, not two:** the goal photo's separate `photo` entry is folded into this note. The
  `photo` entry is skipped when the note carries the photo.

No confirmation is asked: it adds information and changes nothing else.

### 2. Information-only goals are filed to the read plant without a tap

For a goal **without a plant** whose turn ends `InformationSaved`:

- **High-confidence read of an existing stake number** (the `confident` decision, ADR-0034 §2):
  - the goal and photo are filed to that plant directly, with `plant_link_source: auto`;
  - the note from §1 is written;
  - the reply says so ("Saved to #8's history"), and offers **Move** if it's the wrong palm.
  - This applies **even while `AUTO_LINK_ENABLED` is off.**
- **Anything less certain still asks:** a low-confidence read, alternatives, a mismatch with a chosen
  plant, an unknown or out-of-range number. The note is then written when the gardener confirms
  (the confirm applier writes it).
- **Goals that end with a plan are unchanged:** the link is still a Confirm card until the eval
  passes. A plan creates tasks against the plant, which is a change, not just information.

**Order of steps:** the stake read happens before the model turn, but whether the goal ends with a
plan is only known after it. So for a confident read on a goal without a plant, the orchestrator
holds the link decision until the turn ends:

- no plan: file the photo and write the note;
- a plan: propose the "Link to #NN?" card as today.

The model is told the read either way ("the stake reads #8").

**Moving a misfiled note** uses the existing correction path (ADR-0035 §3): "move to #NN" appends
the entry to the right plant and a retraction on the wrong one. Nothing is deleted.

### 3. What the gardener sees

- **Plant page:** Tendril's notes appear in the timeline as "Note from Tendril", with the photo and a
  link to the goal.
- **Goal chat:** the reply mentions where it was saved, with a Move option when it was filed by
  stake read.

## Options Considered

| Option | Integrity | Taps | Verdict |
|---|---|---|---|
| **A. Write saved notes directly; file confident reads for information-only goals (chosen)** | A misread can file a note under the wrong palm until moved; always labelled `tendril` / `auto` and movable | None for the common case | **Chosen** (owner direction) |
| B. Write the note only after the gardener confirms the plant link (first draft) | No unconfirmed links | One tap per saved check-in | Rejected by the owner: information shouldn't need a HITL |
| C. Keep the eval gate for everything (ADR-0034 as is) | Strongest | One tap per check-in until the eval passes | Rejected for information-only goals; kept for goals with a plan |
| D. Store Tendril's notes only on the goal, never in plant history | No model text in history | — | Rejected: the plant page is where the owner looks |

## Trade-off Analysis

The risk is a misread stake number filing a note under the wrong palm without anyone confirming it.
The archive's history of OCR mis-mappings says this will happen sometimes. It is contained because:

- only **high-confidence** reads of numbers that **exist in the garden** are filed this way;
- every such entry says `auto` and `tendril`;
- the reply names the plant;
- Move is one tap and leaves an audit trail;
- nothing about tasks or status changes.

The eval (`scripts/eval_plant_identifier.py`) still measures the real error rate. If it shows
confident reads are wrong often, this decision should be revisited.

## Consequences

- **Easier:** check-ins and "track this" photos build up each palm's history with no extra taps;
  the plant page shows what Tendril observed.
- **Harder:**
  - model-written text now lives in plant history, so it is labelled and kept separate from the
    gardener's own notes;
  - two link policies exist: information-only goals file confident reads, goals with a plan ask.
- **ADR-0034:** §5's gate still applies to goals that get a plan and to everything below high
  confidence. **ADR-0035:** a `source` value is added (`tendril`), and §2 gains the saved-note write
  path. **ADR-0036:** unchanged.

## Action Items

**Build notes (2026-11-23):**

- **One place for the post-turn work:** the orchestrator's `_finish_turn` applies every goal
  turn (intake and chat). A confident read on a goal without a plant is kept on the
  `IdentityOutcome` until the turn ends; identity matches defer their history entry the same
  way, so a saved turn produces **one** entry (Tendril's note with the photo).
- **When a note is written:** a saved turn writes a note when it had a photo or the model's
  `outcome` was `noted`. A text-only clarifying question (`outcome: question`) writes none,
  so a plant's history doesn't fill with chat.
- **Photos already in the history:** if the photo already has its own entry (e.g. the
  gardener filed it when creating the goal), the note is written without repeating it.
- **The confirm path:** `confirmAction` on `set_goal_plant` for an `InformationSaved` goal
  writes the goal's latest Tendril reply as the note, carrying the photo, instead of a plain
  photo entry (this fixes the pending #8 case).
- **"Move" is conversational:** the reply says "If that's the wrong palm, tell me its number".
  The model's `link_plant` proposal then goes through the existing confirm + retraction path.
- **With `AUTO_LINK_ENABLED` on,** goals with a plan are also filed directly (ADR-0034 §5,
  unchanged).

1. [x] Human review: approved 2026-11-23.
2. [x] Orchestrator: saved-turn note; information-only confident auto-file; reply wording with Move;
   tests (including "a plan still asks").
3. [x] Confirm applier: write the saved note when a pending link for an `InformationSaved` goal is
   confirmed.
4. [x] API/OpenAPI: `source: tendril` in `PlantLogEntry`; frontend "Note from Tendril".
5. [x] Docs: data-architecture PlantLog row; ADR-0034/0035 cross-references.
6. [ ] Dev verification (pending deploy): a #8 "Track this" photo ends as Saved and appears on #8's page with
   Tendril's note, with no tap.
