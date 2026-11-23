"""Tests for scripts/backfill_plant_history.py (ADR-0035): what each linked photo becomes, and that
nothing is written twice. No AWS calls."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backfill_plant_history.py"
spec = importlib.util.spec_from_file_location("backfill_plant_history", SCRIPT)
backfill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backfill)

PLANT = {"pk": "GARDEN#g1", "sk": "PLANT#p13", "plant_id": "p13"}
MEDIA = {
    "pk": "GARDEN#g1",
    "sk": "MEDIA#m1",
    "media_id": "m1",
    "plant_id": "p13",
    "plant_link_source": "confirmed",
    "goal_id": "goal-1",
    "uploaded_at": "2026-11-23T10:00:00+00:00",
}


class _Exceptions:
    class ConditionalCheckFailedException(Exception):
        pass


class _Meta:
    class client:  # noqa: N801  (mirrors boto3's table.meta.client)
        exceptions = _Exceptions


class _FakeTable:
    name = "tendril-dev-app"
    meta = _Meta

    def __init__(self, items):
        self.items = {(i["pk"], i["sk"]): dict(i) for i in items}
        self.updates = []

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def query(self, KeyConditionExpression, **_):
        pk_cond, sk_cond = KeyConditionExpression._values
        pk, prefix = pk_cond._values[1], sk_cond._values[1]
        return {
            "Items": [v for (p, s), v in self.items.items() if p == pk and s.startswith(prefix)]
        }

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if key in self.items:
            raise _Exceptions.ConditionalCheckFailedException()
        self.items[key] = Item

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


def test_a_confirmed_goal_photo_becomes_a_dated_photo_entry():
    table = _FakeTable([PLANT, MEDIA])
    entry, reason = backfill.plan_entry(table, MEDIA)
    assert reason == "photo on 2026-11-23"
    assert entry["sk"] == "PLANTLOG#p13#2026-11-23#m1"
    assert entry["source"] == "confirmed" and entry["goal_id"] == "goal-1"


def test_a_completed_checkin_becomes_a_checkin_entry_with_the_task_title():
    media = dict(MEDIA, task_id="t1", plant_link_source=None, taken_at="2026-11-20T08:00:00")
    task = {"pk": "GARDEN#g1", "sk": "TASK#goal-1#t1", "status": "done", "title": "Mulch"}
    entry, _ = backfill.plan_entry(_FakeTable([PLANT, media, task]), media)
    assert entry["kind"] == "checkin" and entry["note"] == "Mulch" and entry["task_id"] == "t1"
    assert entry["observed_on"] == "2026-11-20" and entry["source"] == "user"


def test_photos_already_in_the_history_or_of_deleted_plants_are_skipped():
    existing = {
        "pk": "GARDEN#g1",
        "sk": "PLANTLOG#p13#2026-11-23#m1",
        "kind": "photo",
        "media_ids": ["m1"],
    }
    assert backfill.plan_entry(_FakeTable([PLANT, MEDIA, existing]), MEDIA) == (
        None,
        "already in the history",
    )
    assert backfill.plan_entry(_FakeTable([MEDIA]), MEDIA) == (None, "plant no longer exists")


def test_write_is_idempotent_and_bumps_the_plant():
    table = _FakeTable([PLANT, MEDIA])
    entry, _ = backfill.plan_entry(table, MEDIA)
    assert backfill.write(table, entry) is True
    assert backfill.write(table, entry) is False  # second run: no duplicate
    expressions = [u["UpdateExpression"] for u in table.updates]
    assert expressions == [
        "ADD history_count :d",
        "SET latest_observed_on = :o",
        "SET media_id = :m, latest_photo_on = :o",
    ]


def test_dry_run_writes_nothing(monkeypatch, capsys):
    table = _FakeTable([PLANT, MEDIA])
    table.scan = lambda **_: {"Items": [MEDIA]}

    class _Session:
        def __init__(self, **_):
            pass

        def resource(self, _name):
            class _Dynamo:
                def Table(self, _name):  # noqa: N802
                    return table

            return _Dynamo()

    monkeypatch.setattr(backfill.boto3.session, "Session", _Session)
    assert backfill.main(["--env", "dev"]) == 0
    assert "would add" in capsys.readouterr().out
    assert not any(k[1].startswith("PLANTLOG#") for k in table.items)
