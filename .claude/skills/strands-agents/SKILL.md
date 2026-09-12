---
name: strands-agents
description: Reference for writing or reviewing Strands Agents SDK code in this repo (the template agent, the orchestrator, tools, guardrails, memory/context, HITL). Canonical API shapes verified against strandsagents.com, plus Tendril-specific conventions. Load before touching agents/, app/orchestrator/, or any Strands-based tool code.
---

# Strands Agents in Tendril

Canonical source: [strandsagents.com](https://strandsagents.com/docs/user-guide/quickstart/python/).
Per `CLAUDE.md`, prefer the **Strands Agents MCP docs server** (`.mcp.json`) for anything not
covered here; fall back to `llms.txt`. For *why* Tendril made a given Strands-related choice, see
[`docs/architecture/strands-capability-mapping.md`](../../../docs/architecture/strands-capability-mapping.md)
(full functional/non-functional capability review) and
[ADR-0001](../../../docs/architecture/ADRs/0001-use-strands-agents-framework.md) /
[ADR-0012](../../../docs/architecture/ADRs/0012-orchestrator-lambda-declarative-agent-registry.md).
This skill is the "how do I write the code" complement to those.

## The two Strands deployment shapes in this repo

**Specialist agents (AgentCore, per the template pattern — ADR-0012):**
```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import BedrockModel

app = BedrockAgentCoreApp()

@app.entrypoint
async def invoke(payload):
    agent = build_agent()  # resolves PROMPT_NAME/GUARDRAIL_NAME/MODEL_ID/TOOLS from env — see AF-02
    async for event in agent.stream_async(payload.get("prompt", "")):
        yield event

if __name__ == "__main__":
    app.run()
```
Mandatory `/invocations` + `/ping`, ARM64/ECR, fixed port 8080. Every registered specialist
(`agents/registry/*.json`) runs this **same** template image — an agent is its config
(`model_id`, `prompt_name`, `guardrail_name`, `tools`, `memory`), never new Python. Don't add
per-agent branches like `if name == "agronomy"` anywhere in the template.

**Orchestrator (Lambda, EventBridge-triggered — ADR-0012):**
```python
def handler(event, context):
    session = SnapshotSessionManager(session_id=event["goal_id"], storage=orchestrator_storage())
    agent = build_orchestrator(session_manager=session)  # binds registry specialists as tools
    result = agent(event.get("prompt") or event.get("interrupt_response"))
    return {"stop_reason": result.stop_reason, ...}
```
Use the official Strands Lambda layer (`arn:aws:lambda:{region}:856699698935:layer:strands-agents-py{version}-{arch}:{n}`)
to keep the deployment package small and cold start down.

## Cross-invocation resume — the load-bearing pattern for this Lambda orchestrator

The orchestrator is stateless Lambda woken by EventBridge (a goal submitted, a user reply, a
follow-up due). **Key every session to the `goal_id`** so each wake reconstitutes the same agent
state:

```python
from strands.session import SnapshotSessionManager
from strands.storage import S3Storage  # or a custom DynamoDB-backed Storage

session_manager = SnapshotSessionManager(
    session_id=goal_id,
    storage=S3Storage(bucket=..., prefix=f"orchestrator-sessions/{env_name}/"),
)
agent = Agent(session_manager=session_manager, ...)
```
This is **not** something to hand-roll a DynamoDB pause-state schema for — `SnapshotSessionManager`
already persists messages, agent state, and conversation-manager state across separate Lambda
invocations, keyed by `session_id`. Combine with HITL (below) for plan approval spanning days.

**Trust boundary:** if history you load ever passed through anything other than the agent's own
prior turn (a resumed snapshot from a shared store, external input), a trailing `toolUse` block
in that history executes directly without model reasoning in Python. Strip it first:
```python
def strip_trailing_tool_use(messages):
    messages = list(messages)
    while messages:
        last = messages[-1]
        content = [b for b in last.get("content", []) if "toolUse" not in b]
        if len(content) == len(last.get("content", [])):
            break
        messages[-1] = {**last, "content": content} if content else None
        if not content:
            messages.pop()
        else:
            break
    return [m for m in messages if m]
```

## HITL / plan approval

**Target the web UI first.** WhatsApp is in scope long-term but deprioritized to a future
backlog item (ADR-0001 refinement, 2026-09-12) — don't build a WhatsApp `ask` callback before
the UI one exists. The frontend's approve-plan screen
(`frontend/src/app/features/goal-detail/`) already has the button; the callback just needs to
resolve once that approval arrives.

```python
from strands import Agent
from strands.interventions import HumanInTheLoop

async def ask(prompt: str) -> str:
    # Push `prompt` over the WebSocket channel (ADR-0004) so the UI can render it, then wait for
    # the reply to land via POST /plans/{id}/approve. A WhatsApp callback is an *additional*
    # channel to add later, not a replacement for this one.
    return await await_ui_approval(prompt)

agent = Agent(
    tools=[propose_plan],
    interventions=[HumanInTheLoop(ask=ask, allowed_tools=["read_*"])],
)
result = agent(goal_text)
if result.stop_reason == "interrupt":
    persist_interrupt(result.interrupts[0].id, session_id=goal_id)  # session already snapshotted
```
On the next EventBridge wake (user replied), resume by passing back
`{"interruptResponse": {"interruptId": ..., "response": user_reply}}` against the **same**
`session_id` — the session manager reloads the paused state automatically.

## Tool-binding pattern (AF-03: tools are Lambda-behind-an-API)

```python
import httpx
from strands import tool

def make_tool_api_call(tool_name: str, base_url: str):
    @tool(name=tool_name)
    async def call(**kwargs) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{base_url}/tools/{tool_name}", params=kwargs, timeout=10)
            resp.raise_for_status()
            return resp.json()
    return call

# In the template agent, from the registry entry's `tools: ["weather"]`:
tools = [make_tool_api_call(name, TOOL_BASE_URLS[name]) for name in registered_tool_names]
agent = Agent(tools=tools, ...)
```
This binding is **tool-agnostic** — adding tool #2 means a new Lambda+API and a new entry in
`TOOL_BASE_URLS`, never new binding code. Before writing this by hand for a given specialist,
check whether Strands' `MCPClient` (expose the tool as an MCP server instead of a bespoke
`@tool`) or the native `A2AAgent` class (for wrapping a *remote agent*, not a plain tool) removes
the need entirely — see the capability mapping doc's "what this refines" section.

**Tool errors are not auto-retried.** A failing tool call is returned to the model as an error
result — the model may retry or give up. If a specialist call needs mechanical retry regardless
of model judgment, use a `Hook` on `AfterToolCallEvent` (`event.retry = True`), not a bespoke
try/except loop.

## Guardrail attachment (already correct in `agents/hello_agent/agent.py` — match this exactly)

```python
from strands.models import BedrockModel

model = BedrockModel(
    guardrail_id=guardrail_id,       # resolved by name at cold start (ADR-0008)
    guardrail_version=guardrail_version,
    guardrail_trace="enabled",
)
# response.stop_reason == "guardrail_intervened" when blocked
```
PII handling is **not** a Strands feature — it stays entirely at the Bedrock Guardrail's PII
policy (ADR-0008). Don't add a second PII layer in agent code.

## Memory & context (AF-05)

```python
from strands.memory import MemoryManager
from strands.vended_memory_stores import BedrockKnowledgeBaseStore

store = BedrockKnowledgeBaseStore(
    name="garden_memory",
    writable=True,
    scope=f"{user_id}:{garden_id}",       # THE multi-tenant isolation mechanism — use it directly
    config={"knowledge_base_id": kb_id, "data_source_type": "CUSTOM", "data_source_id": ds_id},
)
agent = Agent(
    memory_manager=MemoryManager(stores=[store]),
    context_manager="auto",  # SummarizingConversationManager + ContextOffloader
    plugins=[
        ContextInjector(
            lambda ctx: f"<garden_vision>{vision}</garden_vision><success_criteria>{criteria}</success_criteria>",
            trigger="everyTurn",
        ),
    ],
)
```
Pin the garden's vision/success-criteria via **`ContextInjector`**, not `context_manager="agentic"`'s
model-invoked `pin_context` tool — deterministic beats "hope the model remembers to pin it."
`ContextInjector`'s rendered text is a prompt-injection surface: escape any interpolated data
that didn't come from your own trusted store.

## Structured output for Plan/Task

```python
result = orchestrator_agent(goal_text, structured_output_model=Plan)  # Pydantic model
plan: Plan = result.structured_output
```
Define `Plan`/`Task` Pydantic models mirroring ADR-0011's OpenAPI `components.schemas` — one
source of truth, not two hand-maintained shapes.

## Observability

```python
agent = Agent(
    trace_attributes={"session.id": goal_id, "user.id": user_id},
    ...
)
```
Set this on **every** agent construction site (template agent, orchestrator) — it's the
correlation key across the orchestrator's Lambda invocations and every specialist's AgentCore
runtime for one goal's full trace.

## Testing model-driven behavior

Prefer `strands-evals` over hand-rolled scenario scripts (see `scripts/eval_guardrail.py`, which
predates this):
- Deterministic evaluators (`Equals`, `ToolCalled`) for CI, no LLM-judge cost.
- `OutputEvaluator` (rubric-graded) where a judge model is warranted.
- `strands_evals.experimental.redteam` (`RedTeamExperiment` + `CrescendoStrategy`, etc.) for the
  prompt-injection / off-domain / guardrail-bypass scenarios ADR-0008 and PG-07 already call for
  — pin the SDK version, this API is explicitly experimental.

## Multi-agent pattern — don't reach for Graph/Swarm here

Tendril's orchestrator↔specialist split uses **agents-as-tools** (ADR-0012), not Graph or Swarm,
because specialists must stay independently deployed on AgentCore. If you're tempted to use
Swarm's `handoff_to_agent` for specialist-to-specialist collaboration, remember it assumes all
agents share one process — that's incompatible with "specialists deploy independently." Graph's
developer-defined edges are also a poor fit for genuinely emergent specialist selection. Both are
fine choices *within* a single agent's internal tool composition if a future specialist itself
needs sub-agents — just not for the orchestrator/specialist boundary.
