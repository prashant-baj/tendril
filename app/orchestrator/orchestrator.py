"""Orchestrator Lambda — Strands agent loop triggered by `goal.submitted`/`goal.message.received`
(WS-02/WS-04, ADR-0012, PA-01/PA-02).

Invoked **asynchronously** by EventBridge rules (`AgentCoreStack`) — never reachable from the
Client API's synchronous request path (ADR-0004). Two entry points share one mechanism:

1. `goal.submitted` (`handle_goal_submitted`) — the first turn on a new goal.
2. `goal.message.received` (`handle_goal_message_received`) — every subsequent turn, after the
   gardener replies in Goal Detail's chat thread or the model itself asked a question.

Both are "just a turn in an ongoing conversation" — this orchestrator never resumes a live Strands
session across Lambda invocations (no `SnapshotSessionManager`; see `docs/stories/plan-approval.md`'s
PA-02 Context note for why that's a deliberate scope decision, not an oversight). Instead, every
turn after the first reconstructs enough context from DynamoDB (the goal, its current `Plan`/
`Task`s if any, and the full `Message` history) and feeds it back into a **fresh** `Agent`.

Each turn runs in two steps (`_run_turn`): first, a normal call so the model can call whichever
specialist tools are relevant (agents-as-tools, ADR-0012 — which specialists exist comes from
`AGENT_MANIFEST`, a deploy-time env var built by `AgentCoreStack` from `agents/registry/*.json`,
never discovered at runtime); second, a **prompt-less** call on the *same* agent instance with
`structured_output_model=ChatTurnResult` — Strands reuses the just-built conversation history for
this (no new prompt, no re-running tool calls), extracting a `reply` string plus an optional
`updated_plan`. This is what turns the model's reasoning into a real `Plan`/`Task` the gardener can
review and approve, instead of one free-text paragraph — and it needs zero changes to the
specialist agents themselves (`agents/hello_agent/agent.py`), since only the orchestrator's own
final synthesis step is structured.

On any failure, the Goal is reverted to `Intake` (submission) or left as-is with an apologetic
chat message (a later turn) — the lifecycle (architecture.md §7.3) has no dedicated "Failed"
state, so `Intake` doubles as "needs re-triggering."
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import boto3
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.models import BedrockModel

# force=True: the standard Lambda Python runtime (this is a plain Lambda, not an AgentCore
# runtime) pre-attaches its own handler to the root logger before user code even runs, and
# basicConfig() is a documented no-op once handlers already exist — confirmed live: every
# logger.info() call here was being silently dropped, while Strands' own print()-based output
# (which bypasses the logging module entirely) always showed up. force=True replaces Lambda's
# handler with this one instead of being ignored by it.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
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


# --- Structured plan-proposal shapes (PA-01/PA-02) --------------------------------------------


class TaskProposal(BaseModel):
    title: str = Field(description="A short task name, a few words, e.g. 'Water deeply'.")
    detail: str = Field(description="One concise sentence — what to actually do.")
    scope: Literal["plant", "garden"] = "plant"


class PlanProposal(BaseModel):
    success_criteria: str = Field(description="One short sentence: how you'll know it worked.")
    tasks: list[TaskProposal]


class ChatTurnResult(BaseModel):
    reply: str = Field(
        description=(
            "A short, conversational chat reply for the gardener — 2-4 short sentences, plain "
            "prose. No markdown headings (no '#'/'##'/'###'), no bold-labeled sections, and no "
            "restating the full task list — that's what the plan/task list already shows. Use "
            "at most one short bullet list only if genuinely listing several distinct items, "
            "never as the whole reply."
        )
    )
    updated_plan: PlanProposal | None = None


ORCHESTRATOR_SYSTEM_PROMPT = (
    "You are Tendril's orchestrator. A gardener has submitted an issue about their "
    "garden. Call whichever specialist tool(s) are actually relevant — a plant issue "
    "can span more than one domain (e.g. watering AND nutrition, or a pest that's also "
    "a disease), so consult more than one specialist when the issue plausibly touches "
    "more than one area. If a photo is available, call a vision-capable specialist "
    "first and pass along what it identifies to any other specialist you consult, so "
    "they reason from the same starting point instead of re-diagnosing from scratch. "
    "Then reply to the gardener like a text message, not a report: 2-4 short sentences, "
    "plain conversational prose — no markdown headings, no bold-labeled sections, no "
    "restating every specialist's answer. If you have enough information, propose a "
    "short, concrete plan (a handful of specific tasks); the plan itself carries the "
    "step-by-step detail, so your reply doesn't need to repeat it — just say what you "
    "concluded and why in a sentence or two. If you genuinely need more information "
    "first, ask one specific clarifying question instead of guessing — it's fine not "
    "to propose a plan on this turn."
)

FALLBACK_TASK_TITLE_MAX_CHARS = 60


def _make_specialist_tool(
    name: str,
    arn: str,
    description: str,
    garden_id: str,
    image_url: str | None = None,
    image_format: str | None = None,
):
    """Wraps one registered specialist as a Strands tool calling InvokeAgentRuntime (ADR-0012)."""

    @tool(name=name, description=description)
    def call_specialist(prompt: str) -> str:
        # AgentCore requires 33-256 chars; two concatenated UUIDs comfortably clears that.
        session_id = uuid.uuid4().hex + uuid.uuid4().hex
        started = time.monotonic()
        # gardenId (AF-05): the specialist's own memory scope key — every specialist can receive
        # it uniformly; only ones with memory.enabled actually use it (config-driven, not
        # specialist-specific code, same posture as imageUrl/imageFormat above).
        payload: dict[str, Any] = {"prompt": prompt, "gardenId": garden_id}
        if image_url and image_format:
            payload["imageUrl"] = image_url
            payload["imageFormat"] = image_format
        # Reasoning trace: exactly what the orchestrator's model decided to ask this specialist —
        # the closest reliable substitute for "why," since the model's own chain-of-thought isn't
        # exposed by every model/isn't logged by Strands' default (print-based, unreliable) handler.
        logger.info("specialist_call name=%s prompt=%r", name, prompt)
        try:
            resp = _get_agentcore_client().invoke_agent_runtime(
                agentRuntimeArn=arn,
                runtimeSessionId=session_id,
                payload=json.dumps(payload).encode("utf-8"),
            )
            body = json.loads(resp["response"].read())
            result_text = body.get("result", "")
            logger.info(
                "specialist_call name=%s duration_ms=%d outcome=ok result=%r",
                name,
                int((time.monotonic() - started) * 1000),
                result_text[:500],
            )
            return result_text
        except Exception as e:
            # Never re-raise: a specialist failure must degrade to a tool-error string the
            # orchestrator's own model can react to, not kill the whole turn. Re-raising here was
            # observed, live, to sometimes propagate past this function's own async/thread
            # boundary inside Strands' agent loop — bypassing handle_goal_submitted's own
            # try/except entirely (no orchestration_failed log, an unhandled Lambda invocation
            # error) rather than being caught as a graceful tool error every time.
            logger.exception(
                "specialist_call name=%s duration_ms=%d outcome=error",
                name,
                int((time.monotonic() - started) * 1000),
            )
            return f"The '{name}' specialist is unavailable right now ({e}). Try another approach."

    return call_specialist


def _build_tools(
    garden_id: str, image_url: str | None = None, image_format: str | None = None
) -> list:
    return [
        _make_specialist_tool(
            name, entry["arn"], entry["description"], garden_id, image_url, image_format
        )
        for name, entry in AGENT_MANIFEST.items()
    ]


def _load_goal(garden_id: str, goal_id: str) -> dict[str, Any] | None:
    resp = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"})
    return resp.get("Item")


def _load_plan(garden_id: str, goal_id: str) -> dict[str, Any] | None:
    resp = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLAN#{goal_id}"})
    return resp.get("Item")


def _load_tasks(garden_id: str, goal_id: str) -> list[dict[str, Any]]:
    resp = _get_table().query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"TASK#{goal_id}#")
    )
    return resp.get("Items", [])


def _load_messages(garden_id: str, goal_id: str) -> list[dict[str, Any]]:
    # sk is GOALMSG#{goal_id}#{iso_timestamp}#{message_id} — a Query on this prefix comes back
    # already time-ordered, same trick architecture.md §2 already uses for EVENT records.
    resp = _get_table().query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"GOALMSG#{goal_id}#")
    )
    return resp.get("Items", [])


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


def _write_message(garden_id: str, goal_id: str, *, role: str, content: str) -> None:
    message_id = uuid.uuid4().hex
    created_at = datetime.now(UTC).isoformat()
    _get_table().put_item(
        Item={
            "pk": f"GARDEN#{garden_id}",
            "sk": f"GOALMSG#{goal_id}#{created_at}#{message_id}",
            "message_id": message_id,
            "goal_id": goal_id,
            "role": role,
            "content": content,
            "created_at": created_at,
        }
    )


def _write_plan_and_tasks(garden_id: str, goal_id: str, plan: PlanProposal) -> None:
    """Writes/replaces the goal's Plan + Task set. A revision always reflects the latest
    proposal — any previously-proposed tasks for this goal are cleared first. `TASK#{goal_id}#*`
    (not the flat `TASK#{task_id}` data-architecture.md §2 originally sketched) is what makes
    that a single cheap prefix Query, not a table scan or a GSI — that sketch's key was optimized
    for the TasksDueIndex GSI, which is Phase 7+ work and doesn't exist yet."""
    table = _get_table()
    existing = table.query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"TASK#{goal_id}#")
    )
    for item in existing.get("Items", []):
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})

    table.put_item(
        Item={
            "pk": f"GARDEN#{garden_id}",
            "sk": f"PLAN#{goal_id}",
            "plan_id": goal_id,
            "goal_id": goal_id,
            "success_criteria": plan.success_criteria,
            "status": "PlanProposed",
        }
    )
    for t in plan.tasks:
        task_id = uuid.uuid4().hex
        table.put_item(
            Item={
                "pk": f"GARDEN#{garden_id}",
                "sk": f"TASK#{goal_id}#{task_id}",
                "task_id": task_id,
                "plan_id": goal_id,
                "goal_id": goal_id,
                "title": t.title,
                "detail": t.detail,
                "scope": t.scope,
                "status": "pending",
            }
        )


def _apply_turn_result(garden_id: str, goal_id: str, turn: ChatTurnResult) -> None:
    _write_message(garden_id, goal_id, role="assistant", content=turn.reply)
    now = datetime.now(UTC).isoformat()
    if turn.updated_plan is not None:
        _write_plan_and_tasks(garden_id, goal_id, turn.updated_plan)
        _update_goal(garden_id, goal_id, status="PlanProposed", updated_at=now)
    else:
        _update_goal(garden_id, goal_id, updated_at=now)


def _run_turn(agent: Agent, prompt: str, *, synthesize_fallback_plan: bool) -> ChatTurnResult:
    """Runs one turn: a normal call (tool-calling happens as usual), then a second, prompt-less
    call on the *same* agent instance with structured_output_model — Strands reuses the just-built
    conversation history for this (no new prompt, confirmed in strands' own agent.py docstrings),
    so it never re-runs the tool calls, just extracts structure from what already happened.

    Fails open: if the structured call itself errors, the turn isn't lost — the free-text reply
    from the first call is still relayed. When `synthesize_fallback_plan` is set (there's no
    existing Plan yet for this goal), the raw text is also wrapped into a single fallback Task
    rather than leaving the gardener with nothing structured at all (PA-01's stated AC); when a
    Plan already exists, a transient structuring failure must never silently clobber it, so no
    plan mutation happens on that path."""
    first = agent(prompt)
    free_text = str(first).strip()
    try:
        structured = agent(structured_output_model=ChatTurnResult)
        if structured.structured_output is not None:
            return structured.structured_output
        logger.warning("structured_output_empty; falling back to raw text")
    except Exception:
        logger.exception("structured_output_failed; falling back to raw text")

    reply = free_text or "(no response)"
    if not synthesize_fallback_plan:
        return ChatTurnResult(reply=reply, updated_plan=None)

    fallback_title = free_text[:FALLBACK_TASK_TITLE_MAX_CHARS] or "Review Tendril's notes"
    return ChatTurnResult(
        reply=reply,
        updated_plan=PlanProposal(
            success_criteria="Confirm with the gardener whether this addressed the issue.",
            tasks=[
                TaskProposal(title=fallback_title, detail=free_text or "No details.", scope="plant")
            ],
        ),
    )


def _format_transcript(
    goal: dict[str, Any],
    plan: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
) -> str:
    """Builds the prompt text for a chat-turn re-invocation. This orchestrator never resumes a
    live Strands session (plan-approval.md's PA-02 Context note) — every turn after the first
    reconstructs context from DynamoDB instead."""
    parts = [f"Original issue: {goal.get('description', '')}"]
    if plan:
        parts.append(f"\nCurrent plan — success criteria: {plan.get('success_criteria', '')}")
        if tasks:
            parts.append("Current tasks:")
            for t in tasks:
                parts.append(f"- {t.get('title', '')}: {t.get('detail', '')}")
    if messages:
        parts.append("\nConversation so far:")
        for m in messages:
            speaker = "Gardener" if m.get("role") == "user" else "Tendril"
            parts.append(f"{speaker}: {m.get('content', '')}")
    parts.append(
        "\nRespond to the gardener's latest message above. If they're asking for a change, "
        "revise the plan. If you need more information first, ask a clarifying question "
        "instead of guessing."
    )
    return "\n".join(parts)


def _build_orchestrator_agent(garden_id: str, goal_id: str, image_url, image_format) -> Agent:
    return Agent(
        model=BedrockModel(**({"model_id": MODEL_ID} if MODEL_ID else {})),
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        tools=_build_tools(garden_id, image_url, image_format),
        trace_attributes={"session.id": goal_id, "garden.id": garden_id},
    )


def handle_goal_submitted(detail: dict[str, Any]) -> None:
    garden_id = detail["gardenId"]
    goal_id = detail["goalId"]

    goal = _load_goal(garden_id, goal_id)
    if not goal:
        logger.error("goal_not_found garden_id=%s goal_id=%s", garden_id, goal_id)
        return

    _update_goal(garden_id, goal_id, status="Decomposing")
    _write_message(garden_id, goal_id, role="user", content=goal["description"])

    image = _resolve_image(garden_id, goal.get("media_ids") or [])
    image_url, image_format = image if image else (None, None)

    agent = _build_orchestrator_agent(garden_id, goal_id, image_url, image_format)

    message = goal["description"]
    if image_url:
        # The model only sees this text — it has no other way of knowing a photo is actually
        # attached, and won't reliably call a vision-capable specialist without being told.
        message += "\n\n(A photo of the affected plant is attached to this goal.)"

    try:
        turn = _run_turn(agent, message, synthesize_fallback_plan=True)
        # Explicit, not just Strands' streaming callback print: that relies on stdout being
        # flushed before Lambda freezes the execution environment, which isn't guaranteed and
        # has been observed to drop the final response from CloudWatch even on a successful run.
        logger.info(
            "orchestration_result garden_id=%s goal_id=%s has_plan=%s reply=%r",
            garden_id,
            goal_id,
            turn.updated_plan is not None,
            turn.reply[:500],
        )
        _apply_turn_result(garden_id, goal_id, turn)
    except Exception as e:
        logger.exception("orchestration_failed garden_id=%s goal_id=%s", garden_id, goal_id)
        _update_goal(
            garden_id,
            goal_id,
            status="Intake",
            orchestrator_error=str(e),
            updated_at=datetime.now(UTC).isoformat(),
        )


def handle_goal_message_received(detail: dict[str, Any]) -> None:
    garden_id = detail["gardenId"]
    goal_id = detail["goalId"]

    goal = _load_goal(garden_id, goal_id)
    if not goal:
        logger.error("goal_not_found garden_id=%s goal_id=%s", garden_id, goal_id)
        return

    plan = _load_plan(garden_id, goal_id)
    tasks = _load_tasks(garden_id, goal_id)
    # Already includes the newest user message — the Client API writes it before publishing this
    # event, the same ordering create_goal already relies on for goal.submitted.
    messages = _load_messages(garden_id, goal_id)

    image = _resolve_image(garden_id, goal.get("media_ids") or [])
    image_url, image_format = image if image else (None, None)

    agent = _build_orchestrator_agent(garden_id, goal_id, image_url, image_format)
    transcript = _format_transcript(goal, plan, tasks, messages)

    try:
        turn = _run_turn(agent, transcript, synthesize_fallback_plan=plan is None)
        logger.info(
            "chat_turn_result garden_id=%s goal_id=%s has_plan=%s reply=%r",
            garden_id,
            goal_id,
            turn.updated_plan is not None,
            turn.reply[:500],
        )
        _apply_turn_result(garden_id, goal_id, turn)
    except Exception as e:
        logger.exception("chat_turn_failed garden_id=%s goal_id=%s", garden_id, goal_id)
        _write_message(
            garden_id,
            goal_id,
            role="assistant",
            content=f"Sorry, something went wrong processing that ({e}). Please try again.",
        )


def handler(event: dict[str, Any], _context: Any) -> None:
    detail_type = event.get("detail-type")
    detail = event.get("detail") or {}
    if detail_type == "goal.submitted":
        handle_goal_submitted(detail)
    elif detail_type == "goal.message.received":
        handle_goal_message_received(detail)
    else:
        logger.warning("unhandled_event detail_type=%s", detail_type)
