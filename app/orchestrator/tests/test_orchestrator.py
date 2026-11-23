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
    messages: list = []  # Phase 7.5+: _strip_trailing_unresolved_tool_use reads this

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
    # Full response text is stored, not display-truncated (that's now a frontend concern —
    # trace-entry.component.ts's "Show more").
    assert entry["says"] == long_result.strip()
    assert isinstance(entry["ms"], int)


def test_specialist_tool_trace_still_caps_a_pathologically_long_response(monkeypatch):
    huge_result = "x" * 5000
    fake_client = FakeAgentCoreClient(response_body={"result": huge_result})
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    trace: list[dict] = []

    tool_fn = handler._make_specialist_tool("agronomy", "arn:x", "desc", "g1", trace=trace)
    tool_fn("what's wrong?")

    assert len(trace[0]["says"]) == 4000
    assert trace[0]["says"].endswith("…")


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

    result = handler._run_turn(FakeAgent(), "help my plant")

    assert result.reply == "Water more."
    assert result.updated_plan is plan


def test_run_turn_returns_no_plan_when_model_asks_a_question(monkeypatch):
    # A legitimate structured response with updated_plan=None: the model asked a clarifying
    # question instead of proposing yet.
    FakeAgent.structured_output = handler.ChatTurnResult(
        reply="How many hours of direct sun does it get?", updated_plan=None
    )

    result = handler._run_turn(FakeAgent(), "my plant looks sad")

    assert result.updated_plan is None
    assert "sun" in result.reply


def test_run_turn_never_invents_a_plan_when_structuring_fails():
    """Dev, 2026-11-23 (#50): the structured call hit MaxTokensReachedException and the old fallback
    turned the model's raw reasoning into a one-task plan. Now the turn is relayed without a plan
    (the goal then ends as InformationSaved, ADR-0036)."""
    FakeAgent.free_text = "Water it more and check the soil."
    FakeAgent.raise_on_structured = True

    result = handler._run_turn(FakeAgent(), "help")

    assert result.reply == "Water it more and check the soil."
    assert result.updated_plan is None


def test_run_turn_with_no_text_at_all_still_replies():
    FakeAgent.free_text = ""
    FakeAgent.raise_on_structured = True
    result = handler._run_turn(FakeAgent(), "help")
    assert result.reply == handler.STRUCTURING_FAILED_REPLY and result.updated_plan is None


def test_orchestrator_model_has_an_explicit_output_budget(monkeypatch):
    seen = {}
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: seen.update(kw) or object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    monkeypatch.setattr(handler, "_table", FakeTable())
    handler._build_orchestrator_agent("g1", "goal-1", None, None)
    assert seen["max_tokens"] == handler.MAX_TOKENS == 8192
    assert "never narrate your own reasoning" in handler.ORCHESTRATOR_SYSTEM_PROMPT


# --- _load_plan / _load_tasks / _load_latest_message / _write_plan_and_tasks (PA-01/PA-02) -


def test_load_plan_returns_none_when_absent(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    assert handler._load_plan("g1", "goal-1") is None


def test_load_tasks_queries_by_goal_scoped_prefix(monkeypatch):
    fake_table = FakeTable(query_response={"Items": [{"task_id": "t1"}]})
    monkeypatch.setattr(handler, "_table", fake_table)

    tasks = handler._load_tasks("g1", "goal-1")

    assert tasks == [{"task_id": "t1"}]


def test_load_latest_message_queries_with_scan_index_forward_false_and_limit_one(monkeypatch):
    fake_table = FakeTable(query_response={"Items": [{"content": "latest"}]})
    monkeypatch.setattr(handler, "_table", fake_table)

    message = handler._load_latest_message("g1", "goal-1")

    assert message == {"content": "latest"}
    assert len(fake_table.query_calls) == 1
    call = fake_table.query_calls[0]
    assert call["ScanIndexForward"] is False
    assert call["Limit"] == 1


def test_load_latest_message_returns_none_when_no_messages(monkeypatch):
    fake_table = FakeTable(query_response={"Items": []})
    monkeypatch.setattr(handler, "_table", fake_table)

    assert handler._load_latest_message("g1", "goal-1") is None


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
    task_items = [i for i in fake_table.put_calls if i["sk"].startswith("TASK#")]
    assert len(task_items) == 2
    assert all(i["sk"].startswith("TASK#goal-1#") for i in task_items)
    assert {i["title"] for i in task_items} == {"Water", "Mulch"}
    assert all("plant_id" not in i for i in task_items)

    # Phase 6: a best-effort plan.updated Event is also written alongside the Plan.
    event_items = [i for i in fake_table.put_calls if i["sk"].startswith("EVENT#")]
    assert len(event_items) == 1
    assert event_items[0]["type"] == "plan.updated"


def test_write_plan_and_tasks_stamps_plant_id_onto_every_task_when_goal_has_one(monkeypatch):
    fake_table = FakeTable(query_response={"Items": []})
    monkeypatch.setattr(handler, "_table", fake_table)

    plan = handler.PlanProposal(
        success_criteria="Healthy again",
        tasks=[handler.TaskProposal(title="Water", detail="Deeply", scope="plant")],
    )
    handler._write_plan_and_tasks("g1", "goal-1", plan, plant_id="plant-1")

    task_items = [i for i in fake_table.put_calls if i["sk"].startswith("TASK#")]
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


def test_write_plan_and_tasks_survives_event_write_failure(monkeypatch):
    # Phase 6: the plan.updated Event write is best-effort — a failure there must not stop the
    # Plan/Task writes themselves from completing.
    fake_table = FakeTable(query_response={"Items": []})
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(
        handler, "_write_event", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    )

    plan = handler.PlanProposal(
        success_criteria="x", tasks=[handler.TaskProposal(title="Water", detail="d")]
    )
    handler._write_plan_and_tasks("g1", "goal-1", plan)

    assert any(i["sk"] == "PLAN#goal-1" for i in fake_table.put_calls)


# --- _build_orchestrator_agent (Phase 7.5+: SnapshotSessionManager wiring) -----------------


def test_build_orchestrator_agent_wires_session_manager_with_goal_id_as_session_id(monkeypatch):
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    monkeypatch.setattr(handler, "AGENT_STATE_BUCKET_NAME", "tendril-dev-agent-state")
    monkeypatch.setattr(handler, "_table", FakeTable())  # ADR-0032 garden-context read

    handler._build_orchestrator_agent("g1", "goal-1", None, None)

    session_manager = FakeAgent.last_kwargs["session_manager"]
    assert session_manager.session_id == "goal-1"
    # SnapshotSessionManager wraps the given storage in a _NamespacedStorage — the real S3Storage
    # we constructed is nested one level deeper, at ._storage._storage.
    s3_storage = session_manager._storage._storage
    assert s3_storage._bucket == "tendril-dev-agent-state"
    assert s3_storage._prefix == "orchestrator-sessions//"


def test_strip_trailing_unresolved_tool_use_removes_trailing_tool_use_message():
    class _StubAgent:
        messages = [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"toolUse": {"name": "x"}}]},
        ]

    agent = _StubAgent()
    handler._strip_trailing_unresolved_tool_use(agent)

    assert agent.messages == [{"role": "user", "content": [{"text": "hi"}]}]


def test_strip_trailing_unresolved_tool_use_is_a_no_op_for_normal_trailing_text():
    class _StubAgent:
        messages = [
            {"role": "user", "content": [{"text": "hi"}]},
            {"role": "assistant", "content": [{"text": "hello there"}]},
        ]

    agent = _StubAgent()
    original = list(agent.messages)
    handler._strip_trailing_unresolved_tool_use(agent)

    assert agent.messages == original


def test_strip_trailing_unresolved_tool_use_handles_empty_messages():
    class _StubAgent:
        messages: list = []

    agent = _StubAgent()
    handler._strip_trailing_unresolved_tool_use(agent)  # must not raise

    assert agent.messages == []


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
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "GOALMSG#goal-1#2026-01-01T00:00:00#m1",
                    "role": "user",
                    "content": "actually water it less often",
                }
            ]
        },
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

    # The prompt is a full transcript restatement (goal + plan + tasks + message history) on top
    # of the already-resumed session (module docstring, SR-02) — the newest message is in there.
    assert "actually water it less often" in FakeAgent.last_prompt
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

    # The prompt is a full transcript restatement ending in the fixed check-in framing (naming
    # the task) — two query calls: _load_tasks (finds the checked-in task) and _load_messages
    # (the prior conversation history restated on top of the resumed session).
    assert "Water deeply" in FakeAgent.last_prompt
    assert "Soak the soil" in FakeAgent.last_prompt
    assert len(fake_table.query_calls) == 2


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


# --- handle_followup_due (Phase 7.5+) --------------------------------------------------------


def test_handle_followup_due_writes_nudge_message_and_event_and_bumps_due_date(monkeypatch):
    fake_table = FakeTable(
        get_item_response={
            "Item": {
                "task_id": "task-1",
                "title": "Water deeply",
                "status": "pending",
            }
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_followup_due({"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"})

    # No model call at all for a nudge.
    assert FakeAgent.last_kwargs is None

    message_puts = [i for i in fake_table.put_calls if i["sk"].startswith("GOALMSG#")]
    assert len(message_puts) == 1
    assert "Water deeply" in message_puts[0]["content"]
    assert message_puts[0]["role"] == "assistant"

    event_puts = [i for i in fake_table.put_calls if i["sk"].startswith("EVENT#")]
    assert len(event_puts) == 1
    assert event_puts[0]["type"] == "task.followup_due"
    assert event_puts[0]["payload"] == {
        "goalId": "goal-1",
        "taskId": "task-1",
        "taskTitle": "Water deeply",
    }

    assert len(fake_table.update_calls) == 1
    bump = fake_table.update_calls[0]
    assert bump["Key"] == {"pk": "GARDEN#g1", "sk": "TASK#goal-1#task-1"}
    assert bump["ExpressionAttributeValues"][":p"] == "TASK_STATUS#pending"
    # The new due-date must be in the future relative to "now".
    from datetime import UTC, datetime

    new_due = datetime.fromisoformat(bump["ExpressionAttributeValues"][":d"])
    assert new_due > datetime.now(UTC)


def test_handle_followup_due_skips_when_task_already_done(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"task_id": "task-1", "title": "Water", "status": "done"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_followup_due({"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"})

    assert fake_table.put_calls == []
    assert fake_table.update_calls == []


def test_handle_followup_due_skips_when_task_not_found(monkeypatch):
    fake_table = FakeTable(get_item_response={})
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_followup_due({"gardenId": "g1", "goalId": "goal-1", "taskId": "missing"})

    assert fake_table.put_calls == []
    assert fake_table.update_calls == []


def test_handle_followup_due_message_write_failure_does_not_block_due_date_bump(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"task_id": "task-1", "title": "Water", "status": "pending"}}
    )

    def _raise_on_message(Item):
        if Item["sk"].startswith("GOALMSG#"):
            raise RuntimeError("write failed")
        fake_table.put_calls.append(Item)

    monkeypatch.setattr(fake_table, "put_item", _raise_on_message)
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handle_followup_due(
        {"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"}
    )  # no raise

    # The message write failed, but the Event write and the due-date bump still happened.
    assert any(i["sk"].startswith("EVENT#") for i in fake_table.put_calls)
    assert len(fake_table.update_calls) == 1


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


def test_handler_routes_followup_due(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"task_id": "task-1", "title": "Water", "status": "pending"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handler(
        {
            "detail-type": "followup.due",
            "detail": {"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
        },
        None,
    )

    assert len(fake_table.update_calls) == 1
    assert fake_table.update_calls[0]["Key"] == {"pk": "GARDEN#g1", "sk": "TASK#goal-1#task-1"}


def test_handler_ignores_unknown_detail_type(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(handler, "_table", fake_table)

    handler.handler({"detail-type": "something.else", "detail": {}}, None)

    assert fake_table.update_calls == []


# --- ADR-0032 (CP-01): garden context injected into every turn ----------------------------

GARDEN_METADATA = {
    "pk": "GARDEN#g1",
    "sk": "METADATA",
    "name": "Bramhangaon Farm",
    "geolocation": "Kopargaon, Maharashtra",
    "climate_zone": "Semi-arid",
    "vision": "A healthy 105-palm coconut block",
    "context": {
        "soil": "Heavy black calcareous/saline clay",
        "irrigation": "16 mm drip off well water",
        "stake_range": {"min": 1, "max": 105},
        "notification_recipients": [{"name": "Recipient Zed", "e164": "+919800000001"}],
    },
}


def test_render_garden_context_includes_facts_but_never_phone_numbers():
    text = handler._render_garden_context(GARDEN_METADATA)
    assert text.startswith("<garden_context>")
    assert "Soil: Heavy black calcareous/saline clay" in text
    assert "Irrigation: 16 mm drip off well water" in text
    assert "Climate zone: Semi-arid" in text
    assert "#1-#105" in text
    assert "+919800000001" not in text
    assert "Recipient Zed" not in text  # recipient names aren't rendered either


def test_render_garden_context_empty_for_missing_garden():
    assert handler._render_garden_context(None) == ""
    assert handler._render_garden_context({}) == ""


def test_load_garden_context_ignores_non_metadata_items(monkeypatch):
    # Many existing fakes answer every get_item with a Goal item — never render that as a garden.
    monkeypatch.setattr(
        handler, "_table", FakeTable(get_item_response={"Item": {"description": "yellow leaves"}})
    )
    assert handler._load_garden_context("g1") == ""


def test_load_garden_context_degrades_to_empty_on_read_failure(monkeypatch):
    class _BrokenTable(FakeTable):
        def get_item(self, Key):
            raise RuntimeError("throttled")

    monkeypatch.setattr(handler, "_table", _BrokenTable())
    assert handler._load_garden_context("g1") == ""


def test_build_orchestrator_agent_appends_garden_context_to_system_prompt(monkeypatch):
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    monkeypatch.setattr(handler, "AGENT_STATE_BUCKET_NAME", "tendril-dev-agent-state")
    monkeypatch.setattr(
        handler, "_table", FakeTable(responses_by_sk={"METADATA": {"Item": GARDEN_METADATA}})
    )

    handler._build_orchestrator_agent("g1", "goal-1", None, None)

    system_prompt = FakeAgent.last_kwargs["system_prompt"]
    assert system_prompt.startswith(handler.ORCHESTRATOR_SYSTEM_PROMPT)
    assert "<garden_context>" in system_prompt
    assert "calcareous" in system_prompt
    assert "+919800000001" not in system_prompt


def test_build_orchestrator_agent_without_garden_uses_plain_system_prompt(monkeypatch):
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)
    monkeypatch.setattr(handler, "AGENT_STATE_BUCKET_NAME", "tendril-dev-agent-state")
    monkeypatch.setattr(handler, "_table", FakeTable())

    handler._build_orchestrator_agent("g1", "goal-1", None, None)

    assert FakeAgent.last_kwargs["system_prompt"] == handler.ORCHESTRATOR_SYSTEM_PROMPT


def test_specialist_tool_prefixes_garden_context_onto_prompt(monkeypatch):
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    context = handler._render_garden_context(GARDEN_METADATA)

    tool_fn = handler._make_specialist_tool(
        "agronomy", "arn:x", "desc", "g1", garden_context=context
    )
    tool_fn("Why are the lower leaves yellowing on #74?")

    sent = json.loads(fake_client.calls[0]["payload"])["prompt"]
    assert sent.startswith("<garden_context>")
    assert sent.endswith("Why are the lower leaves yellowing on #74?")
    assert "+919800000001" not in sent


def test_build_tools_passes_garden_context_to_every_tool(monkeypatch):
    monkeypatch.setattr(
        handler,
        "AGENT_MANIFEST",
        {
            "agronomy": {"arn": "arn:a", "description": "a"},
            "pruning": {"arn": "arn:p", "description": "p"},
        },
    )
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)

    for tool_fn in handler._build_tools("g1", garden_context="<garden_context>X</garden_context>"):
        tool_fn("q")

    prompts = [json.loads(c["payload"])["prompt"] for c in fake_client.calls]
    assert prompts == ["<garden_context>X</garden_context>\n\nq"] * 2


# --- ADR-0033: analysis copies, bounded wait, unreadable photos --------------------------------


class _SequencedMediaTable(FakeTable):
    """Returns successive Media states on each get_item for MEDIA#… (simulates ingestion
    finishing while the orchestrator polls)."""

    def __init__(self, media_states, others=None):
        super().__init__(responses_by_sk=others or {})
        self._media_states = list(media_states)
        self.media_reads = 0

    def get_item(self, Key):
        if Key["sk"].startswith("MEDIA#"):
            self.media_reads += 1
            state = self._media_states[min(self.media_reads, len(self._media_states)) - 1]
            return {"Item": state}
        return super().get_item(Key)


PENDING = {
    "s3_key": "uploads/g1/m1/plant10.jpg",
    "content_type": "image/jpeg",
    "processing_status": "pending",
}
READY = dict(
    PENDING,
    processing_status="ready",
    detected_format="png",
    analysis_s3_key="derived/g1/m1/analysis.jpg",
    thumb_s3_key="derived/g1/m1/thumb.jpg",
)
FAILED = dict(PENDING, processing_status="failed", processing_error="not an image")


@pytest.fixture
def no_wait(monkeypatch):
    sleeps = []
    monkeypatch.setattr(handler, "_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")
    return sleeps


def test_ready_photo_sends_the_analysis_copy_as_jpeg_even_if_the_original_was_png(
    monkeypatch, no_wait
):
    monkeypatch.setattr(handler, "_table", _SequencedMediaTable([READY]))
    photo = handler._resolve_photo("g1", ["m1"])
    assert photo.image_format == "jpeg"
    assert "derived/g1/m1/analysis.jpg" in photo.url
    assert "plant10.jpg" not in photo.url  # never the original
    assert not photo.unreadable and no_wait == []


def test_pending_photo_is_awaited_until_ingestion_finishes(monkeypatch, no_wait):
    table = _SequencedMediaTable([PENDING, PENDING, READY])
    monkeypatch.setattr(handler, "_table", table)
    photo = handler._resolve_photo("g1", ["m1"])
    assert "derived/g1/m1/analysis.jpg" in photo.url
    assert no_wait == [handler.PHOTO_POLL_SECONDS] * 2


def test_pending_photo_times_out_as_unreadable(monkeypatch, no_wait):
    monkeypatch.setattr(handler, "_table", _SequencedMediaTable([PENDING]))
    photo = handler._resolve_photo("g1", ["m1"])
    assert photo.unreadable and photo.url is None
    assert sum(no_wait) == handler.PHOTO_WAIT_SECONDS


def test_failed_photo_is_unreadable_and_never_sent(monkeypatch, no_wait):
    monkeypatch.setattr(handler, "_table", _SequencedMediaTable([FAILED]))
    photo = handler._resolve_photo("g1", ["m1"])
    assert photo.unreadable and photo.url is None
    assert handler._resolve_image("g1", ["m1"]) is None


def test_goal_with_unreadable_photo_tells_the_agent_and_sends_no_image(monkeypatch, no_wait):
    goal = {
        "garden_id": "g1",
        "goal_id": "goal-1",
        "description": "track growth",
        "media_ids": ["m1"],
    }
    table = _SequencedMediaTable([FAILED], others={"GOAL#goal-1": {"Item": goal}})
    monkeypatch.setattr(handler, "_table", table)
    monkeypatch.setattr(
        handler, "AGENT_MANIFEST", {"vision": {"arn": "arn:vision", "description": "d"}}
    )
    monkeypatch.setattr(handler, "BedrockModel", lambda **kw: object())
    monkeypatch.setattr(handler, "Agent", FakeAgent)

    handler.handle_goal_submitted({"gardenId": "g1", "goalId": "goal-1"})

    assert "could not be read" in FakeAgent.last_prompt
    assert "is attached to this goal" not in FakeAgent.last_prompt
    (vision_tool,) = FakeAgent.last_kwargs["tools"]
    fake_client = FakeAgentCoreClient()
    monkeypatch.setattr(handler, "_agentcore", fake_client)
    vision_tool("what is this?")
    assert "imageUrl" not in json.loads(fake_client.calls[0]["payload"])


def test_steps_in_the_reply_must_be_a_structured_plan():
    """Dev, 2026-11-23: two "Initial check in" goals got numbered steps in the reply but no plan,
    leaving the goal in Decomposing. The prompt and the schema both say steps belong in the plan."""
    assert "those steps ARE the plan" in handler.ORCHESTRATOR_SYSTEM_PROMPT
    field = handler.ChatTurnResult.model_fields["updated_plan"]
    assert "never appear only in the reply text" in field.description
