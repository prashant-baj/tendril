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
    def __init__(self, get_item_response=None, responses_by_sk=None):
        self._get_item_response = get_item_response if get_item_response is not None else {}
        self._responses_by_sk = responses_by_sk or {}
        self.update_calls: list[dict] = []

    def get_item(self, Key):
        if Key.get("sk") in self._responses_by_sk:
            return self._responses_by_sk[Key["sk"]]
        return self._get_item_response

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)


class FakeS3:
    def __init__(self):
        self.presign_calls: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return f"https://example-bucket.s3.amazonaws.com/{Params['Key']}?presigned=1"


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


def test_specialist_tool_includes_image_in_payload_when_provided(monkeypatch):
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool(
        "vision", "arn:vision", "desc", image_url="https://s3.example/photo", image_format="jpeg"
    )
    tool_fn("what plant is this?")

    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload == {
        "prompt": "what plant is this?",
        "imageUrl": "https://s3.example/photo",
        "imageFormat": "jpeg",
    }


def test_specialist_tool_omits_image_when_not_provided(monkeypatch):
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool("hello", "arn:hello", "desc")
    tool_fn("hi")

    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload == {"prompt": "hi"}


def test_build_tools_passes_image_to_every_tool(monkeypatch):
    monkeypatch.setattr(
        handler, "AGENT_MANIFEST", {"hello": {"arn": "arn:hello", "description": "d"}}
    )
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    (tool_fn,) = handler._build_tools("https://s3.example/photo", "jpeg")
    tool_fn("prompt")

    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload["imageUrl"] == "https://s3.example/photo"
    assert payload["imageFormat"] == "jpeg"


# --- _resolve_image (data-architecture.md §4 — presigned GET, no specialist S3 IAM) --------


def test_resolve_image_returns_none_when_goal_has_no_media():
    assert handler._resolve_image("g1", []) is None


def test_resolve_image_returns_none_when_media_record_missing(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    assert handler._resolve_image("g1", ["media-1"]) is None


def test_resolve_image_returns_none_for_unrecognized_content_type(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(get_item_response={"Item": {"s3_key": "k", "content_type": "application/pdf"}}),
    )
    assert handler._resolve_image("g1", ["media-1"]) is None


def test_resolve_image_returns_presigned_url_and_format(monkeypatch):
    fake_table = FakeTable(
        get_item_response={
            "Item": {"s3_key": "g1/media-1/tomato.jpg", "content_type": "image/jpeg"}
        }
    )
    fake_s3 = FakeS3()
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_s3", fake_s3)
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")

    result = handler._resolve_image("g1", ["media-1"])

    assert result is not None
    url, image_format = result
    assert image_format == "jpeg"
    assert "g1/media-1/tomato.jpg" in url
    assert fake_s3.presign_calls[0]["Params"] == {
        "Bucket": "tendril-dev-media",
        "Key": "g1/media-1/tomato.jpg",
    }


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


def test_handle_goal_submitted_with_photo_passes_image_url_to_tools(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {
                    "garden_id": "g1",
                    "goal_id": "goal-1",
                    "description": "what's wrong with this?",
                    "media_ids": ["media-1"],
                }
            },
            "MEDIA#media-1": {
                "Item": {"s3_key": "g1/media-1/tomato.jpg", "content_type": "image/jpeg"}
            },
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")
    monkeypatch.setattr(
        handler, "AGENT_MANIFEST", {"vision": {"arn": "arn:vision", "description": "d"}}
    )
    monkeypatch.setattr(handler, "_agentcore", FakeAgentCoreClient())
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())

    captured_tools = {}

    class CapturingAgent:
        def __init__(self, **kwargs):
            captured_tools["tools"] = kwargs["tools"]

        def __call__(self, text):
            return "orchestrator summary"

    monkeypatch.setattr(handler, "Agent", CapturingAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    (vision_tool,) = captured_tools["tools"]
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    vision_tool("what plant is this?")
    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload["imageUrl"].endswith("g1/media-1/tomato.jpg?presigned=1")
    assert payload["imageFormat"] == "jpeg"


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
