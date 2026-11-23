"""Orchestrator flows for ADR-0034: the code-run identity step, proposals, and check-ins as a chat
turn. A dict-backed fake table (real begins_with queries) + fake AgentCore + fake Agent; no AWS."""

import io
import json

import orchestrator as handler
import pytest

G = "g1"


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class DictTable:
    """Just enough of a DynamoDB Table: get/put/update(SET a = :x, nested params.feedback)/query."""

    def __init__(self, items):
        self.items = {(i["pk"], i["sk"]): dict(i) for i in items}

    def get_item(self, Key):
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["pk"], Item["sk"])
        if ConditionExpression == "attribute_not_exists(pk)" and key in self.items:
            raise ConditionalCheckFailed()
        self.items[key] = dict(Item)

    def update_item(
        self,
        Key,
        UpdateExpression,
        ExpressionAttributeValues=None,
        ExpressionAttributeNames=None,
        ConditionExpression=None,
        **_,
    ):
        item = self.items.setdefault((Key["pk"], Key["sk"]), dict(Key))
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        if ConditionExpression == "#s = :prop" and item.get("status") != values[":prop"]:
            raise RuntimeError("ConditionalCheckFailed")
        if UpdateExpression.startswith("ADD "):
            field, value = UpdateExpression.removeprefix("ADD ").split()
            item[field] = item.get(field, 0) + values[value]
            return
        set_part = UpdateExpression.split(" REMOVE ")[0].removeprefix("SET ")
        for assignment in set_part.split(","):
            if "=" not in assignment:
                continue
            target, value = (s.strip() for s in assignment.split("="))
            target = names.get(target, target)
            if "." in target:
                parent, child = target.split(".", 1)
                item.setdefault(parent, {})[child] = values[value]
            else:
                item[target] = values[value]

    def query(self, KeyConditionExpression, **_):
        # boto3 condition objects: pk == X AND begins_with(sk, Y)
        pk_cond, sk_cond = KeyConditionExpression._values
        pk, prefix = pk_cond._values[1], sk_cond._values[1]
        found = [dict(v) for (p, s), v in self.items.items() if p == pk and s.startswith(prefix)]
        return {"Items": sorted(found, key=lambda i: i["sk"])}

    def delete_item(self, Key):
        self.items.pop((Key["pk"], Key["sk"]), None)

    def of_prefix(self, prefix):
        return [v for (p, s), v in self.items.items() if s.startswith(prefix)]


class RoutingAgentCore:
    """plant_identifier returns the configured stake read; every other specialist returns text."""

    def __init__(self, stake_json=None, raise_for_identifier=False):
        self.stake_json = stake_json
        self.raise_for_identifier = raise_for_identifier
        self.calls = []

    def invoke_agent_runtime(self, agentRuntimeArn, runtimeSessionId, payload):
        self.calls.append((agentRuntimeArn, json.loads(payload)))
        if agentRuntimeArn == "arn:pid":
            if self.raise_for_identifier:
                raise RuntimeError("runtime 500")
            body = {"result": json.dumps(self.stake_json)}
        else:
            body = {"result": "looks healthy"}
        return {"response": io.BytesIO(json.dumps(body).encode())}


class FakeS3:
    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://bucket/{Params['Key']}?sig"


class _Result:
    def __init__(self, structured=None):
        self.structured_output = structured

    def __str__(self):
        return "free text"


class FakeAgent:
    reply = "Nice work — the drip line looks clear."
    proposed_action = None
    last_prompt = None
    last_kwargs = None
    messages: list = []

    def __init__(self, **kwargs):
        FakeAgent.last_kwargs = kwargs

    def __call__(self, prompt=None, *, structured_output_model=None, **_):
        if structured_output_model is not None:
            return _Result(
                handler.ChatTurnResult(
                    reply=FakeAgent.reply, proposed_action=FakeAgent.proposed_action
                )
            )
        FakeAgent.last_prompt = prompt
        return _Result()


def _garden_items(goal_plant=None, task_plant="p1"):
    items = [
        {
            "pk": f"GARDEN#{G}",
            "sk": "METADATA",
            "garden_id": G,
            "name": "Coconut Baug",
            "context": {"stake_range": {"min": 1, "max": 105}},
        },
        {
            "pk": f"GARDEN#{G}",
            "sk": "GOAL#goal-1",
            "goal_id": "goal-1",
            "description": "track growth",
            "status": "PlanProposed",
            **({"plant_id": goal_plant} if goal_plant else {}),
        },
        {
            "pk": f"GARDEN#{G}",
            "sk": "TASK#goal-1#t1",
            "task_id": "t1",
            "goal_id": "goal-1",
            "title": "Check drip irrigation",
            "status": "pending",
            "plant_id": task_plant,
        },
        {
            "pk": f"GARDEN#{G}",
            "sk": "MEDIA#m10",
            "media_id": "m10",
            "s3_key": "uploads/g1/m10/p.jpg",
            "processing_status": "ready",
            "analysis_s3_key": "derived/g1/m10/analysis.jpg",
        },
    ]
    for n in (1, 10, 17, 71):
        items.append({"pk": f"GARDEN#{G}", "sk": f"PLANT#p{n}", "plant_id": f"p{n}", "stake_no": n})
        items.append(
            {"pk": f"GARDEN#{G}", "sk": f"STAKE#{n:05d}", "plant_id": f"p{n}", "stake_no": n}
        )
    return items


def _user_message(content="", media_ids=None, task_id=None, ts="2026-11-23T10:00:00"):
    item = {
        "pk": f"GARDEN#{G}",
        "sk": f"GOALMSG#goal-1#{ts}#u1",
        "message_id": "u1",
        "goal_id": "goal-1",
        "role": "user",
        "content": content,
        "created_at": ts,
    }
    if media_ids:
        item["media_ids"] = media_ids
    if task_id:
        item["task_id"] = task_id
    return item


@pytest.fixture
def env(monkeypatch):
    FakeAgent.reply = "Nice work — the drip line looks clear."
    FakeAgent.proposed_action = None
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "_sleep", lambda s: None)
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")
    monkeypatch.setattr(
        handler,
        "AGENT_MANIFEST",
        {
            "vision": {"arn": "arn:vision", "description": "vision", "internal": False},
            "plant_identifier": {"arn": "arn:pid", "description": "reads stakes", "internal": True},
        },
    )
    monkeypatch.setattr(handler, "AUTO_LINK_ENABLED", False)

    def make(items, stake_json=None, raise_for_identifier=False):
        table = DictTable(items)
        core = RoutingAgentCore(stake_json, raise_for_identifier)
        monkeypatch.setattr(handler, "_table", table)
        monkeypatch.setattr(handler, "_agentcore", core)
        return table, core

    return make


def _stake(n, confidence="high", alts=()):
    return {
        "stake_visible": True,
        "stake_no": n,
        "confidence": confidence,
        "alternatives": list(alts),
    }


def _actions(table):
    return table.of_prefix("ACTION#goal-1#")


def _assistant(table):
    return [m for m in table.of_prefix("GOALMSG#goal-1#") if m["role"] == "assistant"]


def test_internal_identifier_is_never_offered_to_the_model_as_a_tool(env):
    env(_garden_items())
    names = [t.tool_name for t in handler._build_tools(G)]
    assert names == ["vision"]


def test_matching_checkin_proposes_complete_task_and_links_photo(env):
    table, core = env(_garden_items() + [_user_message("done", ["m10"], "t1")], _stake(1))
    table.items[(f"GARDEN#{G}", "MEDIA#m10")]["s3_key"] = "uploads/g1/m10/p1.jpg"
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})

    (action,) = _actions(table)
    assert action["type"] == "complete_task" and action["status"] == "proposed"
    assert action["params"]["task_id"] == "t1" and action["params"]["media_id"] == "m10"
    assert action["summary"] == "Mark 'Check drip irrigation' done for #1?"
    assert action["params"]["feedback"] == FakeAgent.reply  # becomes task feedback on confirm
    (reply,) = _assistant(table)
    assert reply["action_id"] == action["action_id"]
    # Nothing was marked done — only proposed.
    assert table.items[(f"GARDEN#{G}", "TASK#goal-1#t1")]["status"] == "pending"
    assert table.items[(f"GARDEN#{G}", "MEDIA#m10")]["plant_link_source"] == "user"
    # A check-in photo joins #1's history only when the check-in is confirmed (ADR-0035 §2).
    assert table.of_prefix("PLANTLOG#") == []
    # The identifier read the analysis copy, by code.
    pid_call = next(p for arn, p in core.calls if arn == "arn:pid")
    assert "derived/g1/m10/analysis.jpg" in pid_call["imageUrl"]
    assert "don't say the task is done" in FakeAgent.last_prompt


def test_the_dev_incident_photo_of_10_on_1s_task_asks_instead_of_completing(env):
    table, _ = env(_garden_items() + [_user_message("", ["m10"], "t1")], _stake(10))
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})

    (action,) = _actions(table)
    assert action["type"] == "set_plant"
    assert action["params"] == {"media_id": "m10", "plant_id": "p10"}
    assert action["summary"] == (
        "This photo shows #10, but 'Check drip irrigation' is for #1. Is it #10's photo?"
    )
    # The photo is NOT filed under the task's plant while the question is open.
    assert "plant_link_source" not in table.items[(f"GARDEN#{G}", "MEDIA#m10")]
    assert "Don't treat the photo as #1" in FakeAgent.last_prompt
    assert "mark anything done" in FakeAgent.last_prompt


def test_text_only_checkin_proposes_without_a_photo(env):
    table, core = env(_garden_items() + [_user_message("mulching done today", task_id="t1")])
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    (action,) = _actions(table)
    assert action["type"] == "complete_task"
    assert action["params"] == {
        "task_id": "t1",
        "note": "mulching done today",
        "feedback": FakeAgent.reply,
    }
    assert not [c for c in core.calls if c[0] == "arn:pid"]  # no photo, no stake read


def test_goal_photo_without_a_plant_proposes_linking_when_confident(env):
    items = _garden_items()
    items[1]["media_ids"] = ["m10"]
    table, _ = env(items, _stake(10))
    handler.handle_goal_submitted({"gardenId": G, "goalId": "goal-1"})

    (action,) = _actions(table)
    assert action["type"] == "set_goal_plant"
    assert action["params"] == {"plant_id": "p10", "media_id": "m10"}
    assert "plant_id" not in table.items[(f"GARDEN#{G}", "GOAL#goal-1")]  # not auto-linked
    stored = table.items[(f"GARDEN#{G}", "MEDIA#m10")]["stake_read"]
    assert stored["stake_no"] == 10 and stored["confidence"] == "high"


def test_auto_link_only_when_enabled(env, monkeypatch):
    monkeypatch.setattr(handler, "AUTO_LINK_ENABLED", True)
    items = _garden_items()
    items[1]["media_ids"] = ["m10"]
    table, _ = env(items, _stake(10))
    handler.handle_goal_submitted({"gardenId": G, "goalId": "goal-1"})
    assert _actions(table) == []
    assert table.items[(f"GARDEN#{G}", "GOAL#goal-1")]["plant_id"] == "p10"
    assert table.items[(f"GARDEN#{G}", "MEDIA#m10")]["plant_link_source"] == "auto"
    # ...and joins #10's history, becoming its current photo (ADR-0035).
    (entry,) = table.of_prefix("PLANTLOG#p10#")
    assert entry["entry_id"] == "m10" and entry["source"] == "auto" and entry["goal_id"] == "goal-1"
    plant = table.items[(f"GARDEN#{G}", "PLANT#p10")]
    assert plant["media_id"] == "m10" and plant["history_count"] == 1


def test_unsure_read_asks_with_candidates(env):
    items = _garden_items()
    items[1]["media_ids"] = ["m10"]
    table, _ = env(items, _stake(71, "low", [17]))
    handler.handle_goal_submitted({"gardenId": G, "goalId": "goal-1"})
    (action,) = _actions(table)
    assert action["type"] == "set_goal_plant"
    assert action["summary"].startswith("Is this #71 or #17?")
    assert [c["stake_no"] for c in action["params"]["candidates"]] == [71, 17]


def test_missing_number_inside_the_range_offers_to_create_the_plant(env):
    items = _garden_items()
    items[1]["media_ids"] = ["m10"]
    table, _ = env(items, _stake(59))
    handler.handle_goal_submitted({"gardenId": G, "goalId": "goal-1"})
    (action,) = _actions(table)
    assert action["type"] == "create_plant" and action["params"]["stake_no"] == 59


def test_identifier_failure_never_blocks_the_turn(env):
    table, _ = env(_garden_items() + [_user_message("", ["m10"])], raise_for_identifier=True)
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    assert _actions(table) == []
    assert len(_assistant(table)) == 1


def test_model_can_propose_completing_a_task_by_title(env):
    FakeAgent.proposed_action = handler.ProposedAction(
        type="complete_task", task_title="check drip irrigation"
    )
    table, _ = env(_garden_items() + [_user_message("I checked the drip lines")])
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    (action,) = _actions(table)
    assert action["type"] == "complete_task" and action["params"] == {"task_id": "t1"}


def test_invalid_model_proposals_are_dropped(env):
    FakeAgent.proposed_action = handler.ProposedAction(
        type="complete_task", task_title="Water the moon"
    )
    table, _ = env(_garden_items() + [_user_message("done")])
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    assert _actions(table) == []
    assert "action_id" not in _assistant(table)[0]


def test_a_new_proposal_supersedes_the_open_one(env):
    old = {
        "pk": f"GARDEN#{G}",
        "sk": "ACTION#goal-1#old",
        "action_id": "old",
        "goal_id": "goal-1",
        "type": "complete_task",
        "params": {"task_id": "t1"},
        "status": "proposed",
    }
    table, _ = env(_garden_items() + [old, _user_message("done", task_id="t1")])
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    statuses = sorted(a["status"] for a in _actions(table))
    assert statuses == ["proposed", "superseded"]


def test_an_identical_open_proposal_is_kept_not_raised_again(env):
    """Dev, 2026-11-23: answering "Plant # 13" re-proposed the same link, superseding the first."""
    open_link = {
        "pk": f"GARDEN#{G}",
        "sk": "ACTION#goal-1#first",
        "action_id": "first",
        "goal_id": "goal-1",
        "type": "set_goal_plant",
        "params": {"plant_id": "p10", "media_id": "m10"},
        "status": "proposed",
    }
    table, _ = env(_garden_items() + [open_link])
    action_id = handler._propose_action(
        G, "goal-1", "set_goal_plant", {"plant_id": "p10", "media_id": "m10"}, "Link to #10?"
    )
    assert action_id == "first"
    (action,) = _actions(table)
    assert action["status"] == "proposed"

    # A different proposal still supersedes it.
    other = handler._propose_action(
        G, "goal-1", "set_goal_plant", {"plant_id": "p17", "media_id": "m10"}, "Link to #17?"
    )
    assert other and other != "first"
    assert sorted(a["status"] for a in _actions(table)) == ["proposed", "superseded"]


def test_a_chat_photo_of_the_goals_plant_joins_its_history_once(env):
    """The goal is #10's; a chat photo reads #10 → filed and added to #10's history, dated by the
    photo's capture time. Filing it again (e.g. createGoal already did) adds nothing."""
    items = _garden_items(goal_plant="p10") + [_user_message("new frond", ["m10"])]
    table, _ = env(items, _stake(10))
    table.items[(f"GARDEN#{G}", "MEDIA#m10")]["taken_at"] = "2026-11-20T08:00:00"
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})

    (entry,) = table.of_prefix("PLANTLOG#p10#")
    assert entry["sk"] == "PLANTLOG#p10#2026-11-20#m10"
    assert entry["observed_on"] == "2026-11-20" and entry["source"] == "user"
    assert table.items[(f"GARDEN#{G}", "PLANT#p10")]["latest_photo_on"] == "2026-11-20"

    handler._link_media_to_plant(G, "m10", "p10", "user", goal_id="goal-1")
    assert len(table.of_prefix("PLANTLOG#p10#")) == 1
    assert table.items[(f"GARDEN#{G}", "PLANT#p10")]["history_count"] == 1


# --- ADR-0036: a turn without a plan on a goal without one -> InformationSaved ----------------


def _goal_status(table):
    return table.items[(f"GARDEN#{G}", "GOAL#goal-1")]["status"]


def test_a_check_in_goal_without_a_plan_ends_as_information_saved(env):
    """Dev, 2026-11-23: "Initial check in" for #13 replied without a plan and stayed Decomposing."""
    items = _garden_items(goal_plant="p10")
    items[1]["media_ids"] = ["m10"]
    table, _ = env(items, _stake(10))
    handler.handle_goal_submitted({"gardenId": G, "goalId": "goal-1"})
    assert _goal_status(table) == "InformationSaved"
    assert table.of_prefix("PLAN#") == []
    assert len(table.of_prefix("PLANTLOG#p10#")) == 1  # the photo is saved to #10's history


def test_a_stuck_decomposing_goal_resolves_on_its_next_chat_turn(env):
    items = _garden_items() + [_user_message("anything to worry about?")]
    items[1]["status"] = "Decomposing"
    table, _ = env(items)
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    assert _goal_status(table) == "InformationSaved"


def test_a_goal_with_a_plan_keeps_its_status_on_a_turn_without_one(env):
    plan = {"pk": f"GARDEN#{G}", "sk": "PLAN#goal-1", "plan_id": "goal-1", "status": "Approved"}
    items = _garden_items() + [plan, _user_message("how is it going?")]
    items[1]["status"] = "Approved"
    table, _ = env(items)
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    assert _goal_status(table) == "Approved"


def test_a_later_plan_moves_a_saved_goal_to_plan_proposed(env, monkeypatch):
    items = _garden_items() + [_user_message("please make a plan")]
    items[1]["status"] = "InformationSaved"
    table, _ = env(items)
    plan = handler.PlanProposal(
        success_criteria="Soil stays moist", tasks=[handler.TaskProposal(title="Mulch", detail="d")]
    )
    original = FakeAgent.__call__

    def with_plan(self, prompt=None, *, structured_output_model=None, **kw):
        if structured_output_model is not None:
            return _Result(handler.ChatTurnResult(reply="Here's a plan.", updated_plan=plan))
        return original(self, prompt, **kw)

    monkeypatch.setattr(FakeAgent, "__call__", with_plan)
    handler.handle_goal_message_received({"gardenId": G, "goalId": "goal-1"})
    assert _goal_status(table) == "PlanProposed"
