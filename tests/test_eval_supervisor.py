"""Tests for scripts/eval_supervisor.py scoring and the scenario file (ADR-0039 §6). No AWS."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "eval_supervisor", ROOT / "scripts" / "eval_supervisor.py"
)
ev = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ev
spec.loader.exec_module(ev)


def test_scenarios_cover_assessments_and_the_scheduling_gate():
    data = json.loads(ev.SCENARIOS.read_text(encoding="utf-8"))
    kinds = [s["kind"] for s in data["scenarios"]]
    assert kinds.count("assess") >= 5 and kinds.count("agenda") >= 2
    ids = {s["id"] for s in data["scenarios"]}
    assert {
        "spray-two-days-after-drench",
        "heavy-watering-on-salt-crusted-clay",
        "spray-before-rain",
    } <= ids


def test_assessment_scoring():
    sc = {"verdicts": ["caution"], "expect_conflict": True}
    assert ev.score_assessment(sc, SimpleNamespace(verdict="caution", conflicts=[1]))[0]
    assert not ev.score_assessment(sc, SimpleNamespace(verdict="caution", conflicts=[]))[0]
    assert not ev.score_assessment(sc, SimpleNamespace(verdict="beneficial", conflicts=[1]))[0]
    assert not ev.score_assessment(sc, None)[0]


def test_agenda_scoring():
    move = SimpleNamespace(type="move_due", activity_id="spray1")
    want = {"expect_suggestion": {"type": "move_due", "activity_id": "spray1"}}
    assert ev.score_agenda(want, SimpleNamespace(suggestions=[move]))[0]
    assert not ev.score_agenda(want, SimpleNamespace(suggestions=[]))[0]
    assert ev.score_agenda({"expect_suggestion": None}, SimpleNamespace(suggestions=[]))[0]
    assert not ev.score_agenda({"expect_suggestion": None}, SimpleNamespace(suggestions=[move]))[0]
