"""Unit tests for the orchestrator Lambda (WS-04, PA-01/PA-02).

No live AgentCore/Bedrock calls — `invoke_agent_runtime` is monkeypatched via a fake
bedrock-agentcore client (mirroring app/api/tests' fake-boto3-client convention), and
`Agent`/`BedrockModel` are monkeypatched the same way agents/hello_agent/tests/test_agent.py
already does, so no real Strands agent loop or model call happens in CI.
"""

import io
import json

import orchestrator as handler
import pytest


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
    def __init__(self, get_item_response=None, responses_by_sk=None, query_response=None):
        self._get_item_response = get_item_response if get_item_response is not None else {}
        self._responses_by_sk = responses_by_sk or {}
        self._query_response = query_response if query_response is not None else {"Items": []}
        self.update_calls: list[dict] = []
        self.put_calls: list[dict] = []
        self.delete_calls: list[dict] = []
        self.query_calls: list[dict] = []

    def get_item(self, Key):
        if Key.get("sk") in self._responses_by_sk:
            return self._responses_by_sk[Key["sk"]]
        return self._get_item_response

    def update_item(self, **kwargs):
        self.update_calls.append(kwargs)

    def put_item(self, Item):
        self.put_calls.append(Item)

    def delete_item(self, Key):
        self.delete_calls.append(Key)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return self._query_response


class FakeS3:
    def __init__(self):
        self.presign_calls: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        self.presign_calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return f"https://example-bucket.s3.amazonaws.com/{Params['Key']}?presigned=1"


class _FakeResult:
    def __init__(self, text=None, structured_output=None):
        self._text = text
        self.structured_output = structured_output

    def __str__(self):
        return self._text or ""


class FakeAgent:
    """Stands in for strands.Agent. `_run_turn` calls it twice — once normally (tool-calling
    happens for real, `Agent`/tools are still real code) and once with
    `structured_output_model=...` (no new prompt) to extract structure — so this fake supports
    both call shapes. Configure via class attributes (set them in the test) since production
    code constructs `Agent(...)` itself, leaving no room to inject per-test behavior via kwargs.
    """

    last_kwargs: dict | None = None
    last_prompt: str | None = None
    free_text = "orchestrator summary"
    structured_output = None  # a ChatTurnResult, or None to simulate no/failed structuring
    raise_on_structured = False
    raise_on_call: Exception | None = None  # set to make the first (tool-calling) call raise

    def __init__(self, **kwargs):
        FakeAgent.last_kwargs = kwargs

    def __call__(self, prompt=None, *, structured_output_model=None, **kwargs):
        if structured_output_model is not None:
            if FakeAgent.raise_on_structured:
                raise RuntimeError("structured output failed")
            return _FakeResult(structured_output=FakeAgent.structured_output)
        if FakeAgent.raise_on_call:
            raise FakeAgent.raise_on_call
        FakeAgent.last_prompt = prompt
        return _FakeResult(text=FakeAgent.free_text)


@pytest.fixture(autouse=True)
def _reset_fake_agent():
    FakeAgent.last_kwargs = None
    FakeAgent.last_prompt = None
    FakeAgent.free_text = "orchestrator summary"
    FakeAgent.structured_output = None
    FakeAgent.raise_on_structured = False
    FakeAgent.raise_on_call = None
    yield


# --- _make_specialist_tool / _build_tools (real InvokeAgentRuntime-calling code path) ------


def test_specialist_tool_calls_invoke_agent_runtime(monkeypatch):
    fake_client = FakeAgentCoreClient(response_body={"result": "hello there"})
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool(
        "hello", "arn:aws:bedrock-agentcore:hello", "desc", "g1"
    )
    result = tool_fn("what's wrong with my tomato?")

    assert result == "hello there"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["agentRuntimeArn"] == "arn:aws:bedrock-agentcore:hello"
    assert len(call["runtimeSessionId"]) >= 33
    assert json.loads(call["payload"]) == {
        "prompt": "what's wrong with my tomato?",
        "gardenId": "g1",
    }


def test_specialist_tool_degrades_to_error_string_instead_of_raising(monkeypatch):
    # Regression: re-raising here was observed, live, to sometimes propagate past this
    # function's own async/thread boundary inside Strands' agent loop, bypassing
    # handle_goal_submitted's own try/except entirely (an unhandled Lambda invocation error,
    # silently masked by EventBridge's automatic retry) instead of a graceful tool-error result.
    fake_client = FakeAgentCoreClient(raise_on_invoke=RuntimeError("unreachable"))
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool("hello", "arn:x", "desc", "g1")
    result = tool_fn("prompt")

    assert "hello" in result
    assert "unreachable" in result


def test_build_tools_one_per_manifest_entry(monkeypatch):
    monkeypatch.setattr(
        handler,
        "AGENT_MANIFEST",
        {
            "hello": {"arn": "arn:hello", "description": "d1"},
            "other": {"arn": "arn:o", "description": "d2"},
        },
    )
    tools = handler._build_tools("g1")
    assert len(tools) == 2


def test_specialist_tool_includes_image_in_payload_when_provided(monkeypatch):
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool(
        "vision",
        "arn:vision",
        "desc",
        "g1",
        image_url="https://s3.example/photo",
        image_format="jpeg",
    )
    tool_fn("what plant is this?")

    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload == {
        "prompt": "what plant is this?",
        "gardenId": "g1",
        "imageUrl": "https://s3.example/photo",
        "imageFormat": "jpeg",
    }


def test_specialist_tool_omits_image_when_not_provided(monkeypatch):
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool("hello", "arn:hello", "desc", "g1")
    tool_fn("hi")

    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload == {"prompt": "hi", "gardenId": "g1"}


def test_build_tools_passes_image_to_every_tool(monkeypatch):
    monkeypatch.setattr(
        handler, "AGENT_MANIFEST", {"hello": {"arn": "arn:hello", "description": "d"}}
    )
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    (tool_fn,) = handler._build_tools("g1", "https://s3.example/photo", "jpeg")
    tool_fn("prompt")

    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload["imageUrl"] == "https://s3.example/photo"
    assert payload["imageFormat"] == "jpeg"


# --- specialist-trace capture (PA-05: "How this was decided") -----------------------------


def test_specialist_tool_appends_to_trace_on_success(monkeypatch):
    long_result = "Looks like nitrogen deficiency. " + ("Detail. " * 30)
    fake_client = FakeAgentCoreClient(response_body={"result": long_result})
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    trace: list[dict] = []

    tool_fn = handler._make_specialist_tool("agronomy", "arn:x", "desc", "g1", trace=trace)
    tool_fn("what's wrong?")

    assert len(trace) == 1
    entry = trace[0]
    assert entry["agent"] == "agronomy"
    assert entry["says"].startswith("Looks like nitrogen deficiency")
    assert len(entry["says"]) <= 150
    assert isinstance(entry["ms"], int)


def test_specialist_tool_appends_to_trace_on_error(monkeypatch):
    fake_client = FakeAgentCoreClient(raise_on_invoke=RuntimeError("unreachable"))
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    trace: list[dict] = []

    tool_fn = handler._make_specialist_tool("agronomy", "arn:x", "desc", "g1", trace=trace)
    tool_fn("prompt")

    assert len(trace) == 1
    assert trace[0]["agent"] == "agronomy"
    assert "Unavailable" in trace[0]["says"]


def test_specialist_tool_works_without_a_trace_list(monkeypatch):
    # No trace given -> no crash, nothing recorded (default None, existing callers unaffected).
    fake_client = FakeAgentCoreClient(response_body={"result": "hi"})
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    tool_fn = handler._make_specialist_tool("hello", "arn:x", "desc", "g1")
    result = tool_fn("hi")

    assert result == "hi"


def test_summarize_returns_short_text_unchanged():
    assert handler._summarize("short") == "short"


def test_summarize_truncates_long_text_with_ellipsis():
    result = handler._summarize("a" * 200, max_len=150)
    assert len(result) == 150
    assert result.endswith("…")


def test_finalize_trace_appends_orchestrator_entry_with_remaining_time():
    trace = [{"agent": "agronomy", "says": "ok", "ms": 100}]
    turn = handler.ChatTurnResult(reply="All good.", updated_plan=None)

    handler._finalize_trace(trace, turn, turn_ms=400)

    assert len(trace) == 2
    orch = trace[-1]
    assert orch["agent"] == "orchestrator"
    assert orch["is_orchestrator"] is True
    assert orch["ms"] == 300
    assert orch["says"] == "All good."


def test_finalize_trace_floors_ms_at_zero():
    # Guards against a negative "orchestrator" duration if timing is ever inconsistent — should
    # not happen in practice (turn_ms wraps the whole turn, specialist ms is a subset of it).
    trace = [{"agent": "agronomy", "says": "ok", "ms": 500}]
    turn = handler.ChatTurnResult(reply="ok", updated_plan=None)

    handler._finalize_trace(trace, turn, turn_ms=100)

    assert trace[-1]["ms"] == 0


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


# --- _run_turn (PA-01/PA-02: structured-output extraction + fail-open fallback) ------------


def test_run_turn_returns_structured_output_when_model_provides_it(monkeypatch):
    plan = handler.PlanProposal(
        success_criteria="Leaves green again",
        tasks=[handler.TaskProposal(title="Water deeply", detail="Soak the soil", scope="plant")],
    )
    FakeAgent.free_text = "here's what I think"
    FakeAgent.structured_output = handler.ChatTurnResult(reply="Water more.", updated_plan=plan)

    result = handler._run_turn(FakeAgent(), "help my plant", synthesize_fallback_plan=True)

    assert result.reply == "Water more."
    assert result.updated_plan is plan


def test_run_turn_returns_no_plan_when_model_asks_a_question(monkeypatch):
    # A legitimate structured response with updated_plan=None (the model asked a clarifying
    # question instead of proposing yet) must NOT trigger the fallback-plan synthesis path.
    FakeAgent.structured_output = handler.ChatTurnResult(
        reply="How many hours of direct sun does it get?", updated_plan=None
    )

    result = handler._run_turn(FakeAgent(), "my plant looks sad", synthesize_fallback_plan=True)

    assert result.updated_plan is None
    assert "sun" in result.reply


def test_run_turn_falls_back_to_one_task_when_structuring_fails_and_no_plan_exists():
    FakeAgent.free_text = "Water it more and check the soil."
    FakeAgent.raise_on_structured = True

    result = handler._run_turn(FakeAgent(), "help", synthesize_fallback_plan=True)

    assert result.reply == "Water it more and check the soil."
    assert result.updated_plan is not None
    assert len(result.updated_plan.tasks) == 1
    assert result.updated_plan.tasks[0].detail == "Water it more and check the soil."


def test_run_turn_does_not_clobber_existing_plan_when_structuring_fails():
    # synthesize_fallback_plan=False: a Plan already exists for this goal — a transient
    # structuring failure must relay the reply without touching it, never overwrite it.
    FakeAgent.free_text = "Sure, that sounds reasonable."
    FakeAgent.raise_on_structured = True

    result = handler._run_turn(
        FakeAgent(), "does that sound right?", synthesize_fallback_plan=False
    )

    assert result.reply == "Sure, that sounds reasonable."
    assert result.updated_plan is None


# --- _load_plan / _load_tasks / _load_messages / _write_plan_and_tasks (PA-01/PA-02) -------


def test_load_plan_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    assert handler._load_plan("g1", "goal-1") is None


def test_load_tasks_queries_by_goal_scoped_prefix(monkeypatch):
    fake_table = FakeTable(query_response={"Items": [{"task_id": "t1"}]})
    monkeypatch.setattr(handler, "_table", fake_table)

    tasks = handler._load_tasks("g1", "goal-1")

    assert tasks == [{"task_id": "t1"}]


def test_write_plan_and_tasks_clears_previous_tasks_then_writes_fresh_set(monkeypatch):
    fake_table = FakeTable(
        query_response={"Items": [{"pk": "GARDEN#g1", "sk": "TASK#goal-1#old-task"}]}
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    plan = handler.PlanProposal(
        success_criteria="Healthy again",
        tasks=[
            handler.TaskProposal(title="Water", detail="Deeply", scope="plant"),
            handler.TaskProposal(title="Mulch", detail="Around base", scope="plant"),
        ],
    )
    handler._write_plan_and_tasks("g1", "goal-1", plan)

    assert fake_table.delete_calls == [{"pk": "GARDEN#g1", "sk": "TASK#goal-1#old-task"}]
    plan_items = [i for i in fake_table.put_calls if i["sk"] == "PLAN#goal-1"]
    assert plan_items == [
        {
            "pk": "GARDEN#g1",
            "sk": "PLAN#goal-1",
            "plan_id": "goal-1",
            "goal_id": "goal-1",
            "success_criteria": "Healthy again",
            "status": "PlanProposed",
        }
    ]
    task_items = [i for i in fake_table.put_calls if i["sk"] != "PLAN#goal-1"]
    assert len(task_items) == 2
    assert all(i["sk"].startswith("TASK#goal-1#") for i in task_items)
    assert {i["title"] for i in task_items} == {"Water", "Mulch"}
    assert all("plant_id" not in i for i in task_items)


def test_write_plan_and_tasks_stamps_plant_id_onto_every_task_when_goal_has_one(monkeypatch):
    fake_table = FakeTable(query_response={"Items": []})
    monkeypatch.setattr(handler, "_table", fake_table)

    plan = handler.PlanProposal(
        success_criteria="Healthy again",
        tasks=[handler.TaskProposal(title="Water", detail="Deeply", scope="plant")],
    )
    handler._write_plan_and_tasks("g1", "goal-1", plan, plant_id="plant-1")

    task_items = [i for i in fake_table.put_calls if i["sk"] != "PLAN#goal-1"]
    assert task_items[0]["plant_id"] == "plant-1"


def test_write_plan_and_tasks_preserves_done_tasks_across_a_revision(monkeypatch):
    # PA-05: a revision must never wipe a completed check-in's status/media/feedback — only
    # not-yet-done tasks get cleared and replaced.
    fake_table = FakeTable(
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "TASK#goal-1#done-task",
                    "status": "done",
                    "media_id": "media-1",
                },
                {"pk": "GARDEN#g1", "sk": "TASK#goal-1#pending-task", "status": "pending"},
            ]
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    plan = handler.PlanProposal(
        success_criteria="Healthy again",
        tasks=[handler.TaskProposal(title="New task", detail="d", scope="plant")],
    )
    handler._write_plan_and_tasks("g1", "goal-1", plan)

    assert fake_table.delete_calls == [{"pk": "GARDEN#g1", "sk": "TASK#goal-1#pending-task"}]


def test_write_plan_and_tasks_stores_trace_on_plan_item_when_given(monkeypatch):
    fake_table = FakeTable(query_response={"Items": []})
    monkeypatch.setattr(handler, "_table", fake_table)
    trace = [{"agent": "agronomy", "says": "ok", "ms": 100}]

    plan = handler.PlanProposal(success_criteria="x", tasks=[])
    handler._write_plan_and_tasks("g1", "goal-1", plan, trace=trace)

    plan_item = next(i for i in fake_table.put_calls if i["sk"] == "PLAN#goal-1")
    assert plan_item["trace"] == trace


def test_write_plan_and_tasks_omits_trace_when_not_given(monkeypatch):
    fake_table = FakeTable(query_response={"Items": []})
    monkeypatch.setattr(handler, "_table", fake_table)

    plan = handler.PlanProposal(success_criteria="x", tasks=[])
    handler._write_plan_and_tasks("g1", "goal-1", plan)

    plan_item = next(i for i in fake_table.put_calls if i["sk"] == "PLAN#goal-1")
    assert "trace" not in plan_item


# --- handle_goal_submitted (agent-loop wiring; Agent/BedrockModel mocked) ------------------


def test_handle_goal_submitted_happy_path(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    plan = handler.PlanProposal(
        success_criteria="Fixed", tasks=[handler.TaskProposal(title="Water", detail="d")]
    )
    FakeAgent.structured_output = handler.ChatTurnResult(reply="Water more.", updated_plan=plan)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    statuses = [c["ExpressionAttributeValues"][":status"] for c in fake_table.update_calls]
    assert statuses == ["Decomposing", "PlanProposed"]
    # A user Message (the goal description) and an assistant Message (the reply) were written.
    message_items = [i for i in fake_table.put_calls if i["sk"].startswith("GOALMSG#goal-1#")]
    assert [m["role"] for m in message_items] == ["user", "assistant"]
    assert message_items[0]["content"] == "help"
    assert message_items[1]["content"] == "Water more."
    # The Plan itself was written too.
    assert any(i["sk"] == "PLAN#goal-1" for i in fake_table.put_calls)


def test_handle_goal_submitted_propagates_goal_plant_id_onto_tasks(monkeypatch):
    fake_table = FakeTable(
        get_item_response={
            "Item": {
                "garden_id": "g1",
                "goal_id": "goal-1",
                "description": "help",
                "plant_id": "plant-1",
            }
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    plan = handler.PlanProposal(
        success_criteria="Fixed", tasks=[handler.TaskProposal(title="Water", detail="d")]
    )
    FakeAgent.structured_output = handler.ChatTurnResult(reply="Water more.", updated_plan=plan)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    task_items = [i for i in fake_table.put_calls if i["sk"].startswith("TASK#")]
    assert task_items[0]["plant_id"] == "plant-1"


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
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    (vision_tool,) = FakeAgent.last_kwargs["tools"]
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    vision_tool("what plant is this?")
    payload = json.loads(fake_client.calls[0]["payload"])
    assert payload["imageUrl"].endswith("g1/media-1/tomato.jpg?presigned=1")
    assert payload["imageFormat"] == "jpeg"


def test_handle_goal_submitted_tells_agent_a_photo_is_attached(monkeypatch):
    # Regression: the top-level agent only sees the message text it's called with — it has no
    # other way of knowing a photo is attached, and won't reliably call vision without being
    # told explicitly (observed live: it asked the user to share a photo that was already there).
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {
                    "garden_id": "g1",
                    "goal_id": "goal-1",
                    "description": "leaves not healthy",
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
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    assert "leaves not healthy" in FakeAgent.last_prompt
    assert "photo" in FakeAgent.last_prompt.lower()
    assert "attached" in FakeAgent.last_prompt.lower()


def test_handle_goal_submitted_omits_photo_note_when_no_media(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    assert FakeAgent.last_prompt == "help"


def test_handle_goal_submitted_goal_not_found(monkeypatch):
    fake_table = FakeTable(get_item_response={})
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "missing"})

    assert fake_table.update_calls == []


def test_handle_goal_submitted_persists_trace_when_plan_is_produced(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    plan = handler.PlanProposal(
        success_criteria="Fixed", tasks=[handler.TaskProposal(title="Water", detail="d")]
    )
    FakeAgent.structured_output = handler.ChatTurnResult(reply="Water more.", updated_plan=plan)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    plan_item = next(i for i in fake_table.put_calls if i["sk"] == "PLAN#goal-1")
    assert "trace" in plan_item
    assert plan_item["trace"][-1]["agent"] == "orchestrator"
    assert plan_item["trace"][-1]["says"] == "Water more."


def test_handle_goal_submitted_omits_trace_when_no_plan_produced(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.structured_output = handler.ChatTurnResult(reply="Tell me more?", updated_plan=None)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    assert not any(i["sk"] == "PLAN#goal-1" for i in fake_table.put_calls)


def test_handle_goal_submitted_agent_failure_reverts_to_intake(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.raise_on_call = RuntimeError("model unreachable")

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    statuses = [c["ExpressionAttributeValues"][":status"] for c in fake_table.update_calls]
    assert statuses == ["Decomposing", "Intake"]
    final_call = fake_table.update_calls[-1]
    assert "model unreachable" in final_call["ExpressionAttributeValues"][":orchestrator_error"]


# --- handle_goal_message_received (PA-02: conversational plan approval, no live session) ---


def test_handle_goal_message_received_revises_plan(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "leaves yellow"}
            },
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "Leaves green",
                    "status": "PlanProposed",
                }
            },
        },
        query_response={"Items": []},
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    revised_plan = handler.PlanProposal(
        success_criteria="Leaves green",
        tasks=[handler.TaskProposal(title="Water less", detail="Every 3 days")],
    )
    FakeAgent.structured_output = handler.ChatTurnResult(
        reply="Sure, I've reduced the watering frequency.", updated_plan=revised_plan
    )

    handler.handle_goal_message_received({"gardenId": "g1", "goalId": "goal-1"})

    assert "leaves yellow" in FakeAgent.last_prompt
    assert "Leaves green" in FakeAgent.last_prompt
    assert any(i["sk"] == "PLAN#goal-1" for i in fake_table.put_calls)
    statuses = [c["ExpressionAttributeValues"][":status"] for c in fake_table.update_calls]
    assert statuses == ["PlanProposed"]


def test_handle_goal_message_received_asks_a_question_without_touching_the_plan(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "leaves yellow"}
            },
            # No "PLAN#goal-1" entry — falls through to the default {} (no "Item" key), matching
            # real DynamoDB's actual "no such item" response shape.
        },
        query_response={"Items": []},
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.structured_output = handler.ChatTurnResult(
        reply="How much sun does it get?", updated_plan=None
    )

    handler.handle_goal_message_received({"gardenId": "g1", "goalId": "goal-1"})

    assert not any(i["sk"] == "PLAN#goal-1" for i in fake_table.put_calls)
    message_items = [i for i in fake_table.put_calls if "GOALMSG#" in i["sk"]]
    assert message_items[-1]["content"] == "How much sun does it get?"


def test_handle_goal_message_received_goal_not_found(monkeypatch):
    fake_table = FakeTable(get_item_response={})
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_goal_message_received({"gardenId": "g1", "goalId": "missing"})

    assert fake_table.put_calls == []
    assert fake_table.update_calls == []


def test_handle_goal_message_received_failure_writes_apologetic_message(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}},
        query_response={"Items": []},
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.raise_on_call = RuntimeError("model unreachable")

    handler.handle_goal_message_received({"gardenId": "g1", "goalId": "goal-1"})

    message_items = [i for i in fake_table.put_calls if "GOALMSG#" in i["sk"]]
    assert len(message_items) == 1
    assert "model unreachable" in message_items[0]["content"]


# --- handle_task_checkin_received (PA-05: check-in feedback + optional plan revision) ------


def test_handle_task_checkin_received_writes_task_feedback(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "leaves yellow"}
            },
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "Leaves green",
                    "status": "PlanProposed",
                }
            },
        },
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "TASK#goal-1#task-1",
                    "task_id": "task-1",
                    "title": "Water deeply",
                    "detail": "Soak the soil",
                    "status": "done",
                    "media_id": "media-1",
                }
            ]
        },
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.structured_output = handler.ChatTurnResult(
        reply="Looking good, keep it up!", updated_plan=None
    )

    handler.handle_task_checkin_received({"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"})

    feedback_calls = [c for c in fake_table.update_calls if c["Key"]["sk"] == "TASK#goal-1#task-1"]
    assert len(feedback_calls) == 1
    assert feedback_calls[0]["ExpressionAttributeValues"][":f"] == "Looking good, keep it up!"


def test_handle_task_checkin_received_with_plan_revision_preserves_checked_in_task(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "leaves yellow"}
            },
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "Leaves green",
                    "status": "PlanProposed",
                }
            },
        },
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "TASK#goal-1#task-1",
                    "task_id": "task-1",
                    "title": "Water deeply",
                    "detail": "Soak the soil",
                    "status": "done",
                    "media_id": "media-1",
                }
            ]
        },
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    revised_plan = handler.PlanProposal(
        success_criteria="Leaves green",
        tasks=[handler.TaskProposal(title="Reduce watering", detail="Every 3 days")],
    )
    FakeAgent.structured_output = handler.ChatTurnResult(
        reply="This isn't improving — I've adjusted the watering.", updated_plan=revised_plan
    )

    handler.handle_task_checkin_received({"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"})

    # The already-done task was never deleted — a revision only clears not-yet-done tasks.
    assert not any(c["sk"] == "TASK#goal-1#task-1" for c in fake_table.delete_calls)
    feedback_calls = [c for c in fake_table.update_calls if c["Key"]["sk"] == "TASK#goal-1#task-1"]
    assert (
        feedback_calls[-1]["ExpressionAttributeValues"][":f"]
        == "This isn't improving — I've adjusted the watering."
    )
    new_tasks = [
        i
        for i in fake_table.put_calls
        if i["sk"].startswith("TASK#goal-1#") and i["sk"] != "TASK#goal-1#task-1"
    ]
    assert len(new_tasks) == 1
    assert new_tasks[0]["title"] == "Reduce watering"


def test_handle_task_checkin_received_goal_not_found(monkeypatch):
    fake_table = FakeTable(get_item_response={})
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_task_checkin_received({"gardenId": "g1", "goalId": "missing", "taskId": "t1"})

    assert fake_table.update_calls == []


def test_handle_task_checkin_received_task_not_found(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}
            },
        },
        query_response={"Items": []},
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_task_checkin_received(
        {"gardenId": "g1", "goalId": "goal-1", "taskId": "missing-task"}
    )

    assert fake_table.update_calls == []


def test_handle_task_checkin_received_agent_failure_writes_no_feedback(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}
            },
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "x",
                    "status": "PlanProposed",
                }
            },
        },
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "TASK#goal-1#task-1",
                    "task_id": "task-1",
                    "title": "Water",
                    "detail": "d",
                    "status": "done",
                }
            ]
        },
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.raise_on_call = RuntimeError("model unreachable")

    handler.handle_task_checkin_received({"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"})

    assert fake_table.update_calls == []


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


def test_handler_routes_goal_message_received(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}},
        query_response={"Items": []},
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handler(
        {
            "detail-type": "goal.message.received",
            "detail": {"gardenId": "g1", "goalId": "goal-1"},
        },
        None,
    )

    assert any("GOALMSG#" in i["sk"] for i in fake_table.put_calls)


def test_handler_routes_task_checkin_received(monkeypatch):
    fake_table = FakeTable(
        responses_by_sk={
            "GOAL#goal-1": {
                "Item": {"garden_id": "g1", "goal_id": "goal-1", "description": "help"}
            },
        },
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "TASK#goal-1#task-1",
                    "task_id": "task-1",
                    "title": "Water",
                    "detail": "d",
                    "status": "done",
                }
            ]
        },
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    FakeAgent.structured_output = handler.ChatTurnResult(reply="Looks good.", updated_plan=None)

    handler.handler(
        {
            "detail-type": "task.checkin.received",
            "detail": {"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
        },
        None,
    )

    assert any(c["Key"]["sk"] == "TASK#goal-1#task-1" for c in fake_table.update_calls)


def test_handler_ignores_unknown_detail_type(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handler({"detail-type": "something.else", "detail": {}}, None)

    assert fake_table.update_calls == []
