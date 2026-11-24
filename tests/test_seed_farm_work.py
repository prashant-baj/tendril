"""Tests for scripts/seed_farm_work.py (ADR-0038): stable ids and correct due dates. No AWS."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_farm_work.py"
spec = importlib.util.spec_from_file_location("seed_farm_work", SCRIPT)
seed = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = seed
spec.loader.exec_module(seed)


def test_humic_is_due_21_28_days_after_23_oct():
    item = seed.activity_item("g1")
    assert item["schedule"] == {"every_days_min": 21, "every_days_max": 28}
    assert (item["last_done_on"], item["next_due_on"], item["overdue_on"]) == (
        "2026-10-23",
        "2026-11-13",
        "2026-11-20",
    )


def test_logs_and_events_have_stable_ids_and_the_confirmed_spray_details():
    first, second = seed.log_items("g1"), seed.log_items("g1")
    assert [i["sk"] for i in first] == [i["sk"] for i in second]
    humic, spray = first
    assert humic["activity_id"] == seed.activity_item("g1")["activity_id"]
    assert spray["product"] == "Neem oil 40 ml + Dhartivita 10 g + sticker 5 ml"
    assert spray["dose"] == "2 pumps" and spray["done_on"] == "2026-11-15"
    (event,) = seed.event_items("g1")  # humic's event already came from the CP-04 migration
    assert event["type"] == "input.applied" and event["created_at"].startswith("2026-11-15")
    assert seed.sid("g1", "humic") != seed.sid("g2", "humic")
