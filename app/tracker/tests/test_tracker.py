"""Unit tests for the tracker/scheduler Lambda (Phase 7.5+).

No live DynamoDB/EventBridge calls — `_get_table`/`_get_events` are monkeypatched with fake
boto3-shaped stand-ins, mirroring the fake-client convention already established in
app/api/tests/test_garden_handler.py and app/orchestrator/tests/test_orchestrator.py.
"""

import json

import tracker as handler


class FakeTable:
    """Supports a sequence of query responses (for pagination tests) — a single fixed response
    is just a sequence of length 1."""

    def __init__(self, query_responses=None):
        self._query_responses = list(query_responses or [{"Items": []}])
        self.query_calls: list[dict] = []

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        index = min(len(self.query_calls) - 1, len(self._query_responses) - 1)
        return self._query_responses[index]


class FakeEvents:
    def __init__(self, raise_on_entry_index: int | None = None):
        self.raise_on_entry_index = raise_on_entry_index
        self.put_events_calls: list[dict] = []
        self._attempts = 0

    def put_events(self, Entries):
        attempt_index = self._attempts
        self._attempts += 1
        if attempt_index == self.raise_on_entry_index:
            raise RuntimeError("put_events failed")
        self.put_events_calls.append({"Entries": Entries})


def _task_item(garden_id: str, goal_id: str, task_id: str) -> dict:
    return {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"TASK#{goal_id}#{task_id}",
        "status": "pending",
        "gsi1pk": "TASK_STATUS#pending",
        "gsi1sk": "2026-09-01T00:00:00+00:00",
    }


def test_queries_tasks_due_index_for_pending_tasks_at_or_before_now(monkeypatch):
    table = FakeTable()
    monkeypatch.setattr(handler, "_get_table", lambda: table)
    monkeypatch.setattr(handler, "_get_events", lambda: FakeEvents())

    handler.handler({}, None)

    assert len(table.query_calls) == 1
    kwargs = table.query_calls[0]
    assert kwargs["IndexName"] == "TasksDueIndex"
    condition = kwargs["KeyConditionExpression"]
    eq_clause, lte_clause = condition._values
    assert eq_clause._values[0].name == "gsi1pk"
    assert eq_clause._values[1] == "TASK_STATUS#pending"
    assert lte_clause._values[0].name == "gsi1sk"


def test_publishes_one_followup_due_event_per_due_task(monkeypatch):
    items = [_task_item("g1", "goal1", "t1"), _task_item("g1", "goal1", "t2")]
    table = FakeTable(query_responses=[{"Items": items}])
    events = FakeEvents()
    monkeypatch.setattr(handler, "_get_table", lambda: table)
    monkeypatch.setattr(handler, "_get_events", lambda: events)

    handler.handler({}, None)

    assert len(events.put_events_calls) == 2
    for call, task_id in zip(events.put_events_calls, ["t1", "t2"], strict=True):
        entry = call["Entries"][0]
        assert entry["Source"] == "tendril.tracker"
        assert entry["DetailType"] == "followup.due"
        detail = json.loads(entry["Detail"])
        assert detail == {"gardenId": "g1", "goalId": "goal1", "taskId": task_id}


def test_publish_failure_for_one_task_does_not_abort_the_run(monkeypatch):
    items = [_task_item("g1", "goal1", "t1"), _task_item("g1", "goal1", "t2")]
    table = FakeTable(query_responses=[{"Items": items}])
    events = FakeEvents(raise_on_entry_index=0)
    monkeypatch.setattr(handler, "_get_table", lambda: table)
    monkeypatch.setattr(handler, "_get_events", lambda: events)

    handler.handler({}, None)  # must not raise

    # The first publish raised (not recorded by FakeEvents), the second still went through.
    assert len(events.put_events_calls) == 1
    detail = events.put_events_calls[0]["Entries"][0]
    assert '"taskId": "t2"' in detail["Detail"]


def test_pagination_across_multiple_query_pages(monkeypatch):
    page1 = {"Items": [_task_item("g1", "goal1", "t1")], "LastEvaluatedKey": {"pk": "x", "sk": "y"}}
    page2 = {"Items": [_task_item("g1", "goal1", "t2")]}
    table = FakeTable(query_responses=[page1, page2])
    events = FakeEvents()
    monkeypatch.setattr(handler, "_get_table", lambda: table)
    monkeypatch.setattr(handler, "_get_events", lambda: events)

    handler.handler({}, None)

    assert len(table.query_calls) == 2
    assert "ExclusiveStartKey" not in table.query_calls[0]
    assert table.query_calls[1]["ExclusiveStartKey"] == {"pk": "x", "sk": "y"}
    assert len(events.put_events_calls) == 2


def test_no_due_tasks_is_a_no_op(monkeypatch):
    table = FakeTable(query_responses=[{"Items": []}])
    events = FakeEvents()
    monkeypatch.setattr(handler, "_get_table", lambda: table)
    monkeypatch.setattr(handler, "_get_events", lambda: events)

    handler.handler({}, None)  # must not raise

    assert events.put_events_calls == []
