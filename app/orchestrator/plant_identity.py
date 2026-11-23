"""Plant identity from a photo (ADR-0034 §1–2) — pure logic, no AWS calls, so it is unit-testable
on its own and deterministic: the model only *reads* the stake; these rules decide what happens.

- `parse_stake_read` turns the `plant_identifier` specialist's reply into a `StakeRead`, failing
  safe (anything unparseable means "no stake read", never a guessed number).
- `decide` applies the identity policy to a read, given how stake numbers map to plants in this
  garden and which plant (if any) the user already chose (a goal's plant, or a task's plant).

Nothing here changes data. The orchestrator turns a `Decision` into either nothing, a note for its
model, or a *proposed* action the gardener confirms with a tap.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

MAX_ALTERNATIVES = 4

DecisionKind = Literal[
    "no_stake",  # nothing readable: identity unknown, no conflict
    "match",  # read agrees with the plant the user chose
    "confident",  # no plant chosen; a confident read of a plant that exists here
    "ask",  # no plant chosen; unsure read, offer candidates
    "mismatch",  # read disagrees with the plant the user chose — never automatic
    "missing_plant",  # a number inside the stake range with no plant yet
    "out_of_range",  # a number outside the garden's stake range — probably a misread
]


@dataclass(frozen=True)
class StakeRead:
    stake_visible: bool = False
    stake_no: int | None = None
    confidence: Literal["high", "low"] = "low"
    alternatives: tuple[int, ...] = ()
    reason: str = ""

    def as_record(self) -> dict:
        """Shape stored on the Media item (`stake_read`)."""
        return {
            "stake_visible": self.stake_visible,
            "stake_no": self.stake_no,
            "confidence": self.confidence,
            "alternatives": list(self.alternatives),
            "reason": self.reason[:120],
        }


NO_READ = StakeRead()


@dataclass(frozen=True)
class Candidate:
    stake_no: int
    plant_id: str | None


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    read: StakeRead
    plant_id: str | None = None  # the plant the read points at, when it exists
    stake_no: int | None = None  # the number read
    expected_plant_id: str | None = None
    expected_stake_no: int | None = None
    candidates: tuple[Candidate, ...] = field(default=())

    @property
    def conflicts(self) -> bool:
        return self.kind in ("mismatch", "missing_plant", "out_of_range") or (
            self.kind == "ask" and self.expected_plant_id is not None
        )


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().lstrip("#").isdigit():
        n = int(value.strip().lstrip("#"))
        return n if n > 0 else None
    return None


def parse_stake_read(text: str | None) -> StakeRead:
    """The identifier's reply -> StakeRead. Tolerates code fences or prose around one JSON object;
    anything else is treated as "no stake read" (fail safe, never a guessed number)."""
    if not text:
        return NO_READ
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return NO_READ
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return NO_READ
    if not isinstance(data, dict):
        return NO_READ
    stake_no = _as_int(data.get("stake_no"))
    visible = bool(data.get("stake_visible")) and stake_no is not None
    confidence = "high" if data.get("confidence") == "high" and visible else "low"
    alternatives: list[int] = []
    for alt in data.get("alternatives") or []:
        n = _as_int(alt)
        if n is not None and n != stake_no and n not in alternatives:
            alternatives.append(n)
    return StakeRead(
        stake_visible=visible,
        stake_no=stake_no if visible else None,
        # A "high" read that still lists alternatives is not unambiguous.
        confidence="low" if alternatives else confidence,
        alternatives=tuple(alternatives[:MAX_ALTERNATIVES]),
        reason=str(data.get("reason") or "")[:120],
    )


def decide(
    read: StakeRead,
    plant_for_stake: Callable[[int], str | None],
    *,
    expected_plant_id: str | None = None,
    expected_stake_no: int | None = None,
    stake_range: tuple[int, int] | None = None,
) -> Decision:
    """ADR-0034 §2 identity policy. `plant_for_stake` looks a number up in this garden's STAKE#
    index; `expected_*` is the plant the user already chose (goal or task), if any."""
    base = {
        "read": read,
        "expected_plant_id": expected_plant_id,
        "expected_stake_no": expected_stake_no,
    }
    if not read.stake_visible or read.stake_no is None:
        return Decision(kind="no_stake", **base)

    n = read.stake_no
    plant_id = plant_for_stake(n)
    candidates = tuple(Candidate(s, plant_for_stake(s)) for s in (n, *read.alternatives))
    found = {"plant_id": plant_id, "stake_no": n, "candidates": candidates}

    if expected_plant_id is not None:
        if plant_id == expected_plant_id:
            return Decision(kind="match", **base, **found)
        # An unsure read that could be the chosen plant's number is not evidence against it.
        if (
            read.confidence == "low"
            and expected_stake_no is not None
            and (expected_stake_no in read.alternatives)
        ):
            return Decision(kind="match", **base, **found)
        if plant_id is None:
            kind = "out_of_range" if _outside(n, stake_range) else "mismatch"
            return Decision(kind=kind, **base, **found)
        return Decision(kind="mismatch", **base, **found)

    if plant_id is None:
        kind = "out_of_range" if _outside(n, stake_range) else "missing_plant"
        if read.confidence == "low" and any(c.plant_id for c in candidates[1:]):
            kind = "ask"  # an alternative reading matches a real plant: just ask
        return Decision(kind=kind, **base, **found)
    if read.confidence == "high":
        return Decision(kind="confident", **base, **found)
    return Decision(kind="ask", **base, **found)


def _outside(n: int, stake_range: tuple[int, int] | None) -> bool:
    return stake_range is not None and not (stake_range[0] <= n <= stake_range[1])
