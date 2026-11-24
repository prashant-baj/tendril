"""Garden activities (ADR-0038): farm work, its schedule, and when it's due. Pure logic, no AWS.

Two records in the garden partition:

    ACTIVITY#{activity_id}          what the work is and when it's next due (one-off or recurring)
    ACTLOG#{done_on}#{log_id}       each time work was done (ad-hoc, or a log of an activity)

Schedules are counted from the last time the work was done (ADR-0038 §2):

    none                      one-off: due on its start date, `done` once logged
    every_days: N             due N days after the last done, overdue the day after
    every_days_min/max: a, b  a window ("every 3-4 weeks"): due from +a, overdue after +b

Before the first time it's done, the activity is due on its `start_on`. `next_due_on` and
`overdue_on` are stored on the item (computed here at write time); the due state is read-time.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any

KINDS = ("input", "operation")
STATUSES = ("active", "paused", "done", "archived")
TITLE_MAX = 120
DETAILS_MAX = 1000
FIELD_MAX = 200
MAX_INTERVAL_DAYS = 365
MAX_MEDIA = 6
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def _add(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


# --- validation ----------------------------------------------------------------------------------


def parse_date(raw: Any, field: str, *, allow_future: bool) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not DATE_PATTERN.match(raw):
        return None, f"{field} must be a date (YYYY-MM-DD)"
    try:
        date.fromisoformat(raw)
    except ValueError:
        return None, f"{field} must be a real date"
    if not allow_future and raw > today():
        return None, f"{field} cannot be in the future"
    return raw, None


def parse_schedule(raw: Any) -> tuple[dict[str, int] | None, str | None]:
    """API `schedule` -> stored schedule. `null`/absent = one-off; `{everyDays}` or
    `{minDays, maxDays}`. Returns (schedule or None, error)."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "schedule must be an object"

    def days(key: str) -> int | None:
        value = raw.get(key)
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            return -1
        return value

    every, low, high = days("everyDays"), days("minDays"), days("maxDays")
    if every is not None:
        if low is not None or high is not None:
            return None, "schedule takes everyDays or minDays/maxDays, not both"
        if not 1 <= every <= MAX_INTERVAL_DAYS:
            return None, f"everyDays must be between 1 and {MAX_INTERVAL_DAYS}"
        return {"every_days": every}, None
    if low is None or high is None:
        return None, "schedule needs everyDays, or both minDays and maxDays"
    if not (1 <= low <= high <= MAX_INTERVAL_DAYS):
        return None, f"minDays/maxDays must satisfy 1 <= minDays <= maxDays <= {MAX_INTERVAL_DAYS}"
    return {"every_days_min": low, "every_days_max": high}, None


def schedule_to_api(schedule: dict[str, Any] | None) -> dict[str, int] | None:
    if not schedule:
        return None
    if schedule.get("every_days"):
        return {"everyDays": int(schedule["every_days"])}
    return {"minDays": int(schedule["every_days_min"]), "maxDays": int(schedule["every_days_max"])}


def parse_text(raw: Any, field: str, limit: int, *, required: bool = False):
    if raw is None:
        return (None, f"{field} is required") if required else (None, None)
    if not isinstance(raw, str) or len(raw) > limit:
        return None, f"{field} must be text of at most {limit} characters"
    value = raw.strip()
    if required and not value:
        return None, f"{field} is required"
    return value or None, None


def parse_targets(raw: Any) -> tuple[Any, str | None]:
    """`"all"` (default) or a non-empty list of plant ids."""
    if raw is None or raw == "all":
        return "all", None
    if isinstance(raw, list) and raw and all(isinstance(p, str) and p for p in raw):
        return list(dict.fromkeys(raw)), None
    return None, 'targets must be "all" or a list of plant ids'


# --- due dates -----------------------------------------------------------------------------------


def due_window(
    schedule: dict[str, Any] | None, last_done_on: str | None, start_on: str | None
) -> tuple[str | None, str | None]:
    """(next_due_on, overdue_on) for an active activity, or (None, None) when nothing is due."""
    if last_done_on is None:
        if not start_on:
            return None, None
        grace = 0
        if schedule and schedule.get("every_days_max"):
            grace = int(schedule["every_days_max"]) - int(schedule["every_days_min"])
        return start_on, _add(start_on, grace)
    if not schedule:
        return None, None  # one-off and already done
    if schedule.get("every_days"):
        n = int(schedule["every_days"])
        return _add(last_done_on, n), _add(last_done_on, n)
    return (
        _add(last_done_on, int(schedule["every_days_min"])),
        _add(last_done_on, int(schedule["every_days_max"])),
    )


def due_state(item: dict[str, Any], on: str | None = None) -> str:
    """`overdue` | `due` | `upcoming` | `paused` | `done` | `none` (active, nothing scheduled)."""
    status = item.get("status", "active")
    if status in ("paused", "done", "archived"):
        return "paused" if status == "paused" else "done"
    next_due, overdue = item.get("next_due_on"), item.get("overdue_on")
    if not next_due:
        return "none"
    on = on or today()
    if overdue and on > overdue:
        return "overdue"
    if on >= next_due:
        return "due"
    return "upcoming"


DUE_ORDER = {"overdue": 0, "due": 1, "upcoming": 2, "none": 3, "paused": 4, "done": 5}


def after_logging(item: dict[str, Any], done_on: str) -> dict[str, Any]:
    """The fields an activity takes after being done on `done_on`. A log older than the last one
    (backfilling) doesn't move the schedule backwards."""
    last = item.get("last_done_on")
    if last and done_on < last:
        return {}
    schedule = item.get("schedule")
    if not schedule:
        return {"last_done_on": done_on, "status": "done", "next_due_on": None, "overdue_on": None}
    next_due, overdue = due_window(schedule, done_on, item.get("start_on"))
    return {"last_done_on": done_on, "next_due_on": next_due, "overdue_on": overdue}


# --- API shapes ----------------------------------------------------------------------------------


def assessment_to_api(item: dict[str, Any]) -> dict[str, Any] | None:
    """The Supervisor's assessment (ADR-0038 §4 contract), with `stale` when the activity changed
    after it was assessed."""
    a = item.get("assessment")
    if not a:
        return None
    out: dict[str, Any] = {
        "verdict": a.get("verdict", "unknown"),
        "summary": a.get("summary", ""),
        "benefits": list(a.get("benefits") or []),
        "risks": list(a.get("risks") or []),
        "conflicts": [
            {"with": c.get("with", ""), "reason": c.get("reason", "")}
            for c in (a.get("conflicts") or [])
        ],
    }
    if a.get("suggestion"):
        out["suggestion"] = a["suggestion"]
    if a.get("assessed_at"):
        out["assessedAt"] = a["assessed_at"]
    version = item.get("version")
    if version is not None and a.get("assessed_version") is not None:
        out["stale"] = int(a["assessed_version"]) != int(version)
    return out


def activity_to_api(item: dict[str, Any], on: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "activityId": item["activity_id"],
        "title": item["title"],
        "kind": item.get("kind", "operation"),
        "targets": item.get("targets", "all"),
        "status": item.get("status", "active"),
        "dueState": due_state(item, on),
        "schedule": schedule_to_api(item.get("schedule")),
        "source": item.get("source", "user"),
    }
    for field, key in (
        ("details", "details"),
        ("product", "product"),
        ("dose", "dose"),
        ("start_on", "startOn"),
        ("next_due_on", "nextDueOn"),
        ("overdue_on", "overdueOn"),
        ("last_done_on", "lastDoneOn"),
    ):
        if item.get(field):
            out[key] = item[field]
    assessment = assessment_to_api(item)
    if assessment:
        out["assessment"] = assessment
    if out["schedule"] is None:
        del out["schedule"]
    return out


def log_to_api(item: dict[str, Any], media: list[dict[str, str]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "logId": item["log_id"],
        "title": item["title"],
        "kind": item.get("kind", "operation"),
        "targets": item.get("targets", "all"),
        "doneOn": item["done_on"],
        "media": media,
    }
    for field, key in (
        ("activity_id", "activityId"),
        ("details", "details"),
        ("product", "product"),
        ("dose", "dose"),
        ("note", "note"),
    ):
        if item.get(field):
            out[key] = item[field]
    assessment = assessment_to_api(item)
    if assessment:
        out["assessment"] = assessment
    return out
