"""Tests for scripts/backfill_media.py (ADR-0033): key mapping and dry-run safety. No AWS calls."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_media.py"
spec = importlib.util.spec_from_file_location("backfill_media", SCRIPT)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)


ITEM = {
    "pk": "GARDEN#g1",
    "sk": "MEDIA#m1",
    "garden_id": "g1",
    "media_id": "m1",
    "s3_key": "g1/m1/2026-11-10_plant10.jpg",
}


def test_new_key_moves_legacy_layout_under_uploads():
    assert backfill.new_key(ITEM) == "uploads/g1/m1/2026-11-10_plant10.jpg"


def test_new_key_skips_items_already_under_uploads():
    assert backfill.new_key(dict(ITEM, s3_key="uploads/g1/m1/x.jpg")) is None


class _FakeTable:
    name = "tendril-dev-app"

    def __init__(self):
        self.updates = []

    def scan(self, **kwargs):
        return {"Items": [ITEM]}

    def query(self, **kwargs):
        return {"Items": [ITEM]}

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


class _FakeS3:
    def __init__(self):
        self.copies = []

    def copy_object(self, **kwargs):
        self.copies.append(kwargs)


class _FakeSession:
    def __init__(self, table, s3):
        self._table, self._s3 = table, s3

    def resource(self, name):
        return type("R", (), {"Table": lambda _self, n: self._table})()

    def client(self, name):
        return self._s3


@pytest.fixture
def fakes(monkeypatch):
    table, s3 = _FakeTable(), _FakeS3()
    monkeypatch.setattr(
        backfill.boto3.session, "Session", lambda region_name=None: _FakeSession(table, s3)
    )
    return table, s3


def test_dry_run_changes_nothing(fakes, capsys):
    table, s3 = fakes
    assert backfill.main(["--env", "dev"]) == 0
    assert table.updates == [] and s3.copies == []
    assert "Dry run only" in capsys.readouterr().out


def test_apply_copies_then_marks_pending_and_keeps_the_old_object(fakes):
    table, s3 = fakes
    backfill.main(["--env", "dev", "--garden", "g1", "--apply"])
    (copy,) = s3.copies
    assert copy["Bucket"] == "tendril-dev-media"
    assert copy["Key"] == "uploads/g1/m1/2026-11-10_plant10.jpg"
    assert copy["CopySource"] == {"Bucket": "tendril-dev-media", "Key": ITEM["s3_key"]}
    (update,) = table.updates
    values = update["ExpressionAttributeValues"]
    assert values == {
        ":new": "uploads/g1/m1/2026-11-10_plant10.jpg",
        ":old": ITEM["s3_key"],
        ":pending": "pending",
    }
    assert update["ConditionExpression"] == "attribute_not_exists(processing_status)"
