"""The Supervisor's garden-state rollup (ADR-0039 §3): a compact text summary built by code, with
no model, from the garden's own records. The model reads this, never 105 raw plant records.

Pure functions over already-loaded items so it is unit-testable without AWS.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

RECENT_DAYS = 60
MAX_RECENT_LOGS = 25
MAX_WATCH = 15
MAX_CHARS = 9000
CONTEXT_FIELDS = (
    ("soil", "Soil"),
    ("irrigation", "Irrigation"),
    ("intercrop", "Intercrop"),
    ("layout", "Layout"),
    ("notes", "Notes"),
)


def _days_ago(day: str, today: str) -> int:
    return (date.fromisoformat(today) - date.fromisoformat(day)).days


def garden_section(metadata: dict[str, Any]) -> list[str]:
    lines = [f"Garden: {metadata.get('name', '')} — {metadata.get('geolocation', '')}"]
    if metadata.get("climate_zone"):
        lines.append(f"Climate: {metadata['climate_zone']}")
    if metadata.get("vision"):
        lines.append(f"About: {metadata['vision']}")
    ctx = metadata.get("context") or {}
    for key, label in CONTEXT_FIELDS:  # never notification_recipients (personal data)
        if ctx.get(key):
            lines.append(f"{label}: {ctx[key]}")
    return lines


def plant_section(plants: list[dict[str, Any]], notes_by_plant: dict[str, str]) -> list[str]:
    counts = Counter(p.get("latest_status") or "unlogged" for p in plants)
    lines = [
        f"Plants: {len(plants)} — "
        + ", ".join(f"{counts[s]} {s}" for s in ("healthy", "ok", "watch", "unlogged") if counts[s])
    ]
    watch = sorted(
        (p for p in plants if p.get("latest_status") == "watch"),
        key=lambda p: int(p.get("stake_no") or 0),
    )
    if watch:
        lines.append("Watch list:")
        for p in watch[:MAX_WATCH]:
            label = (
                f"#{int(p['stake_no'])}" if p.get("stake_no") is not None else p.get("species", "")
            )
            note = notes_by_plant.get(p["plant_id"], "")
            lines.append(f"- {label} ({p.get('latest_observed_on', '?')}): {note}".rstrip(": "))
    return lines


def _work_line(item: dict[str, Any]) -> str:
    parts = [item.get("title", "")]
    if item.get("product"):
        parts.append(f"product {item['product']}")
    if item.get("dose"):
        parts.append(f"dose {item['dose']}")
    if item.get("details"):
        parts.append(item["details"])
    targets = item.get("targets", "all")
    parts.append("whole garden" if targets == "all" else f"{len(targets)} plant(s)")
    return "; ".join(p for p in parts if p)


def recent_work_section(logs: list[dict[str, Any]], today: str) -> list[str]:
    recent = sorted(
        (log for log in logs if _days_ago(log["done_on"], today) <= RECENT_DAYS),
        key=lambda log: log["done_on"],
        reverse=True,
    )[:MAX_RECENT_LOGS]
    if not recent:
        return [f"Recent work (last {RECENT_DAYS} days): none recorded."]
    lines = [f"Recent work (last {RECENT_DAYS} days, newest first):"]
    for log in recent:
        verdict = (log.get("assessment") or {}).get("verdict")
        tag = f" [{verdict}]" if verdict else ""
        lines.append(
            f"- {log['done_on']} ({_days_ago(log['done_on'], today)} days ago): "
            f"{_work_line(log)}{tag}"
        )
    return lines


def schedule_text(schedule: dict[str, Any] | None) -> str:
    if not schedule:
        return "one-off"
    if schedule.get("every_days"):
        return f"every {int(schedule['every_days'])} days"
    return f"every {int(schedule['every_days_min'])}-{int(schedule['every_days_max'])} days"


def activities_section(activities: list[dict[str, Any]], today: str) -> list[str]:
    active = [a for a in activities if a.get("status") in ("active", "paused")]
    if not active:
        return ["Planned activities: none."]
    lines = ["Planned activities (id: what — schedule — status/due):"]
    for a in sorted(active, key=lambda a: a.get("next_due_on") or "9999"):
        due = a.get("next_due_on")
        if a.get("status") == "paused":
            state = "paused"
        elif not due:
            state = "nothing due"
        elif a.get("overdue_on") and today > a["overdue_on"]:
            state = f"OVERDUE since {a['overdue_on']}"
        elif today >= due:
            state = f"due now (from {due})"
        else:
            state = f"next due {due}"
        last = f", last done {a['last_done_on']}" if a.get("last_done_on") else ""
        lines.append(
            f"- {a['activity_id']}: {_work_line(a)} — {schedule_text(a.get('schedule'))} — "
            f"{state}{last}"
        )
    return lines


def forecast_section(forecast: list[dict[str, Any]] | None) -> list[str]:
    if not forecast:
        return ["Forecast: unavailable."]
    lines = ["Forecast (next days):"]
    for d in forecast:
        lines.append(
            f"- {d['date']}: {d.get('tmax')}°C max, rain {d.get('rain_mm', 0)} mm "
            f"({d.get('rain_chance', '?')}% chance)"
        )
    return lines


def build_rollup(
    *,
    metadata: dict[str, Any],
    plants: list[dict[str, Any]],
    notes_by_plant: dict[str, str],
    logs: list[dict[str, Any]],
    activities: list[dict[str, Any]],
    forecast: list[dict[str, Any]] | None,
    today: str,
) -> str:
    sections = [
        [f"Today: {today}"],
        garden_section(metadata),
        plant_section(plants, notes_by_plant),
        activities_section(activities, today),
        recent_work_section(logs, today),
        forecast_section(forecast),
    ]
    text = "\n\n".join("\n".join(s) for s in sections)
    if len(text) <= MAX_CHARS:
        return text
    # Over budget: drop the oldest recent-work lines first (ADR-0039 §3).
    work = sections[4]
    while len(text) > MAX_CHARS and len(work) > 2:
        work.pop()
        text = "\n\n".join("\n".join(s) for s in sections)
    return text[:MAX_CHARS]


def describe(item: dict[str, Any], *, logged: bool, today: str | None = None) -> str:
    """The one activity being assessed, in full. The days since it was last done are spelled out:
    in the eval the model misread a 32-day gap as 2 days when it had to work it out."""
    lines = [
        ("Work that was DONE" if logged else "Work being PLANNED") + f": {item.get('title', '')}",
        f"Kind: {item.get('kind', 'operation')}",
    ]
    for key, label in (
        ("product", "Product"),
        ("dose", "Dose"),
        ("details", "Details"),
        ("note", "Note"),
    ):
        if item.get(key):
            lines.append(f"{label}: {item[key]}")
    targets = item.get("targets", "all")
    lines.append(
        "Targets: whole garden" if targets == "all" else f"Targets: {len(targets)} plant(s)"
    )
    if logged:
        lines.append(f"Done on: {item.get('done_on')}")
        if item.get("media_ids"):
            lines.append(f"Photos/videos attached: {len(item['media_ids'])}")
    else:
        lines.append(f"Schedule: {schedule_text(item.get('schedule'))}")
        if item.get("last_done_on"):
            ago = f" ({_days_ago(item['last_done_on'], today)} days ago)" if today else ""
            lines.append(f"Last done: {item['last_done_on']}{ago}")
        else:
            lines.append("Last done: never (first time)")
        if item.get("next_due_on"):
            lines.append(f"First/next due: {item['next_due_on']}")
    return "\n".join(lines)


def valid_due_on(raw: str, today: str) -> bool:
    try:
        day = date.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    return date.fromisoformat(today) <= day <= date.fromisoformat(today) + timedelta(days=365)
