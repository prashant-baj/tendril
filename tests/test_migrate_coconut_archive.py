"""Tests for scripts/migrate_coconut_archive.py (CP-04): archive parsing and the history it
produces. No AWS calls."""

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "migrate_coconut_archive.py"
spec = importlib.util.spec_from_file_location("migrate_coconut_archive", SCRIPT)
mig = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mig  # dataclasses look their module up
spec.loader.exec_module(mig)

LOG_100 = """# Plant #100 — Kalpa Samrudhi coconut

Bramhangaon west block. Planted ~10 Sep 2026.

## Log

### 10 Oct 2026  [watch]
Drooping after transplant.

### 23 Oct 2026  [ok]
Establishing; one lower leaf yellowing/browning. Spear green.
  Photo: `2026-10-23_0839_plant100.jpg`
"""

SUMMARY = """# Coconut Tracking — Summary

_Last updated: 25 Oct 2026._  105 Kalpa Samrudhi palms.

## Inputs applied
- 23 Oct: humic acid drench via drip.

## Open field actions
1. Mulch all basins.
"""


def test_photo_names_give_the_palm_date_and_time():
    assert mig.parse_photo_name("2026-10-23_0839_plant100.jpg") == (
        100,
        "2026-10-23",
        "2026-10-23T08:39:00",
    )
    assert mig.parse_photo_name("2026-11-20_plant3.JPG") == (3, "2026-11-20", None)
    assert mig.parse_photo_name("WhatsApp Image 2026-10-10.jpeg") is None


def test_log_entries_have_date_status_note_and_named_photo():
    first, second = mig.parse_log(100, LOG_100)
    assert (first.observed_on, first.status, first.text) == (
        "2026-10-10",
        "watch",
        "Drooping after transplant.",
    )
    assert second.observed_on == "2026-10-23" and second.status == "ok"
    assert second.photo_file == "2026-10-23_0839_plant100.jpg"
    assert "Photo:" not in second.text


def test_inputs_log_becomes_dated_inputs():
    assert mig.parse_inputs(SUMMARY) == [("2026-10-23", "humic acid drench via drip")]


def test_a_note_carries_its_photo_and_other_photos_get_their_own_entry(tmp_path):
    folder = tmp_path / "100"
    folder.mkdir()
    (folder / "2026-10-10_plant100.jpg").write_bytes(b"\xff\xd8x")
    (folder / "2026-10-23_0839_plant100.jpg").write_bytes(b"\x89PNG\r\n\x1a\nx")
    (folder / "log.md").write_text(LOG_100, encoding="utf-8")
    (tmp_path / "raw").mkdir()  # unsorted originals are skipped
    (tmp_path / "raw" / "2026-10-10_plant5.jpg").write_bytes(b"x")
    (tmp_path / "_SUMMARY.md").write_text(SUMMARY, encoding="utf-8")

    palms, inputs = mig.read_archive(tmp_path)
    assert list(palms) == [100] and len(palms[100].photos) == 2 and len(inputs) == 1

    media = {p.path.name: f"m-{p.path.name[:10]}" for p in palms[100].photos}
    entries = mig.history_entries("g1", palms[100], "p100", media)
    kinds = sorted((e["observed_on"], e["kind"], tuple(e.get("media_ids") or [])) for e in entries)
    assert kinds == [
        ("2026-10-10", "log", ()),  # the note without a photo
        ("2026-10-10", "photo", ("m-2026-10-10",)),  # the photo no note names
        ("2026-10-23", "log", ("m-2026-10-23",)),  # the note carries its photo — shown once
    ]
    assert all(e["source"] == "migration" for e in entries)
    photo = next(e for e in entries if e["kind"] == "photo")
    assert photo["entry_id"] == "m-2026-10-10"


def test_ids_are_stable_across_runs():
    assert mig.stable_id("media", "g1", 100, "a.jpg") == mig.stable_id("media", "g1", 100, "a.jpg")
    assert mig.stable_id("media", "g1", 100, "a.jpg") != mig.stable_id("media", "g2", 100, "a.jpg")


def test_content_type_follows_the_bytes_not_the_extension():
    assert mig.content_type(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert mig.content_type(b"\xff\xd8\xff...") == "image/jpeg"
