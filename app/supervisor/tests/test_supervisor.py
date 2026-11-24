"""Supervisor handler (ADR-0039): what it writes (assessments, proposals) and what it never does.
The model call (`run_structured`) is stubbed; the table is an in-memory fake."""

import supervisor as sup

G = "g1"


class DictTable:
    def __init__(self, items):
        self.items = {(i["pk"], i["sk"]): dict(i) for i in items}
        self.updates = []

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, **_):
        self.items[(Item["pk"], Item["sk"])] = dict(Item)

    def update_item(
        self, Key, UpdateExpression, ExpressionAttributeValues, ExpressionAttributeNames=None, **_
    ):
        self.updates.append((Key["sk"], UpdateExpression))
        item = self.items[(Key["pk"], Key["sk"])]
        if UpdateExpression == "SET assessment = :a":
            item["assessment"] = ExpressionAttributeValues[":a"]
        elif UpdateExpression == "SET #s = :sup":
            item["status"] = "superseded"

    def query(self, KeyConditionExpression, **_):
        pk_cond, sk_cond = KeyConditionExpression._values
        pk, prefix = pk_cond._values[1], sk_cond._values[1]
        found = [dict(v) for (p, s), v in self.items.items() if p == pk and s.startswith(prefix)]
        return {"Items": sorted(found, key=lambda i: i["sk"])}

    def scan(self, **_):
        return {"Items": [{"pk": p} for (p, s) in self.items if s.startswith("ACTIVITY#")]}

    def of_prefix(self, prefix):
        return [v for (p, s), v in self.items.items() if s.startswith(prefix)]


def _items():
    return [
        {
            "pk": f"GARDEN#{G}",
            "sk": "METADATA",
            "name": "Coconut Plot",
            "geolocation": "x",
            "context": {"notification_recipients": [{"name": "Owner", "e164": "+91000"}]},
        },
        {
            "pk": f"GARDEN#{G}",
            "sk": "PLANT#p1",
            "plant_id": "p1",
            "stake_no": 1,
            "latest_status": "healthy",
        },
        {
            "pk": f"GARDEN#{G}",
            "sk": "ACTIVITY#a1",
            "activity_id": "a1",
            "title": "Foliar spray",
            "kind": "input",
            "targets": "all",
            "status": "active",
            "version": 2,
            "next_due_on": "2026-11-25",
            "overdue_on": "2026-11-25",
        },
        {
            "pk": f"GARDEN#{G}",
            "sk": "ACTLOG#2026-11-22#l1",
            "log_id": "l1",
            "title": "Humic acid drench",
            "kind": "input",
            "targets": "all",
            "done_on": "2026-11-22",
        },
    ]


def _assessment(**kw):
    base = dict(
        verdict="caution",
        summary="Too close to the drench.",
        benefits=["pest control"],
        risks=["leaf burn"],
        conflicts=[
            sup.Conflict(conflicting_work="Humic acid drench (22 Nov)", reason="3 days apart")
        ],
        suggestion="Wait until 29 Nov.",
        proposal="none",
        min_days=0,
        max_days=0,
        due_on="",
        proposal_reason="",
    )
    base.update(kw)
    return sup.AssessmentOutput(**base)


def _env(monkeypatch, outputs):
    table = DictTable(_items())
    monkeypatch.setattr(sup, "_table", table)
    monkeypatch.setattr(sup, "_forecast", lambda geo: None)
    prompts = []

    def fake_run(prompt, model_cls, **kw):
        prompts.append(prompt)
        return outputs.pop(0) if outputs else None

    monkeypatch.setattr(sup, "run_structured", fake_run)
    return table, prompts


def test_a_planned_activity_gets_an_assessment_and_nothing_else_changes(monkeypatch):
    table, prompts = _env(monkeypatch, [_assessment()])
    sup.handler(
        {"detail-type": "activity.planned", "detail": {"gardenId": G, "activityId": "a1"}}, None
    )
    activity = table.items[(f"GARDEN#{G}", "ACTIVITY#a1")]
    a = activity["assessment"]
    assert (
        a["verdict"] == "caution"
        and a["assessed_version"] == 2
        and a["assessed_by"] == "supervisor"
    )
    assert a["conflicts"] == [{"with": "Humic acid drench (22 Nov)", "reason": "3 days apart"}]
    assert activity["next_due_on"] == "2026-11-25" and activity["status"] == "active"  # untouched
    assert [u[1] for u in table.updates] == ["SET assessment = :a"]  # the only write
    assert "Humic acid drench" in prompts[0] and "Work being PLANNED: Foliar spray" in prompts[0]
    assert "+91" not in prompts[0]  # personal data never sent to the model


def test_the_same_version_is_not_assessed_twice(monkeypatch):
    table, prompts = _env(monkeypatch, [_assessment(), _assessment()])
    event = {"detail-type": "activity.planned", "detail": {"gardenId": G, "activityId": "a1"}}
    sup.handler(event, None)
    sup.handler(event, None)
    assert len(prompts) == 1


def test_logged_work_is_assessed_once(monkeypatch):
    table, prompts = _env(monkeypatch, [_assessment(verdict="beneficial")])
    event = {
        "detail-type": "activity.logged",
        "detail": {"gardenId": G, "logId": "l1", "doneOn": "2026-11-22"},
    }
    sup.handler(event, None)
    sup.handler(event, None)
    assert (
        table.items[(f"GARDEN#{G}", "ACTLOG#2026-11-22#l1")]["assessment"]["verdict"]
        == "beneficial"
    )
    assert len(prompts) == 1 and "Work that was DONE" in prompts[0]


def test_a_failed_model_turn_records_unknown_never_blocks(monkeypatch):
    table, _ = _env(monkeypatch, [])
    sup.handler(
        {"detail-type": "activity.planned", "detail": {"gardenId": G, "activityId": "a1"}}, None
    )
    assert table.items[(f"GARDEN#{G}", "ACTIVITY#a1")]["assessment"]["verdict"] == "unknown"


def test_a_schedule_idea_becomes_a_proposal_the_owner_must_confirm(monkeypatch):
    table, _ = _env(
        monkeypatch,
        [
            _assessment(
                proposal="move_due",
                due_on="2026-11-29",
                proposal_reason="Space it from the drench.",
            )
        ],
    )
    sup.handler(
        {"detail-type": "activity.planned", "detail": {"gardenId": G, "activityId": "a1"}}, None
    )
    (action,) = table.of_prefix("ACTION#garden#")
    assert action["type"] == "move_due" and action["status"] == "proposed"
    assert action["params"] == {"activity_id": "a1", "due_on": "2026-11-29"}
    assert action["summary"] == "Move 'Foliar spray' to 2026-11-29?"
    assert table.items[(f"GARDEN#{G}", "ACTIVITY#a1")]["next_due_on"] == "2026-11-25"  # not applied


def test_invalid_proposals_are_dropped(monkeypatch):
    table, _ = _env(
        monkeypatch,
        [
            _assessment(proposal="set_schedule", min_days=30, max_days=7),
            _assessment(proposal="move_due", due_on="2020-01-01"),
        ],
    )
    sup.assess(G, "ACTIVITY#a1", logged=False, today="2026-11-24")
    table.items[(f"GARDEN#{G}", "ACTIVITY#a1")]["version"] = 3
    sup.assess(G, "ACTIVITY#a1", logged=False, today="2026-11-24")
    assert table.of_prefix("ACTION#garden#") == []


def test_daily_suggestions_are_only_logged_until_the_evals_pass(monkeypatch):
    agenda = sup.AgendaOutput(
        summary="Rain tomorrow.",
        suggestions=[
            sup.AgendaItem(
                type="move_due",
                activity_id="a1",
                due_on="2026-11-27",
                min_days=0,
                max_days=0,
                reason="Rain tomorrow.",
            )
        ],
    )
    monkeypatch.setattr(sup, "PROPOSALS_ENABLED", False)
    table, _ = _env(monkeypatch, [_assessment(), agenda])
    sup.handler({"detail-type": "supervisor.daily", "detail": {}}, None)
    assert table.of_prefix("ACTION#garden#") == []
    assert table.items[(f"GARDEN#{G}", "ACTIVITY#a1")]["assessment"]  # catch-up assessment ran


def test_daily_suggestions_become_proposals_once_enabled(monkeypatch):
    agenda = sup.AgendaOutput(
        summary="Rain tomorrow.",
        suggestions=[
            sup.AgendaItem(
                type="move_due",
                activity_id="a1",
                due_on="2026-11-27",
                min_days=0,
                max_days=0,
                reason="Rain tomorrow.",
            ),
            sup.AgendaItem(
                type="pause_activity",
                activity_id="nope",
                due_on="",
                min_days=0,
                max_days=0,
                reason="x",
            ),
        ],
    )
    monkeypatch.setattr(sup, "PROPOSALS_ENABLED", True)
    items = _items()
    items[2]["assessment"] = {"assessed_version": 2, "verdict": "beneficial"}
    table = DictTable(items)
    monkeypatch.setattr(sup, "_table", table)
    monkeypatch.setattr(sup, "_forecast", lambda geo: None)
    monkeypatch.setattr(sup, "run_structured", lambda *a, **k: agenda)
    sup.daily(G, "2026-11-24")
    (action,) = table.of_prefix("ACTION#garden#")  # the unknown activity was dropped
    assert action["type"] == "move_due" and action["reason"] == "Rain tomorrow."


def test_a_newer_proposal_supersedes_the_open_one_on_the_same_activity(monkeypatch):
    table, _ = _env(monkeypatch, [])
    activity = table.items[(f"GARDEN#{G}", "ACTIVITY#a1")]
    first = sup.propose(
        G,
        "move_due",
        {"activity_id": "a1", "due_on": "2026-11-27"},
        "a",
        "r",
        activity,
        "2026-11-24",
    )
    second = sup.propose(
        G,
        "move_due",
        {"activity_id": "a1", "due_on": "2026-11-28"},
        "b",
        "r",
        activity,
        "2026-11-24",
    )
    statuses = {a["action_id"]: a["status"] for a in table.of_prefix("ACTION#garden#")}
    assert statuses == {first: "superseded", second: "proposed"}


def test_specialists_are_consulted_read_only_and_capped(monkeypatch):
    monkeypatch.setattr(
        sup,
        "AGENT_MANIFEST",
        {
            "agronomy": {"arn": "arn:agro", "description": "soil and nutrition"},
            "plant_identifier": {"arn": "arn:pid", "description": "stakes", "internal": True},
        },
    )
    calls = []

    class FakeCore:
        def invoke_agent_runtime(self, agentRuntimeArn, runtimeSessionId, payload):
            import io
            import json

            calls.append(agentRuntimeArn)
            return {"response": io.BytesIO(json.dumps({"result": "ok"}).encode())}

    monkeypatch.setattr(sup, "_agentcore", FakeCore())
    budget = [0]
    tools = sup.specialist_tools(G, None, budget)
    assert [t.tool_name for t in tools] == ["agronomy"]  # internal ones are never offered
    for _ in range(3):
        tools[0]._tool_func(question="humic dose?")
    assert calls == ["arn:agro", "arn:agro"]  # capped at MAX_CONSULTATIONS
