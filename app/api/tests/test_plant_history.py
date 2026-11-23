"""Pure plant-history rules (ADR-0035): dates, entries, retractions, cursors."""

from datetime import UTC, datetime, timedelta

import plant_history as ph
import pytest


def test_entry_key_sorts_by_plant_then_observation_date():
    item = ph.new_entry(
        "g1",
        "p13",
        kind="photo",
        source="user",
        observed_on="2026-08-02",
        entry_id="m1",
        media_ids=["m1"],
        note=None,
    )
    assert item["pk"] == "GARDEN#g1"
    assert item["sk"] == "PLANTLOG#p13#2026-08-02#m1"
    assert "note" not in item and item["media_ids"] == ["m1"]


def test_unknown_kind_or_source_is_refused():
    with pytest.raises(ValueError):
        ph.new_entry("g", "p", kind="diagnosis", source="user", observed_on="2026-01-01")
    with pytest.raises(ValueError):
        ph.new_entry("g", "p", kind="log", source="model", observed_on="2026-01-01")


@pytest.mark.parametrize(
    ("media", "expected"),
    [
        ({"taken_at": "2026-08-02T07:30:00", "uploaded_at": "2026-11-23T10:00:00"}, "2026-08-02"),
        ({"uploaded_at": "2026-11-23T10:00:00+00:00"}, "2026-11-23"),
        ({"taken_at": "garbage", "uploaded_at": "2026-11-23T10:00:00"}, "2026-11-23"),
        ({}, "2026-01-05"),
    ],
)
def test_a_photo_is_dated_by_capture_then_upload(media, expected):
    assert ph.observed_on_for_media(media, fallback="2026-01-05") == expected


def test_observed_on_must_be_a_real_past_date():
    assert ph.parse_observed_on(None) == (None, None)
    assert ph.parse_observed_on("2026-08-02") == ("2026-08-02", None)
    assert ph.parse_observed_on("2026-02-30")[1] == "observedOn must be a real date"
    assert ph.parse_observed_on("02-08-2026")[1]
    tomorrow = (datetime.now(UTC).date() + timedelta(days=2)).isoformat()
    assert ph.parse_observed_on(tomorrow)[1] == "observedOn cannot be in the future"


def _entry(entry_id, kind="photo", media=("m1",), retracted=()):
    item = {"entry_id": entry_id, "kind": kind, "media_ids": list(media)}
    if retracted:
        item["retracted_media_ids"] = list(retracted)
    return item


def test_moved_photos_are_hidden_but_checkins_and_logs_stay():
    photo_moved = _entry("a", retracted=["m1"])
    checkin_moved = _entry("b", kind="checkin", retracted=["m1"])
    retraction = _entry("c", kind="retraction")
    two_photos = _entry("d", media=("m1", "m2"), retracted=["m1"])
    page = ph.visible_page([photo_moved, checkin_moved, retraction, two_photos], False)
    assert [(e["entry_id"], e["media_ids"]) for e in page] == [("b", []), ("d", ["m2"])]
    # The audit view returns everything as stored.
    assert len(ph.visible_page([photo_moved, retraction], True)) == 2


def test_entries_with_media_finds_what_a_move_must_retract():
    items = [
        _entry("a"),
        _entry("b", retracted=["m1"]),
        _entry("c", kind="retraction"),
        _entry("d", media=("m2",)),
    ]
    assert [e["entry_id"] for e in ph.entries_with_media(items, "m1")] == ["a"]


def test_cursor_round_trips_and_cannot_leave_the_plant():
    cursor = ph.encode_cursor({"pk": "GARDEN#g1", "sk": "PLANTLOG#p13#2026-08-02#m1"})
    assert ph.decode_cursor(cursor, "p13") == ("PLANTLOG#p13#2026-08-02#m1", None)
    assert ph.decode_cursor(cursor, "p1")[1] == "cursor is not valid"
    assert ph.decode_cursor("not-base64!!", "p13")[1] == "cursor is not valid"
    other = ph.encode_cursor({"sk": "GOAL#x"})
    assert ph.decode_cursor(other, "p13")[1] == "cursor is not valid"
    assert ph.encode_cursor(None) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, (20, None)), ("5", (5, None)), ("0", None), ("51", None), ("x", None)],
)
def test_page_size(raw, expected):
    value, err = ph.parse_limit(raw)
    if expected is None:
        assert err
    else:
        assert (value, err) == expected


def test_entry_to_api():
    item = ph.new_entry(
        "g1",
        "p1",
        kind="checkin",
        source="confirmed",
        observed_on="2026-11-20",
        entry_id="a1",
        task_id="t1",
        goal_id="goal-1",
        note="Check drip irrigation",
    )
    photo = {"mediaId": "m1", "thumbUrl": "t", "url": "u"}
    assert ph.entry_to_api(item, [photo]) == {
        "entryId": "a1",
        "observedOn": "2026-11-20",
        "kind": "checkin",
        "source": "confirmed",
        "photos": [photo],
        "note": "Check drip irrigation",
        "goalId": "goal-1",
        "taskId": "t1",
    }
