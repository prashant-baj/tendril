"""Unit tests for the orchestrator Lambda (WS-04).

No live AgentCore/Bedrock calls — `invoke_agent_runtime` is monkeypatched via a fake
bedrock-agentcore client (mirroring app/api/tests' fake-boto3-client convention), and
`Agent`/`BedrockModel` are monkeypatched the same way agents/hello_agent/tests/test_agent.py
already does, so no real Strands agent loop or model call happens in CI.
"""

import io
import json

import orchestrator as handler


class FakeAgentCoreClient:
    def __init__(self, response_body=None, raise_on_invoke=None):
        self.response_body = response_body if response_body is not None else {"result": "ok"}
        self.raise_on_invoke = raise_on_invoke
        self.calls: list[dict] = []

    def invoke_agent_runtime(self, **kwargs):
        if self.raise_on_invoke:
            raise self.raise_on_invoke
        self.calls.append(kwargs)
        return {"response": io.BytesIO(json.dumps(self.response_body).encode("utf-8"))}


class FakeTable:
    def __init__(self, get_item_response=None):
        self._get_item_response = get_item_response if get_item_response is not None else {}
        self.update_calls: list[dict] = []

    def get_item(self, Key):
        return self._get_item_response

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)


class FakeAgent:
    """Stands in for strands.Agent — records construction kwargs, returns a canned result."""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs):
        FakeAgent.last_kwargs = kwargs

    def __call__(self, text):
        return "orchestrator summary"


class FakeFailingAgent:
    def __init__(self, **kwargs):
        pass

    def __call__(self, text):
        raise RuntimeError("model unreachable")


# --- _make_specialist_tool / _build_tools (real InvokeAgentRuntime-calling code path) ------


def test_specialist_tool_calls_invoke_agent_runtime(monkeypatch):
    fake_client = FakeAgentCoreClient(response_body={"result": "hello there"})
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool("hello", "arn:aws:bedrock-agentcore:hello", "desc")
    result = tool_fn("what's wrong with my tomato?")

    assert result == "hello there"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["agentRuntimeArn"] == "arn:aws:bedrock-agentcore:hello"
    assert len(call["runtimeSessionId"]) >= 33
    assert json.loads(call["payload"]) == {"prompt": "what's wrong with my tomato?"}


def test_specialist_tool_propagates_errors(monkeypatch):
    fake_client = FakeAgentCoreClient(raise_on_invoke=RuntimeError("unreachable"))
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool("hello", "arn:x", "desc")
    try:
        tool_fn("prompt")
        raised = False
    except RuntimeError:
        raised = True
    assert raised


def test_build_tools_one_per_manifest_entry(monkeypatch):
    monkeypatch.setattr(
        handler,
        "AGENT_MANIFEST",
        {
            "hello": {"arn": "arn:hello", "description": "d1"},
            "other": {"arn": "arn:o", "description": "d2"},
        },
    )
    tools = handler._build_tools()
    assert len(tools) == 2


# --- handle_goal_submitted (agent-loop wiring; Agent/BedrockModel mocked) ------------------


def test_handle_goal_submitted_happy_path(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    statuses = [c["ExpressionAttributeValues"][":status"] for c in fake_table.update_calls]
    assert statuses == ["Decomposing", "PlanProposed"]
    final_call = fake_table.update_calls[-1]
    assert final_call["ExpressionAttributeValues"][":orchestrator_result"] == "orchestrator summary"


def test_handle_goal_submitted_goal_not_found(monkeypatch):
    fake_table = FakeTable(get_item_response={})
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "missing"})

    assert fake_table.update_calls == []


def test_handle_goal_submitted_agent_failure_reverts_to_intake(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeFailingAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    statuses = [c["ExpressionAttributeValues"][":status"] for c in fake_table.update_calls]
    assert statuses == ["Decomposing", "Intake"]
    final_call = fake_table.update_calls[-1]
    assert "model unreachable" in final_call["ExpressionAttributeValues"][":orchestrator_error"]


# --- handler() routing ----------------------------------------------------------------------


def test_handler_routes_goal_submitted(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handler(
        {"detail-type": "goal.submitted", "detail": {"gardenId": "g1", "goalId": "goal-1"}}, None
    )

    assert len(fake_table.update_calls) == 2


def test_handler_ignores_unknown_detail_type(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handler({"detail-type": "something.else", "detail": {}}, None)

    assert fake_table.update_calls == []
