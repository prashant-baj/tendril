"""Tests for scripts/eval_plant_identifier.py (ADR-0034 §5): ground truth and the < 1% gate.
No AWS calls."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_plant_identifier.py"
spec = importlib.util.spec_from_file_location("eval_plant_identifier", SCRIPT)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)


def test_archive_photos_use_numbered_folders_as_ground_truth(tmp_path):
    for folder, name in [("10", "2026-11-10_plant10.jpg"), ("1", "a.JPG"), ("raw", "x.jpg")]:
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / name).write_bytes(b"x")
    (tmp_path / "10" / "notes.txt").write_text("skip me")
    found = [(n, p.name) for n, p in ev.archive_photos(tmp_path)]
    assert found == [(1, "a.JPG"), (10, "2026-11-10_plant10.jpg")]  # raw/ and non-images skipped


def _row(truth, read, confidence):
    return {"truth": truth, "read": read, "confidence": confidence}


def test_summary_gates_auto_link_on_wrong_high_confidence_reads():
    rows = [_row(n, n, "high") for n in range(1, 100)] + [_row(100, 7, "high")]  # 1 wrong of 100
    s = ev.summarize(rows)
    assert s["wrong_high"] == 1
    assert s["wrong_high_rate"] == 0.01
    assert s["auto_link_ready"] is False  # must be strictly below 1%

    rows = [_row(n, n, "high") for n in range(1, 201)] + [_row(201, 7, "high")]  # 1 of 201
    assert ev.summarize(rows)["auto_link_ready"] is True


def test_low_confidence_misses_do_not_count_against_auto_link():
    rows = [_row(1, 1, "high"), _row(2, None, "low"), _row(3, 8, "low")]
    s = ev.summarize(rows)
    assert s["wrong_high"] == 0 and s["auto_link_ready"] is True
    assert s["miss_rate"] == 1 / 3
    assert s["exact_accuracy"] == 1 / 3
