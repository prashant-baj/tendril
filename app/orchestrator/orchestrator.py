"""Orchestrator Lambda — Strands agent loop triggered by `goal.submitted` (WS-02/WS-04, ADR-0012).

Invoked **asynchronously** by an EventBridge rule (`AgentCoreStack`) — never reachable from the
Client API's synchronous request path (ADR-0004). On wake it:

1. Loads the submitted Goal from `AppTable` (data-architecture.md §2).
2. Builds a Strands `Agent` whose tools are the registered specialists (agents-as-tools,
   ADR-0012) — each tool calls AgentCore's `InvokeAgentRuntime` against that specialist's
   deployed runtime. Which specialists exist comes from `AGENT_MANIFEST` (an env var built by
   `AgentCoreStack` at synth time from `agents/registry/*.json` + the runtimes it actually
   provisioned — never discovered at runtime via `ListAgentRuntimes`).
3. Runs one turn, calling at least the `hello` stand-in specialist, and writes the result back
   onto the Goal record so a future status endpoint (WS-05+) has something real to read.
4. On any failure (specialist unreachable, malformed response, guardrail block), the Goal is
   reverted to `Intake` with an `orchestrator_error` field — the lifecycle
   (architecture.md §7.3) has no dedicated "Failed" state yet, so `Intake` doubles as
   "needs re-triggering," which is more informative than leaving it stuck at `Decomposing`
   with no other signal.

Tracing: `Agent(trace_attributes={"session.id": goal_id, ...})` is Strands' own documented
correlation mechanism (`.claude/skills/strands-agents/SKILL.md`) — this Lambda doesn't hand-wire
a separate OTel exporter; structured `logger.info(...)` calls at each major step (specialist
called, duration, outcome) cover the rest of ADR-0001's observability NFR for now.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("tendril.orchestrator")

APP_TABLE_NAME = os.getenv("APP_TABLE_NAME")  # injected by AgentCoreStack; never hardcoded
MODEL_ID = os.getenv("MODEL_ID") or None
AGENT_MANIFEST: dict[str, dict[str, str]] = json.loads(os.getenv("AGENT_MANIFEST", "{}"))

_table = None
_agentcore = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(APP_TABLE_NAME)
    return _table


def _get_agentcore_client():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore")
    return _agentcore


def _make_specialist_tool(name: str, arn: str, description: str):
    """Wraps one registered specialist as a Strands tool calling InvokeAgentRuntime (ADR-0012)."""

    @tool(name=name, description=description)
    def call_specialist(prompt: str) -> str:
        # AgentCore requires 33-256 chars; two concatenated UUIDs comfortably clears that.
        session_id = uuid.uuid4().hex + uuid.uuid4().hex
        started = time.monotonic()
        try:
            resp = _get_agentcore_client().invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=json.dumps({"prompt": prompt}).encode("utf-8"),
            )
            body = json.loads(resp["response"].read())
            logger.info(
                "specialist_call name=%s duration_ms=%d outcome=ok",
                name,
                int((time.monotonic() - started) * 1000),
            )
            return body.get("result", "")
        except Exception:
            logger.exception(
                "specialist_call name=%s duration_ms=%d outcome=error",
                name,
                int((time.monotonic() - started) * 1000),
            )
            raise

    return call_specialist


def _build_tools() -> list:
    return [
        _make_specialist_tool(name, entry["arn"], entry["description"])
        for name, entry in AGENT_MANIFEST.items()
    ]


def _load_goal(garden_id: str, goal_id: str) -> dict[str, Any] | None:
    resp = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"})
    return resp.get("Item")


def _update_goal(garden_id: str, goal_id: str, **fields: Any) -> None:
    _get_table().update_item(
        Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
    )


def handle_goal_submitted(detail: dict[str, Any]) -> None:
    garden_id = detail["gardenId"]
    goal_id = detail["goalId"]

    goal = _load_goal(garden_id, goal_id)
    if not goal:
        logger.error("goal_not_found garden_id=%s goal_id=%s", garden_id, goal_id)
        return

    _update_goal(garden_id, goal_id, status="Decomposing")

    agent = Agent(
        model=BedrockModel(**({"model_id": MODEL_ID} if MODEL_ID else {})),
        system_prompt=(
            "You are Tendril's orchestrator. A gardener has submitted an issue about their "
            "garden. Call the most relevant specialist tool(s) to help understand it, then "
            "summarize what you learned in one or two sentences."
        ),
        tools=_build_tools(),
        trace_attributes={"session.id": goal_id, "garden.id": garden_id},
    )

    try:
        result = agent(goal["description"])
        _update_goal(
            garden_id,
            goal_id,
            status="PlanProposed",
            orchestrator_result=str(result),
            updated_at=datetime.now(UTC).isoformat(),
        )
    except Exception as e:
        logger.exception("orchestration_failed garden_id=%s goal_id=%s", garden_id, goal_id)
        _update_goal(
            garden_id,
            goal_id,
            status="Intake",
            orchestrator_error=str(e),
            updated_at=datetime.now(UTC).isoformat(),
        )


def handler(event: dict[str, Any], _context: Any) -> None:
    detail_type = event.get("detail-type")
    if detail_type != "goal.submitted":
        logger.warning("unhandled_event detail_type=%s", detail_type)
        return
    handle_goal_submitted(event.get("detail") or {})
