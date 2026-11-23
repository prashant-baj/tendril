"""Plant history (ADR-0035): dated, append-only entries per plant — pure logic, no AWS calls.

An entry is one observation of one plant, stored in the garden partition:

    pk = GARDEN#{garden_id}
    sk = PLANTLOG#{plant_id}#{observed_on}#{entry_id}

so one `Query` on the `PLANTLOG#{plant_id}#` prefix returns a plant's history by date, newest
first with `ScanIndexForward=False`. `garden_handler` does the DynamoDB I/O; this module builds the
items, decides the observation date, filters retracted photos and encodes the page cursor.

Corrections are recorded, never rewritten (§3): moving a photo away from a plant appends a
`retraction` entry on that plant and marks the original entry with `retracted_media_ids` (a marker,
the entry's own content is untouched), so a page can hide moved photos without reading other pages.
"""

from __future__ import annotations

import base64
import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

KINDS = ("photo", "checkin", "log", "retraction")
# `tendril`: a note in Tendril's own words for a saved goal (ADR-0037) — never sets a status.
SOURCES = ("user", "confirmed", "auto", "migration", "tendril")
STATUSES = ("healthy", "ok", "watch")
NOTE_MAX_CHARS = 1000
MAX_MEDIA_PER_ENTRY = 4
DEFAULT_PAGE = 20
MAX_PAGE = 50
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def prefix(plant_id: str) -> str:
    return f"PLANTLOG#{plant_id}#"


def entry_sk(plant_id: str, observed_on: str, entry_id: str) -> str:
    return f"{prefix(plant_id)}{observed_on}#{entry_id}"


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def observed_on_for_media(media: dict[str, Any] | None, fallback: str | None = None) -> str:
    """The date a photo shows (§1): its camera capture time (`taken_at`, ADR-0035 §5), else when it
    was uploaded, else `fallback`, else today."""
    for field in ("taken_at", "uploaded_at"):
        value = (media or {}).get(field)
        if isinstance(value, str) and DATE_PATTERN.match(value[:10]):
            return value[:10]
    return fallback or today()


def observed_at_for_media(media: dict[str, Any] | None) -> str | None:
    value = (media or {}).get("taken_at")
    return value if isinstance(value, str) and len(value) > 10 else None


def parse_observed_on(raw: Any) -> tuple[str | None, str | None]:
    """A gardener-given date: `YYYY-MM-DD`, a real date, not in the future. (value, error)."""
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not DATE_PATTERN.match(raw):
        return None, "observedOn must be a date (YYYY-MM-DD)"
    try:
        value = date.fromisoformat(raw)
    except ValueError:
        return None, "observedOn must be a real date"
    if value.isoformat() > today():
        return None, "observedOn cannot be in the future"
    return raw, None


def new_entry(
    garden_id: str,
    plant_id: str,
    *,
    kind: str,
    source: str,
    observed_on: str,
    entry_id: str | None = None,
    observed_at: str | None = None,
    media_ids: list[str] | None = None,
    status: str | None = None,
    note: str | None = None,
    goal_id: str | None = None,
    task_id: str | None = None,
    action_id: str | None = None,
    retracts: str | None = None,
) -> dict[str, Any]:
    """One PlantLog item (resource-layer shape). `entry_id` should be deterministic when the source
    has an id (an action, a photo, an archive file) so a retried write is a no-op (§1)."""
    if kind not in KINDS or source not in SOURCES:
        raise ValueError(f"unknown kind/source: {kind}/{source}")
    entry_id = entry_id or uuid.uuid4().hex
    item: dict[str, Any] = {
        "pk": f"GARDEN#{garden_id}",
        "sk": entry_sk(plant_id, observed_on, entry_id),
        "entry_id": entry_id,
        "plant_id": plant_id,
        "observed_on": observed_on,
        "kind": kind,
        "source": source,
        "created_at": datetime.now(UTC).isoformat(),
    }
    optional = {
        "observed_at": observed_at,
        "media_ids": list(media_ids) if media_ids else None,
        "status": status,
        "note": note,
        "goal_id": goal_id,
        "task_id": task_id,
        "action_id": action_id,
        "retracts": retracts,
    }
    item.update({k: v for k, v in optional.items() if v})
    return item


def counts_as_visible(item: dict[str, Any]) -> bool:
    """Whether an entry shows on the plant's page (and in `history_count`)."""
    if item.get("kind") == "retraction":
        return False
    remaining = [m for m in item.get("media_ids") or [] if m not in retracted(item)]
    # A photo entry whose only photo moved away has nothing left to show; a check-in or a log
    # still happened, so it stays (without the moved photo).
    return not (item.get("kind") == "photo" and not remaining)


def retracted(item: dict[str, Any]) -> set[str]:
    return set(item.get("retracted_media_ids") or [])


def entries_with_media(items: list[dict[str, Any]], media_id: str) -> list[dict[str, Any]]:
    """The plant's entries that still show `media_id` — what a move must retract."""
    return [
        i
        for i in items
        if i.get("kind") != "retraction"
        and media_id in (i.get("media_ids") or [])
        and media_id not in retracted(i)
    ]


def visible_page(items: list[dict[str, Any]], include_retracted: bool) -> list[dict[str, Any]]:
    """Entries to show, with moved photos removed — unless the full audit trail is asked for."""
    if include_retracted:
        return items
    shown = []
    for item in items:
        if not counts_as_visible(item):
            continue
        gone = retracted(item)
        if gone:
            item = {**item, "media_ids": [m for m in item.get("media_ids") or [] if m not in gone]}
        shown.append(item)
    return shown


def encode_cursor(last_evaluated_key: dict[str, Any] | None) -> str | None:
    if not last_evaluated_key:
        return None
    raw = json.dumps({"sk": last_evaluated_key["sk"]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None, plant_id: str) -> tuple[str | None, str | None]:
    """The `sk` to resume after, or an error. A cursor must belong to this plant's history — a
    crafted one can never page into another plant or entity (§6)."""
    if not cursor:
        return None, None
    try:
        decoded = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
        sk = decoded["sk"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None, "cursor is not valid"
    if not isinstance(sk, str) or not sk.startswith(prefix(plant_id)):
        return None, "cursor is not valid"
    return sk, None


def parse_limit(raw: Any) -> tuple[int, str | None]:
    if raw in (None, ""):
        return DEFAULT_PAGE, None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 0, "limit must be a number"
    if not 1 <= value <= MAX_PAGE:
        return 0, f"limit must be between 1 and {MAX_PAGE}"
    return value, None


def entry_to_api(item: dict[str, Any], photos: list[dict[str, str]]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "entryId": item["entry_id"],
        "observedOn": item["observed_on"],
        "kind": item["kind"],
        "source": item["source"],
        "photos": photos,
    }
    for field, key in (
        ("observed_at", "observedAt"),
        ("status", "status"),
        ("note", "note"),
        ("goal_id", "goalId"),
        ("task_id", "taskId"),
        ("retracts", "retracts"),
    ):
        if item.get(field):
            entry[key] = item[field]
    return entry
