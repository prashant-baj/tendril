"""Orchestrator Lambda — Strands agent loop triggered by `goal.submitted` (WS-02/WS-04, ADR-0012).

Invoked **asynchronously** by an EventBridge rule (`AgentCoreStack`) — never reachable from the
Client API's synchronous request path (ADR-0004). On wake it:

1. Loads the submitted Goal from `AppTable` (data-architecture.md §2). If the goal has an
   attached photo (`media_ids`), resolves a short-lived presigned S3 **GET** URL for the first
   one — the orchestrator is the trusted-tier exception that holds `MediaBucket` IAM (ADR-0013);
   specialists never do, so the URL (not direct S3 access) is how a photo reaches them, exactly
   the design data-architecture.md §4 sketched as an open option and this resolves.
2. Builds a Strands `Agent` whose tools are the registered specialists (agents-as-tools,
   ADR-0012) — each tool calls AgentCore's `InvokeAgentRuntime` against that specialist's
   deployed runtime, passing the same image URL to every specialist uniformly (config-driven:
   each specialist's own template code — `agents/hello_agent/agent.py`'s `resolve_content` —
   decides whether its prompt/model actually needs it, not the orchestrator). Which specialists
   exist comes from `AGENT_MANIFEST` (an env var built by `AgentCoreStack` at synth time from
   `agents/registry/*.json` + the runtimes it actually provisioned — never discovered at runtime
   via `ListAgentRuntimes`).
3. Runs one turn, calling whichever specialist(s) its registry description suggests are
   relevant (e.g. `vision`, once a photo is attached), and writes the result back onto the Goal
   record so a future status endpoint (WS-05+) has something real to read.
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
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")  # injected by AgentCoreStack; never hardcoded
MODEL_ID = os.getenv("MODEL_ID") or None
AGENT_MANIFEST: dict[str, dict[str, str]] = json.loads(os.getenv("AGENT_MANIFEST", "{}"))

# Media.content_type (data-architecture.md §2) -> strands.types.media.ImageContent format.
CONTENT_TYPE_TO_IMAGE_FORMAT = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}

_table = None
_agentcore = None
_s3 = None


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


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _make_specialist_tool(
    name: str,
    arn: str,
    description: str,
    image_url: str | None = None,
    image_format: str | None = None,
):
    """Wraps one registered specialist as a Strands tool calling InvokeAgentRuntime (ADR-0012)."""

    @tool(name=name, description=description)
    def call_specialist(prompt: str) -> str:
        # AgentCore requires 33-256 chars; two concatenated UUIDs comfortably clears that.
        session_id = uuid.uuid4().hex + uuid.uuid4().hex
        started = time.monotonic()
        payload: dict[str, Any] = {"prompt": prompt}
        if image_url and image_format:
            payload["imageUrl"] = image_url
            payload["imageFormat"] = image_format
        try:
            resp = _get_agentcore_client().invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=json.dumps(payload).encode("utf-8"),
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


def _build_tools(image_url: str | None = None, image_format: str | None = None) -> list:
    return [
        _make_specialist_tool(name, entry["arn"], entry["description"], image_url, image_format)
        for name, entry in AGENT_MANIFEST.items()
    ]


def _load_goal(garden_id: str, goal_id: str) -> dict[str, Any] | None:
    resp = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"})
    return resp.get("Item")


def _resolve_image(garden_id: str, media_ids: list[str]) -> tuple[str, str] | None:
    """First attached photo -> (presigned GET URL, image format), or None if there isn't one
    or its Media record/content-type is missing/unrecognized. Only the first is used — a goal
    with several photos is future work, not needed to prove this pipe."""
    if not media_ids:
        return None
    resp = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_ids[0]}"})
    item = resp.get("Item")
    if not item:
        return None
    image_format = CONTENT_TYPE_TO_IMAGE_FORMAT.get(item.get("content_type", ""))
    if not image_format:
        return None
    url = _get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": MEDIA_BUCKET_NAME, "Key": item["s3_key"]},
        ExpiresIn=900,
    )
    return url, image_format


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

    image = _resolve_image(garden_id, goal.get("media_ids") or [])
    image_url, image_format = image if image else (None, None)

    agent = Agent(
        model=BedrockModel(**({"model_id": MODEL_ID} if MODEL_ID else {})),
        system_prompt=(
            "You are Tendril's orchestrator. A gardener has submitted an issue about their "
            "garden. Call the most relevant specialist tool(s) to help understand it — if a "
            "photo is available, prefer a specialist that can inspect it — then summarize "
            "what you learned in one or two sentences."
        ),
        tools=_build_tools(image_url, image_format),
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
