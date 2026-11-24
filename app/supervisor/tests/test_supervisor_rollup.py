"""The Supervisor's garden summary (ADR-0039 §3): built by code, compact, no personal data."""

import supervisor_rollup as rollup

TODAY = "2026-11-24"
METADATA = {
    "name": "Coconut Plot",
    "geolocation": "Bramhangaon, Kopargaon, Maharashtra",
    "climate_zone": "Maharashtra North region near Nasik",
    "context": {
        "irrigation": "16 mm drip",
        "soil": "clay, salt crust on some basins",
        "notification_recipients": [{"name": "Owner", "e164": "+910000000000"}],
    },
}
PLANTS = [
    {
        "plant_id": "p91",
        "stake_no": 91,
        "latest_status": "watch",
        "latest_observed_on": "2026-10-24",
    },
    {"plant_id": "p3", "stake_no": 3, "latest_status": "healthy"},
    {"plant_id": "p7", "stake_no": 7},
]
HUMIC = {
    "activity_id": "a1",
    "title": "Humic acid drench",
    "kind": "input",
    "dose": "via drip",
    "targets": "all",
    "status": "active",
    "schedule": {"every_days_min": 21, "every_days_max": 28},
    "last_done_on": "2026-10-23",
    "next_due_on": "2026-11-13",
    "overdue_on": "2026-11-20",
}
LOGS = [
    {
        "done_on": "2026-11-15",
        "title": "Foliar spray",
        "product": "neem oil 40 ml + Dhartivita 10 g",
        "targets": "all",
        "assessment": {"verdict": "caution"},
    },
    {"done_on": "2026-08-01", "title": "Old work", "targets": "all"},
]


def _rollup(**kw):
    args = dict(
        metadata=METADATA,
        plants=PLANTS,
        notes_by_plant={"p91": "Pale/yellowing, brown tips; likely lime-locked."},
        logs=LOGS,
        activities=[HUMIC],
        forecast=[{"date": "2026-11-25", "tmax": 31, "rain_mm": 12.5, "rain_chance": 80}],
        today=TODAY,
    )
    args.update(kw)
    return rollup.build_rollup(**args)


def test_the_summary_has_context_status_watch_list_work_and_forecast():
    text = _rollup()
    assert "Irrigation: 16 mm drip" in text and "Soil: clay" in text
    assert "Plants: 3 — 1 healthy, 1 watch, 1 unlogged" in text
    assert "#91 (2026-10-24): Pale/yellowing" in text
    assert (
        "a1: Humic acid drench; dose via drip; whole garden — every 21-28 days — OVERDUE since 2026-11-20"
        in text
    )
    assert "2026-11-15 (9 days ago): Foliar spray; product neem oil" in text and "[caution]" in text
    assert "Old work" not in text  # older than 60 days
    assert "rain 12.5 mm (80% chance)" in text


def test_personal_data_never_reaches_the_model():
    text = _rollup()
    assert "+91" not in text and "Owner" not in text


def test_due_states_in_the_summary():
    upcoming = dict(HUMIC, next_due_on="2026-12-01", overdue_on="2026-12-08")
    assert "next due 2026-12-01" in _rollup(activities=[upcoming])
    due = dict(HUMIC, next_due_on="2026-11-20", overdue_on="2026-11-30")
    assert "due now (from 2026-11-20)" in _rollup(activities=[due])
    assert "Planned activities: none." in _rollup(activities=[])
    assert "Forecast: unavailable." in _rollup(forecast=None)


def test_the_summary_stays_within_budget_dropping_old_work_first(monkeypatch):
    monkeypatch.setattr(rollup, "MAX_CHARS", 900)
    many = [
        {"done_on": f"2026-11-{d:02d}", "title": "Leaching " + "x" * 60, "targets": "all"}
        for d in range(1, 24)
    ]
    text = _rollup(logs=many)
    assert len(text) <= 900
    assert "2026-11-23" in text  # newest kept


def test_describe_one_activity():
    planned = rollup.describe(HUMIC, logged=False)
    assert "Work being PLANNED: Humic acid drench" in planned and "every 21-28 days" in planned
    done = rollup.describe(dict(LOGS[0], kind="input", media_ids=["m1"]), logged=True)
    assert "Work that was DONE" in done and "Photos/videos attached: 1" in done


def test_valid_due_on():
    assert rollup.valid_due_on("2026-11-30", TODAY)
    assert not rollup.valid_due_on("2026-11-01", TODAY)
    assert not rollup.valid_due_on("2028-01-01", TODAY)
    assert not rollup.valid_due_on("soon", TODAY)
