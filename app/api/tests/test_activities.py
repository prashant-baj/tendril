"""Garden activity rules (ADR-0038): schedules, due dates, validation, API shapes."""

import activities as act
import pytest


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        (None, None),
        ({"everyDays": 7}, {"every_days": 7}),
        ({"minDays": 21, "maxDays": 28}, {"every_days_min": 21, "every_days_max": 28}),
    ],
)
def test_schedules_parse(raw, stored):
    assert act.parse_schedule(raw) == (stored, None)
    assert act.schedule_to_api(stored) == raw


@pytest.mark.parametrize(
    "raw",
    [
        {"everyDays": 0},
        {"everyDays": 400},
        {"minDays": 28, "maxDays": 21},
        {"minDays": 21},
        {"everyDays": 7, "minDays": 1, "maxDays": 2},
        {"everyDays": True},
        "weekly",
    ],
)
def test_bad_schedules_are_refused(raw):
    assert act.parse_schedule(raw)[1]


def test_humic_every_three_to_four_weeks_is_due_from_day_21_and_overdue_after_day_28():
    window = {"every_days_min": 21, "every_days_max": 28}
    assert act.due_window(window, "2026-10-23", None) == ("2026-11-13", "2026-11-20")
    item = {"status": "active", "next_due_on": "2026-11-13", "overdue_on": "2026-11-20"}
    assert act.due_state(item, "2026-11-12") == "upcoming"
    assert act.due_state(item, "2026-11-13") == "due"
    assert act.due_state(item, "2026-11-20") == "due"
    assert act.due_state(item, "2026-11-21") == "overdue"


def test_before_the_first_time_it_is_due_on_its_start_date():
    assert act.due_window({"every_days": 7}, None, "2026-11-25") == ("2026-11-25", "2026-11-25")
    assert act.due_window({"every_days_min": 21, "every_days_max": 28}, None, "2026-11-25") == (
        "2026-11-25",
        "2026-12-02",
    )
    assert act.due_window(None, None, None) == (None, None)


def test_logging_moves_the_schedule_forward_never_back():
    weekly = {"status": "active", "schedule": {"every_days": 7}, "last_done_on": "2026-11-20"}
    assert act.after_logging(weekly, "2026-11-24") == {
        "last_done_on": "2026-11-24",
        "next_due_on": "2026-12-01",
        "overdue_on": "2026-12-01",
    }
    assert act.after_logging(weekly, "2026-11-01") == {}  # backfilled older log


def test_a_one_off_activity_is_done_once_logged():
    assert act.after_logging({"status": "active"}, "2026-11-24") == {
        "last_done_on": "2026-11-24",
        "status": "done",
        "next_due_on": None,
        "overdue_on": None,
    }
    assert act.due_state({"status": "done"}) == "done"
    assert act.due_state({"status": "paused", "next_due_on": "2026-01-01"}) == "paused"


@pytest.mark.parametrize(
    ("raw", "ok"),
    [(None, "all"), ("all", "all"), (["p1", "p1", "p2"], ["p1", "p2"]), ([], None), ("p1", None)],
)
def test_targets(raw, ok):
    value, err = act.parse_targets(raw)
    assert (value if not err else None) == ok


def test_assessment_is_marked_stale_after_the_activity_changes():
    item = {
        "activity_id": "a1",
        "title": "Humic drench",
        "version": 2,
        "assessment": {
            "verdict": "beneficial",
            "summary": "Good spacing.",
            "benefits": ["root growth"],
            "risks": [],
            "conflicts": [],
            "assessed_version": 1,
        },
    }
    out = act.activity_to_api(item, "2026-11-24")
    assert out["assessment"]["stale"] is True and out["assessment"]["verdict"] == "beneficial"
    assert out["dueState"] == "none" and "schedule" not in out
