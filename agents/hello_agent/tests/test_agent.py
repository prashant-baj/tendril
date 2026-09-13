"""Unit tests for the hello agent (PG-05).

These test the deterministic logic — prompt/guardrail resolution, fail-open fallback,
and input validation — without any AWS calls (boto3 and the model are mocked). Skips
cleanly if the agent's runtime deps aren't installed (e.g. a minimal CI lane).
"""

import importlib
import json

import pytest

# The module imports strands / bedrock_agentcore at top level; skip if unavailable.
pytest.importorskip("strands")
pytest.importorskip("bedrock_agentcore")

agent = importlib.import_module("agent")


# --- input validation (deterministic floor) ---------------------------------


def test_resolve_user_message_defaults_when_missing():
    assert agent.resolve_user_message({}) == agent.DEFAULT_USER_MESSAGE


def test_resolve_user_message_defaults_on_blank_or_nonstring():
    assert agent.resolve_user_message({"prompt": "   "}) == agent.DEFAULT_USER_MESSAGE
    assert agent.resolve_user_message({"prompt": 123}) == agent.DEFAULT_USER_MESSAGE


def test_resolve_user_message_passes_through_real_prompt():
    assert agent.resolve_user_message({"prompt": "why are my basil leaves yellow?"}) == (
        "why are my basil leaves yellow?"
    )


def test_resolve_user_message_rejects_non_dict():
    with pytest.raises(ValueError):
        agent.resolve_user_message("not a dict")


def test_resolve_content_text_only_when_no_image(monkeypatch):
    assert agent.resolve_content({"prompt": "why are my basil leaves yellow?"}) == (
        "why are my basil leaves yellow?"
    )


def test_resolve_content_builds_multimodal_blocks_when_image_present(monkeypatch):
    monkeypatch.setattr(agent, "fetch_image_bytes", lambda url: b"fake-bytes")
    content = agent.resolve_content(
        {
            "prompt": "what plant is this?",
            "imageUrl": "https://s3.example/photo",
            "imageFormat": "jpeg",
        }
    )
    assert content == [
        {"image": {"format": "jpeg", "source": {"bytes": b"fake-bytes"}}},
        {"text": "what plant is this?"},
    ]


def test_resolve_content_falls_back_to_text_on_fetch_failure(monkeypatch):
    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(agent, "fetch_image_bytes", boom)
    content = agent.resolve_content(
        {
            "prompt": "what plant is this?",
            "imageUrl": "https://s3.example/photo",
            "imageFormat": "jpeg",
        }
    )
    assert content == "what plant is this?"


def test_resolve_content_ignores_unrecognized_image_format(monkeypatch):
    monkeypatch.setattr(
        agent,
        "fetch_image_bytes",
        lambda url: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    content = agent.resolve_content(
        {"prompt": "hi", "imageUrl": "https://s3.example/photo", "imageFormat": "bmp"}
    )
    assert content == "hi"


# --- prompt resolution (ADR-0006) -------------------------------------------


def test_load_system_prompt_uses_default_when_no_name(monkeypatch):
    monkeypatch.setattr(agent, "PROMPT_NAME", None)
    assert agent.load_system_prompt() == agent.DEFAULT_SYSTEM_PROMPT


def test_load_system_prompt_falls_back_on_error(monkeypatch):
    monkeypatch.setattr(agent, "PROMPT_NAME", "tendril-dev-hello-system")

    def boom(*_a, **_k):
        raise RuntimeError("no AWS here")

    monkeypatch.setattr(agent.boto3, "client", boom)
    assert agent.load_system_prompt() == agent.DEFAULT_SYSTEM_PROMPT


def test_load_system_prompt_returns_fetched_text(monkeypatch):
    monkeypatch.setattr(agent, "PROMPT_NAME", "tendril-dev-hello-system")

    class FakeClient:
        def list_prompts(self, **_):
            return {"promptSummaries": [{"name": "tendril-dev-hello-system", "id": "PID"}]}

        def get_prompt(self, promptIdentifier=None):
            assert promptIdentifier == "PID"
            return {
                "defaultVariant": "default",
                "variants": [
                    {
                        "name": "default",
                        "templateConfiguration": {"text": {"text": "FETCHED PROMPT"}},
                    }
                ],
            }

    monkeypatch.setattr(agent.boto3, "client", lambda *a, **k: FakeClient())
    assert agent.load_system_prompt() == "FETCHED PROMPT"


def test_resolve_prompt_id_paginates(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = 0

        def list_prompts(self, **kwargs):
            self.calls += 1
            if "nextToken" not in kwargs:
                return {"promptSummaries": [{"name": "other", "id": "X"}], "nextToken": "t2"}
            return {"promptSummaries": [{"name": "wanted", "id": "GOOD"}]}

    c = FakeClient()
    assert agent._resolve_prompt_id(c, "wanted") == "GOOD"
    assert c.calls == 2


# --- guardrail resolution + model build (ADR-0008) --------------------------


def test_resolve_guardrail_id_found_and_missing():
    class FakeClient:
        def list_guardrails(self, **_):
            return {"guardrails": [{"name": "tendril-dev-hello-guardrail", "id": "GRID"}]}

    c = FakeClient()
    assert agent._resolve_guardrail_id(c, "tendril-dev-hello-guardrail") == "GRID"
    assert agent._resolve_guardrail_id(c, "nope") is None


def test_build_model_without_guardrail(monkeypatch):
    monkeypatch.setattr(agent, "GUARDRAIL_NAME", None)
    monkeypatch.setattr(agent, "MODEL_ID", "global.amazon.nova-2-lite-v1:0")
    captured = {}
    monkeypatch.setattr(agent, "BedrockModel", lambda **kw: captured.update(kw) or object())
    agent.build_model()
    assert captured == {"model_id": "global.amazon.nova-2-lite-v1:0"}


def test_build_model_attaches_resolved_guardrail(monkeypatch):
    monkeypatch.setattr(agent, "GUARDRAIL_NAME", "tendril-dev-hello-guardrail")
    monkeypatch.setattr(agent, "MODEL_ID", "m")

    class FakeClient:
        def list_guardrails(self, **_):
            return {"guardrails": [{"name": "tendril-dev-hello-guardrail", "id": "GRID"}]}

    monkeypatch.setattr(agent.boto3, "client", lambda *a, **k: FakeClient())
    captured = {}
    monkeypatch.setattr(agent, "BedrockModel", lambda **kw: captured.update(kw) or object())
    agent.build_model()
    assert captured["guardrail_id"] == "GRID"
    assert captured["guardrail_version"] == "DRAFT"


def test_build_model_fail_open_when_guardrail_unresolvable(monkeypatch):
    monkeypatch.setattr(agent, "GUARDRAIL_NAME", "tendril-dev-hello-guardrail")
    monkeypatch.setattr(agent, "MODEL_ID", "m")

    def boom(*_a, **_k):
        raise RuntimeError("no AWS")

    monkeypatch.setattr(agent.boto3, "client", boom)
    captured = {}
    monkeypatch.setattr(agent, "BedrockModel", lambda **kw: captured.update(kw) or object())
    agent.build_model()
    # fail-open: model still built, no guardrail attached
    assert "guardrail_id" not in captured


# --- entrypoint --------------------------------------------------------------


def test_invoke_returns_typed_result(monkeypatch):
    monkeypatch.setattr(agent, "_get_model", lambda: object())
    monkeypatch.setattr(agent, "_get_system_prompt", lambda: "system")

    class FakeAgent:
        def __init__(self, **_):
            pass

        def __call__(self, message):
            return f"echo: {message}"

    monkeypatch.setattr(agent, "Agent", FakeAgent)
    out = agent.invoke({"prompt": "hello there"})
    assert out == {"result": "echo: hello there"}


def test_invoke_retries_without_memory_when_agent_init_fails(monkeypatch):
    # Regression: BedrockKnowledgeBaseStore.initialize() runs as a plugin hook inside
    # Agent(...)'s own constructor and raises by design on failure (e.g. a permission gap) —
    # a real AccessDeniedException crashed the whole turn here before this retry existed.
    monkeypatch.setattr(agent, "_get_model", lambda: object())
    monkeypatch.setattr(agent, "_get_system_prompt", lambda: "system")
    monkeypatch.setattr(agent, "build_memory_manager", lambda garden_id: object())

    class FlakyAgent:
        def __init__(self, **kwargs):
            if kwargs.get("memory_manager") is not None:
                raise RuntimeError("AccessDeniedException: GetKnowledgeBase")

        def __call__(self, message):
            return f"echo: {message}"

    monkeypatch.setattr(agent, "Agent", FlakyAgent)
    out = agent.invoke({"prompt": "hello there", "gardenId": "g1"})
    assert out == {"result": "echo: hello there"}


def test_invoke_reraises_when_failure_is_unrelated_to_memory(monkeypatch):
    monkeypatch.setattr(agent, "_get_model", lambda: object())
    monkeypatch.setattr(agent, "_get_system_prompt", lambda: "system")
    monkeypatch.setattr(agent, "build_memory_manager", lambda garden_id: None)

    class BrokenAgent:
        def __init__(self, **_):
            raise RuntimeError("something unrelated to memory")

    monkeypatch.setattr(agent, "Agent", BrokenAgent)
    with pytest.raises(RuntimeError, match="unrelated to memory"):
        agent.invoke({"prompt": "hello there"})


# --- tool binding (AF-03) -----------------------------------------------------


def test_build_tools_empty_when_none_declared(monkeypatch):
    monkeypatch.delenv("TOOLS", raising=False)
    monkeypatch.delenv("TOOL_ENDPOINTS", raising=False)
    assert agent.build_tools() == []


def test_build_tools_builds_one_per_declared_name_with_an_endpoint(monkeypatch):
    monkeypatch.setenv("TOOLS", json.dumps(["weather"]))
    monkeypatch.setenv("TOOL_ENDPOINTS", json.dumps({"weather": "https://example.com/weather"}))
    assert len(agent.build_tools()) == 1


def test_build_tools_skips_names_without_a_matching_endpoint(monkeypatch):
    monkeypatch.setenv("TOOLS", json.dumps(["weather", "unknown"]))
    monkeypatch.setenv("TOOL_ENDPOINTS", json.dumps({"weather": "https://example.com/weather"}))
    assert len(agent.build_tools()) == 1


def test_make_tool_delegates_to_call_tool_endpoint(monkeypatch):
    captured = {}

    def fake_call(url, params):
        captured["url"] = url
        captured["params"] = params
        return {"temperatureC": 21.5}

    monkeypatch.setattr(agent, "call_tool_endpoint", fake_call)
    tool_fn = agent.make_tool("weather", "https://example.com/weather")
    result = tool_fn(params={"lat": 12.9, "lon": 77.6})

    assert result == {"temperatureC": 21.5}
    assert captured == {
        "url": "https://example.com/weather",
        "params": {"lat": 12.9, "lon": 77.6},
    }


def test_call_tool_endpoint_signs_request_and_parses_json_response(monkeypatch):
    import botocore.credentials

    class FakeSession:
        def get_credentials(self):
            return botocore.credentials.Credentials("AKIDFAKE", "SECRETFAKE")

    monkeypatch.setattr(agent.boto3, "Session", FakeSession)
    monkeypatch.setattr(agent, "AWS_REGION", "ap-south-1")

    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"temperatureC": 21.5}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.headers.items()}
        return FakeResponse()

    monkeypatch.setattr(agent.urllib.request, "urlopen", fake_urlopen)

    result = agent.call_tool_endpoint("https://example.com/weather", {"lat": 12.9, "lon": 77.6})

    assert result == {"temperatureC": 21.5}
    assert "lat=12.9" in captured["url"]
    assert "authorization" in captured["headers"]


# --- memory (AF-05) -----------------------------------------------------------


def test_build_memory_manager_none_when_disabled(monkeypatch):
    monkeypatch.setattr(agent, "MEMORY_ENABLED", False)
    assert agent.build_memory_manager("g1") is None


def test_build_memory_manager_none_when_no_garden_id(monkeypatch):
    monkeypatch.setattr(agent, "MEMORY_ENABLED", True)
    assert agent.build_memory_manager(None) is None


def test_build_memory_manager_none_when_knowledge_base_not_found(monkeypatch):
    monkeypatch.setattr(agent, "MEMORY_ENABLED", True)
    monkeypatch.setattr(agent, "KNOWLEDGE_BASE_NAME", "tendril-dev-memory")

    class FakeClient:
        def list_knowledge_bases(self, **_):
            return {"knowledgeBaseSummaries": []}

    monkeypatch.setattr(agent.boto3, "client", lambda *a, **k: FakeClient())
    assert agent.build_memory_manager("g1") is None


def test_build_memory_manager_none_when_data_source_not_found(monkeypatch):
    monkeypatch.setattr(agent, "MEMORY_ENABLED", True)
    monkeypatch.setattr(agent, "KNOWLEDGE_BASE_NAME", "tendril-dev-memory")
    monkeypatch.setattr(agent, "MEMORY_DATA_SOURCE_NAME", "tendril-dev-memory-datasource")

    class FakeClient:
        def list_knowledge_bases(self, **_):
            return {
                "knowledgeBaseSummaries": [{"name": "tendril-dev-memory", "knowledgeBaseId": "KB1"}]
            }

        def list_data_sources(self, **_):
            return {"dataSourceSummaries": []}

    monkeypatch.setattr(agent.boto3, "client", lambda *a, **k: FakeClient())
    assert agent.build_memory_manager("g1") is None


def test_build_memory_manager_fail_open_on_exception(monkeypatch):
    monkeypatch.setattr(agent, "MEMORY_ENABLED", True)

    def boom(*_a, **_k):
        raise RuntimeError("no AWS")

    monkeypatch.setattr(agent.boto3, "client", boom)
    assert agent.build_memory_manager("g1") is None


def test_build_memory_manager_scopes_per_garden(monkeypatch):
    monkeypatch.setattr(agent, "MEMORY_ENABLED", True)
    monkeypatch.setattr(agent, "KNOWLEDGE_BASE_NAME", "tendril-dev-memory")
    monkeypatch.setattr(agent, "MEMORY_DATA_SOURCE_NAME", "tendril-dev-memory-datasource")
    monkeypatch.setattr(agent, "MEMORY_SCOPE", "garden")

    class FakeClient:
        def list_knowledge_bases(self, **_):
            return {
                "knowledgeBaseSummaries": [{"name": "tendril-dev-memory", "knowledgeBaseId": "KB1"}]
            }

        def list_data_sources(self, **_):
            return {
                "dataSourceSummaries": [
                    {"name": "tendril-dev-memory-datasource", "dataSourceId": "DS1"}
                ]
            }

    monkeypatch.setattr(agent.boto3, "client", lambda *a, **k: FakeClient())

    captured_store_kwargs = []

    class FakeStore:
        def __init__(self, **kwargs):
            captured_store_kwargs.append(kwargs)

    captured_manager_stores = []

    class FakeMemoryManager:
        def __init__(self, stores):
            captured_manager_stores.append(stores)

    monkeypatch.setattr(agent, "BedrockKnowledgeBaseStore", FakeStore)
    monkeypatch.setattr(agent, "MemoryManager", FakeMemoryManager)

    manager_g1 = agent.build_memory_manager("garden-1")
    manager_g2 = agent.build_memory_manager("garden-2")

    assert manager_g1 is not None
    assert manager_g2 is not None
    assert captured_store_kwargs[0]["scope"] == "garden:garden-1"
    assert captured_store_kwargs[1]["scope"] == "garden:garden-2"
    # Two different gardens never share a scope string — the actual isolation mechanism.
    assert captured_store_kwargs[0]["scope"] != captured_store_kwargs[1]["scope"]
