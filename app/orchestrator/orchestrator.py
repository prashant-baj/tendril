"""Orchestrator Lambda — Strands agent loop triggered by `goal.submitted`/`goal.message.received`/
`task.checkin.received`/`followup.due` (WS-02/WS-04, ADR-0012, PA-01/PA-02/PA-05, Phase 7.5+).

Invoked **asynchronously** by EventBridge rules (`AgentCoreStack`) — never reachable from the
Client API's synchronous request path (ADR-0004). Four entry points:

1. `goal.submitted` (`handle_goal_submitted`) — the first turn on a new goal.
2. `goal.message.received` (`handle_goal_message_received`) — every subsequent turn, after the
   gardener replies in Goal Detail's chat thread or the model itself asked a question.
3. `task.checkin.received` (`handle_task_checkin_received`, PA-05) — after a check-in photo is
   attached to a task (`garden_handler.py::post_task_checkin`), assesses that photo against the
   plan and writes feedback onto the task; may also revise the plan, same as a chat turn.
4. `followup.due` (`handle_followup_due`, Phase 7.5+) — the tracker/scheduler (`app/tracker/`)
   found a pending task past its due-date. Unlike the other three, this is deliberately **not**
   "just a turn" — no `Agent`/model call at all, just a deterministic nudge message + Activity
   event + due-date bump (see `handle_followup_due`'s own docstring).

The first three are "just a turn in an ongoing conversation." `_build_orchestrator_agent` attaches
a real `SnapshotSessionManager` (`session_id=goal_id`, Phase 7.5+ — see its own docstring) so the
resumed conversation history survives across Lambda invocations, closing the gap
`docs/stories/plan-approval.md`'s PA-02 Context note deliberately deferred. Each handler still
also reconstructs a per-turn prompt from DynamoDB (the goal, current `Plan`/`Task`s, and the full
`Message` history) — restating this on top of an already-resumed session is deliberately
redundant for now, an intermediate rollout step (`docs/stories/tracker-scheduler.md`'s SR-02)
verifying resume itself works correctly before SR-03 minimizes each turn's prompt to just what's
new.

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

Each specialist call made during a turn is also recorded into a shared `trace` list
(`_make_specialist_tool`) — persisted onto the `Plan` item whenever that turn produces one
(`_write_plan_and_tasks`), backing the frontend's "How this was decided" section (PA-05). A plan
revision (from any of the three turn kinds) never deletes an already-`done` Task — only
not-yet-done tasks are cleared and replaced — so a check-in's completed evidence (photo/feedback)
always survives a later plan change.

On any failure, the Goal is reverted to `Intake` (submission) or left as-is with an apologetic
chat message (a later turn) — the lifecycle (architecture.md §7.3) has no dedicated "Failed"
state, so `Intake` doubles as "needs re-triggering."
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import boto3
import plant_identity as pid
from boto3.dynamodb.conditions import Key
from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.models import BedrockModel
from strands.session.snapshot_session_manager import SnapshotSessionManager
from strands.storage.s3_storage import S3Storage

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
AGENT_STATE_BUCKET_NAME = os.getenv("AGENT_STATE_BUCKET_NAME")  # injected by AgentCoreStack
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


class ProposedAction(BaseModel):
    """ADR-0034: something the gardener asked for that changes data. The model only *proposes*;
    code validates it and the gardener confirms it with a tap. Never used for identity checks on
    photos — code already handles those."""

    type: Literal["complete_task", "link_plant"] = Field(
        description=(
            "'complete_task' when the gardener says they finished one of the current tasks; "
            "'link_plant' when they tell you which numbered plant a photo or this goal is about."
        )
    )
    task_title: str | None = Field(
        default=None, description="For complete_task: the exact title of the pending task."
    )
    stake_no: int | None = Field(
        default=None, description="For link_plant: the plant's stake number, e.g. 12."
    )


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
    proposed_action: ProposedAction | None = None


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
    "to propose a plan on this turn. You never change anything yourself: when the gardener "
    "says they finished a task, or tells you which numbered plant a photo is about, set "
    "proposed_action and tell them to tap Confirm; a typed 'yes' does not confirm anything. "
    "When a note in the conversation says a confirmation question was already shown, don't "
    "repeat it as done — just explain briefly."
)

FALLBACK_TASK_TITLE_MAX_CHARS = 60
# Phase 7.5+: how far forward a followup.due nudge pushes a task's due-date, so the tracker
# doesn't re-fire for the same task on its next tick before the gardener responds. Own copy, not
# shared with garden_handler.py's equivalent constant (separate deployable units, no cross-package
# imports — same convention as GOAL_EVENT_SOURCE's duplication).
FOLLOWUP_OFFSET_DAYS = 3


def _summarize(text: str, max_len: int = 4000) -> str:
    """Stores a specialist/orchestrator response for the trace (PA-05's "How this was decided"
    section) — the full text, not a display-truncated one-liner; truncation to a preview is a
    frontend concern (`trace-entry.component.ts`'s "Show more"), matching this codebase's
    "UI presentation lives in the frontend" precedent. `max_len` is only a defensive cap against a
    pathological response, not a normal-path truncation — never re-asks the model to summarize
    itself, just a plain substring, matching this codebase's existing bias toward cheap
    deterministic steps over an extra model round-trip."""
    text = text.strip()
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


GARDEN_CONTEXT_FIELDS = (
    ("soil", "Soil"),
    ("irrigation", "Irrigation"),
    ("intercrop", "Intercrop / ground cover"),
    ("layout", "Layout"),
    ("notes", "Other notes"),
)


def _render_garden_context(garden: dict[str, Any] | None) -> str:
    """Renders a Garden item as a compact `<garden_context>` block (ADR-0032), or "" when there
    is nothing to say. `notification_recipients` are deliberately never rendered: phone numbers
    are personal data no model needs to reason with."""
    if not garden:
        return ""
    lines: list[str] = []
    for key, label in (
        ("name", "Garden"),
        ("geolocation", "Location"),
        ("climate_zone", "Climate zone"),
        ("vision", "Owner's vision"),
    ):
        if garden.get(key):
            lines.append(f"{label}: {garden[key]}")
    context = garden.get("context") or {}
    for key, label in GARDEN_CONTEXT_FIELDS:
        if context.get(key):
            lines.append(f"{label}: {context[key]}")
    stake_range = context.get("stake_range")
    if stake_range:
        lines.append(
            f"Plants are tagged with numbered stakes #{int(stake_range['min'])}"
            f"-#{int(stake_range['max'])}"
        )
    if not lines:
        return ""
    return (
        "<garden_context>\n"
        + "\n".join(lines)
        + "\n</garden_context>\nTake these whole-garden facts into account in your advice."
    )


def _load_garden_context(garden_id: str) -> str:
    """The rendered garden context for this turn. Best-effort: a missing or unreadable Garden
    item just means no context block, never a failed turn (ADR-0032)."""
    try:
        garden = (
            _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"}).get("Item")
        )
    except Exception:
        logger.exception("garden_context_load_failed garden_id=%s", garden_id)
        return ""
    # Guard on the item's own sk: only a real Garden METADATA item is rendered.
    if not garden or garden.get("sk") != "METADATA":
        return ""
    return _render_garden_context(garden)


def _make_specialist_tool(
    name: str,
    arn: str,
    description: str,
    garden_id: str,
    image_url: str | None = None,
    image_format: str | None = None,
    trace: list[dict[str, Any]] | None = None,
    garden_context: str = "",
):
    """Wraps one registered specialist as a Strands tool calling InvokeAgentRuntime (ADR-0012).

    `trace` (PA-05): a mutable list shared across every specialist tool built for one turn —
    each call appends its own entry, giving the caller a real, ordered record of which
    specialists were actually consulted and what they said, instead of only a CloudWatch log
    line. Persisted onto the Plan item by `_write_plan_and_tasks` when the turn produces one."""

    @tool(name=name, description=description)
    def call_specialist(prompt: str) -> str:
        # AgentCore requires 33-256 chars; two concatenated UUIDs comfortably clears that.
        session_id = uuid.uuid4().hex + uuid.uuid4().hex
        started = time.monotonic()
        # gardenId (AF-05): the specialist's own memory scope key — every specialist can receive
        # it uniformly; only ones with memory.enabled actually use it (config-driven, not
        # specialist-specific code, same posture as imageUrl/imageFormat above).
        # ADR-0032: whole-garden facts ride along as part of the prompt itself, so specialists
        # need no code/template change to receive them (ADR-0013: data arrives as input).
        full_prompt = f"{garden_context}\n\n{prompt}" if garden_context else prompt
        payload: dict[str, Any] = {"prompt": full_prompt, "gardenId": garden_id}
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
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.info(
                "specialist_call name=%s duration_ms=%d outcome=ok result=%r",
                name,
                duration_ms,
                result_text[:500],
            )
            if trace is not None:
                trace.append({"agent": name, "says": _summarize(result_text), "ms": duration_ms})
            return result_text
        except Exception as e:
            # Never re-raise: a specialist failure must degrade to a tool-error string the
            # orchestrator's own model can react to, not kill the whole turn. Re-raising here was
            # observed, live, to sometimes propagate past this function's own async/thread
            # boundary inside Strands' agent loop — bypassing handle_goal_submitted's own
            # try/except entirely (no orchestration_failed log, an unhandled Lambda invocation
            # error) rather than being caught as a graceful tool error every time.
            duration_ms = int((time.monotonic() - started) * 1000)
            logger.exception(
                "specialist_call name=%s duration_ms=%d outcome=error", name, duration_ms
            )
            if trace is not None:
                trace.append({"agent": name, "says": f"Unavailable ({e}).", "ms": duration_ms})
            return f"The '{name}' specialist is unavailable right now ({e}). Try another approach."

    return call_specialist


def _build_tools(
    garden_id: str,
    image_url: str | None = None,
    image_format: str | None = None,
    trace: list[dict[str, Any]] | None = None,
    garden_context: str = "",
) -> list:
    return [
        _make_specialist_tool(
            name,
            entry["arn"],
            entry["description"],
            garden_id,
            image_url,
            image_format,
            trace,
            garden_context=garden_context,
        )
        for name, entry in AGENT_MANIFEST.items()
        # ADR-0034: internal specialists (plant_identifier) are called by code, never offered
        # to the model as tools — identification must not depend on the model's discretion.
        if not entry.get("internal")
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


def _load_latest_message(garden_id: str, goal_id: str) -> dict[str, Any] | None:
    """The single newest Message for this goal — Phase 7.5+'s session-resume means a handler no
    longer needs the FULL message history (the resumed Agent's own conversation already has every
    prior turn); only the just-arrived message is genuinely new input for this turn's prompt.
    `ScanIndexForward=False` + `Limit=1` makes this a cheap single-item query, not a full-history
    read."""
    resp = _get_table().query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"GOALMSG#{goal_id}#"),
        ScanIndexForward=False,
        Limit=1,
    )
    items = resp.get("Items", [])
    return items[0] if items else None


def _load_messages(garden_id: str, goal_id: str) -> list[dict[str, Any]]:
    """Every Message for this goal — `GOALMSG#{goal_id}#{iso_ts}#{id}` sk already sorts
    chronologically (data-architecture.md §2); the explicit sort here only guards against a
    fake/non-ordering table in tests, mirroring `garden_handler.py::get_goal_detail`'s identical
    pattern. Restated into this turn's prompt by `_format_transcript` on top of the
    already-resumed session — see that function's docstring for why."""
    resp = _get_table().query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"GOALMSG#{goal_id}#")
    )
    return sorted(resp.get("Items", []), key=lambda m: m.get("sk", ""))


def _format_transcript(
    goal: dict[str, Any],
    plan: dict[str, Any] | None,
    tasks: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    checkin_task: dict[str, Any] | None = None,
) -> str:
    """Reconstructs this turn's prompt from DynamoDB — the goal, the current Plan/Tasks (if any),
    and the full Message history so far. Restating this on top of an already-resumed session is
    deliberately redundant for now (module docstring, SR-02) — an intermediate rollout step
    verifying resume itself works correctly before a later step (SR-03) minimizes each turn's
    prompt to just what's new. Ends with whatever is genuinely new for this turn: the fixed
    check-in framing (`_build_checkin_prompt`) when one is in progress, or an instruction to
    respond to the latest message already included in the transcript above."""
    lines = [f"Goal: {goal.get('description', '')}"]
    if plan:
        lines.append(f"Current plan — success criteria: {plan.get('success_criteria', '')}")
    if tasks:
        lines.append("Current tasks:")
        for t in tasks:
            lines.append(
                f"- [{t.get('status', 'pending')}] {t.get('title', '')}: {t.get('detail', '')}"
            )
    if messages:
        lines.append("Conversation so far:")
        for m in messages:
            lines.append(f"{m.get('role', 'user')}: {m.get('content', '')}")
    if checkin_task is not None:
        lines.append(_build_checkin_prompt(checkin_task))
    else:
        lines.append("Respond to the gardener's latest message above.")
    return "\n".join(lines)


PHOTO_WAIT_SECONDS = 20  # ADR-0033: bounded wait for a just-uploaded photo still processing
PHOTO_POLL_SECONDS = 2
_sleep = time.sleep  # module-level so tests can skip real waiting
UNREADABLE_PHOTO_NOTE = (
    "(The gardener attached a photo, but it could not be read — it may be corrupt, an "
    "unsupported format, larger than 10 MB, or still processing. Do not guess what it shows and "
    "do not call a vision specialist for it; say you couldn't read the photo and ask them to "
    "resend it.)"
)


class PhotoResolution(BaseModel):
    url: str | None = None
    image_format: str | None = None
    unreadable: bool = False  # a photo was attached but must not be sent to any model


def _load_media(garden_id: str, media_id: str) -> dict[str, Any] | None:
    return (
        _get_table()
        .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"})
        .get("Item")
    )


def _resolve_photo(garden_id: str, media_ids: list[str]) -> PhotoResolution:
    """First attached photo -> what to send to specialists (ADR-0033).

    - Processed (`processing_status: ready`): the **analysis copy**, always JPEG — a right-sized,
      correctly typed image, whatever the original was.
    - Still `pending`: poll up to PHOTO_WAIT_SECONDS (a goal is often submitted seconds after its
      upload); still not ready, or `failed`: `unreadable` — the turn proceeds without the photo
      instead of sending specialists something they will reject.
    - No `processing_status` at all (uploaded before ingestion existed): the original with its
      declared type, as before.
    Only the first photo is used — several photos per goal is future work.
    """
    if not media_ids:
        return PhotoResolution()
    item = _load_media(garden_id, media_ids[0])
    if not item:
        return PhotoResolution()

    waited = 0.0
    while item.get("processing_status") == "pending" and waited < PHOTO_WAIT_SECONDS:
        _sleep(PHOTO_POLL_SECONDS)
        waited += PHOTO_POLL_SECONDS
        item = _load_media(garden_id, media_ids[0]) or item

    status = item.get("processing_status")
    if status is None:  # legacy media: original + declared type
        key = item["s3_key"]
        image_format = CONTENT_TYPE_TO_IMAGE_FORMAT.get(item.get("content_type", ""))
        if not image_format:
            return PhotoResolution(unreadable=True)
    elif status == "ready" and item.get("analysis_s3_key"):
        key, image_format = item["analysis_s3_key"], "jpeg"
    else:
        logger.warning(
            "photo_unreadable garden_id=%s media_id=%s status=%s error=%s waited_s=%s",
            garden_id,
            media_ids[0],
            status,
            item.get("processing_error"),
            waited,
        )
        return PhotoResolution(unreadable=True)

    url = _get_s3().generate_presigned_url(
        "get_object", Params={"Bucket": MEDIA_BUCKET_NAME, "Key": key}, ExpiresIn=900
    )
    return PhotoResolution(url=url, image_format=image_format)


def _resolve_image(garden_id: str, media_ids: list[str]) -> tuple[str, str] | None:
    """(presigned GET URL, image format) of the first attached photo, or None — see
    `_resolve_photo`, which also tells callers when an attached photo was unreadable."""
    photo = _resolve_photo(garden_id, media_ids)
    return (photo.url, photo.image_format) if photo.url and photo.image_format else None


# --- ADR-0034: plant identity from photos + proposed actions ------------------------------------

PLANT_IDENTIFIER = "plant_identifier"
# §5: auto-link ships only once wrong high-confidence reads are < 1% on the archive evals. Until
# the owner flips this, every confident read is still a proposal the gardener confirms.
AUTO_LINK_ENABLED = os.getenv("AUTO_LINK_ENABLED", "false").strip().lower() == "true"
ACTION_TYPES = ("complete_task", "set_plant", "set_goal_plant", "create_plant")


class IdentityOutcome(BaseModel):
    note: str = ""  # appended to the model's prompt for this turn
    action_id: str | None = None  # a proposal shown with the reply


def _key(garden_id: str, sk: str) -> dict[str, str]:
    return {"pk": f"GARDEN#{garden_id}", "sk": sk}


def _get(garden_id: str, sk: str) -> dict[str, Any] | None:
    return _get_table().get_item(Key=_key(garden_id, sk)).get("Item")


def _plant_for_stake(garden_id: str):
    def lookup(stake_no: int) -> str | None:
        item = _get(garden_id, f"STAKE#{stake_no:05d}")
        return item.get("plant_id") if item else None

    return lookup


def _plant_stake_no(garden_id: str, plant_id: str | None) -> int | None:
    if not plant_id:
        return None
    item = _get(garden_id, f"PLANT#{plant_id}")
    return int(item["stake_no"]) if item and item.get("stake_no") is not None else None


def _stake_range(garden_id: str) -> tuple[int, int] | None:
    item = _get(garden_id, "METADATA") or {}
    rng = (item.get("context") or {}).get("stake_range")
    return (int(rng["min"]), int(rng["max"])) if rng else None


def _read_stake(garden_id: str, media_id: str, photo: PhotoResolution) -> pid.StakeRead | None:
    """Runs the internal plant_identifier on the photo's analysis copy (ADR-0034 §1) and records
    the read on the Media item. None when there's nothing to read or the call failed — identity
    then stays unknown; it never blocks the turn."""
    entry = AGENT_MANIFEST.get(PLANT_IDENTIFIER) or {}
    if not entry.get("arn") or not entry.get("internal") or not photo.url:
        return None
    payload = {
        "prompt": "Read the number on the plant's stake in this photo.",
        "gardenId": garden_id,
        "imageUrl": photo.url,
        "imageFormat": photo.image_format,
    }
    started = time.monotonic()
    try:
        resp = _get_agentcore_client().invoke_agent_runtime(
            agentRuntimeArn=entry["arn"],
            runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
            payload=json.dumps(payload).encode("utf-8"),
        )
        read = pid.parse_stake_read(json.loads(resp["response"].read()).get("result", ""))
    except Exception:
        logger.exception("stake_read_failed garden_id=%s media_id=%s", garden_id, media_id)
        return None
    logger.info(
        "stake_read garden_id=%s media_id=%s visible=%s stake_no=%s confidence=%s alts=%s ms=%d",
        garden_id,
        media_id,
        read.stake_visible,
        read.stake_no,
        read.confidence,
        list(read.alternatives),
        int((time.monotonic() - started) * 1000),
    )
    try:
        _get_table().update_item(
            Key=_key(garden_id, f"MEDIA#{media_id}"),
            UpdateExpression="SET stake_read = :r",
            ExpressionAttributeValues={":r": read.as_record()},
            ConditionExpression="attribute_exists(pk)",
        )
    except Exception:
        logger.exception("stake_read_record_failed garden_id=%s media_id=%s", garden_id, media_id)
    return read


def _validate_action(garden_id: str, goal_id: str, kind: str, params: dict[str, Any]) -> str | None:
    """Deterministic check before a proposal is ever shown (ADR-0034 §3). Returns why it's
    invalid, or None. A model can't get an impossible action in front of the gardener."""
    if kind not in ACTION_TYPES:
        return f"unknown action type {kind!r}"
    media_id = params.get("media_id")
    if media_id and not _get(garden_id, f"MEDIA#{media_id}"):
        return "media not in this garden"
    if kind == "complete_task":
        task = _get(garden_id, f"TASK#{goal_id}#{params.get('task_id')}")
        if not task:
            return "task not in this goal"
        if task.get("status") != "pending":
            return "task is not pending"
    if kind in ("set_plant", "set_goal_plant"):
        if not params.get("plant_id") or not _get(garden_id, f"PLANT#{params['plant_id']}"):
            return "plant not in this garden"
        if kind == "set_plant" and not media_id:
            return "set_plant needs a photo"
    if kind == "create_plant":
        stake_no = params.get("stake_no")
        if not isinstance(stake_no, int) or stake_no < 1:
            return "invalid stake number"
        if _plant_for_stake(garden_id)(stake_no):
            return "stake number already used"
    return None


def _open_actions(garden_id: str, goal_id: str) -> list[dict[str, Any]]:
    resp = _get_table().query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"ACTION#{goal_id}#")
    )
    return [i for i in resp.get("Items", []) if i.get("status") == "proposed"]


def _same_params(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Proposal params compared on what they change; `feedback` (the reply text) and
    `candidates` (display only) don't make a proposal different."""
    ignore = {"feedback", "candidates"}

    def norm(d: dict[str, Any]) -> dict[str, str]:
        return {k: str(v) for k, v in d.items() if k not in ignore and v is not None}

    return norm(a) == norm(b)


def _supersede_actions(garden_id: str, open_items: list[dict[str, Any]]) -> None:
    for item in open_items:
        try:
            _get_table().update_item(
                Key=_key(garden_id, item["sk"]),
                UpdateExpression="SET #s = :sup, resolved_at = :now",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":sup": "superseded",
                    ":now": datetime.now(UTC).isoformat(),
                    ":prop": "proposed",
                },
                ConditionExpression="#s = :prop",
            )
        except Exception:
            logger.warning("supersede_failed sk=%s", item["sk"])


def _propose_action(
    garden_id: str, goal_id: str, kind: str, params: dict[str, Any], summary: str
) -> str | None:
    """Validates and stores a proposal (`ACTION#{goal_id}#{action_id}`, status `proposed`),
    superseding older open ones on this goal. Only the Client API's confirm endpoint applies it.
    An identical proposal that's still open is kept and its id returned — not raised again."""
    params = {k: v for k, v in params.items() if v is not None}
    error = _validate_action(garden_id, goal_id, kind, params)
    if error:
        logger.warning("action_rejected goal_id=%s type=%s reason=%s", goal_id, kind, error)
        return None
    open_items = _open_actions(garden_id, goal_id)
    for item in open_items:
        if item.get("type") == kind and _same_params(item.get("params") or {}, params):
            logger.info("action_repeated goal_id=%s action_id=%s", goal_id, item["action_id"])
            return item["action_id"]
    _supersede_actions(garden_id, open_items)
    action_id = uuid.uuid4().hex
    _get_table().put_item(
        Item={
            "pk": f"GARDEN#{garden_id}",
            "sk": f"ACTION#{goal_id}#{action_id}",
            "action_id": action_id,
            "goal_id": goal_id,
            "type": kind,
            "params": params,
            "summary": summary,
            "status": "proposed",
            "created_at": datetime.now(UTC).isoformat(),
        }
    )
    logger.info("action_proposed goal_id=%s action_id=%s type=%s", goal_id, action_id, kind)
    return action_id


def _set_action_feedback(garden_id: str, goal_id: str, action_id: str, feedback: str) -> None:
    """The model's reply to a check-in becomes the task's feedback once the check-in is confirmed.
    Only for `complete_task` proposals — an identity question carries no task feedback."""
    item = _get(garden_id, f"ACTION#{goal_id}#{action_id}")
    if not item or item.get("type") != "complete_task":
        return
    try:
        _get_table().update_item(
            Key=_key(garden_id, f"ACTION#{goal_id}#{action_id}"),
            UpdateExpression="SET params.feedback = :f",
            ExpressionAttributeValues={":f": feedback[:2000]},
            ConditionExpression="attribute_exists(pk)",
        )
    except Exception:
        logger.exception("action_feedback_failed action_id=%s", action_id)


def _link_media_to_plant(
    garden_id: str,
    media_id: str,
    plant_id: str,
    source: str,
    *,
    goal_id: str | None = None,
    add_to_history: bool = True,
) -> None:
    """Files a photo under a plant. Unless a check-in will record it (the confirmed `complete_task`
    writes the plant's history entry then), it also joins the plant's history (ADR-0035 §2)."""
    try:
        _get_table().update_item(
            Key=_key(garden_id, f"MEDIA#{media_id}"),
            UpdateExpression="SET plant_id = :p, plant_link_source = :s",
            ExpressionAttributeValues={":p": plant_id, ":s": source},
            ConditionExpression="attribute_exists(pk)",
        )
    except Exception:
        logger.exception("media_link_failed media_id=%s", media_id)
        return
    if add_to_history:
        _append_photo_history(garden_id, plant_id, media_id, source, goal_id)


def _photo_observed_on(media: dict[str, Any] | None) -> str:
    """ADR-0035 §1: the photo's capture date, else its upload date, else today. (Mirrors the Client
    API's `plant_history.observed_on_for_media` — separate deployable units.)"""
    for field in ("taken_at", "uploaded_at"):
        value = (media or {}).get(field)
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}", value):
            return value[:10]
    return datetime.now(UTC).date().isoformat()


def _append_photo_history(
    garden_id: str, plant_id: str, media_id: str, source: str, goal_id: str | None
) -> None:
    """One `photo` entry `PLANTLOG#{plant_id}#{date}#{media_id}` — id = the media id, so the same
    photo filed twice (createGoal, then this identity match) is written once. Then the plant's
    count and latest photo, each only moving forward in time. Best-effort, like every link here."""
    media = _get(garden_id, f"MEDIA#{media_id}") or {}
    observed_on = _photo_observed_on(media)
    entry = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"PLANTLOG#{plant_id}#{observed_on}#{media_id}",
        "entry_id": media_id,
        "plant_id": plant_id,
        "observed_on": observed_on,
        "kind": "photo",
        "source": source,
        "media_ids": [media_id],
        "created_at": datetime.now(UTC).isoformat(),
    }
    if isinstance(media.get("taken_at"), str) and len(media["taken_at"]) > 10:
        entry["observed_at"] = media["taken_at"]
    if goal_id:
        entry["goal_id"] = goal_id
    try:
        _get_table().put_item(Item=entry, ConditionExpression="attribute_not_exists(pk)")
    except Exception as e:
        if _error_code(e) != "ConditionalCheckFailedException":
            logger.exception("plant_history_failed plant_id=%s media_id=%s", plant_id, media_id)
        return
    updates = [
        ("ADD history_count :d", {":d": 1}, "attribute_exists(pk)"),
        (
            "SET latest_observed_on = :o",
            {":o": observed_on},
            "attribute_exists(pk) AND (attribute_not_exists(latest_observed_on) "
            "OR latest_observed_on <= :o)",
        ),
        (
            "SET media_id = :m, latest_photo_on = :o",
            {":m": media_id, ":o": observed_on},
            "attribute_exists(pk) AND (attribute_not_exists(latest_photo_on) "
            "OR latest_photo_on <= :o)",
        ),
    ]
    for expression, values, condition in updates:
        try:
            _get_table().update_item(
                Key=_key(garden_id, f"PLANT#{plant_id}"),
                UpdateExpression=expression,
                ExpressionAttributeValues=values,
                ConditionExpression=condition,
            )
        except Exception as e:
            if _error_code(e) != "ConditionalCheckFailedException":
                logger.exception("plant_latest_failed plant_id=%s", plant_id)
    logger.info("plant_history_photo plant_id=%s media_id=%s source=%s", plant_id, media_id, source)


def _error_code(e: Exception) -> str | None:
    return (getattr(e, "response", None) or {}).get("Error", {}).get("Code")


def _label(stake_no: int | None) -> str:
    return f"#{stake_no}" if stake_no is not None else "this plant"


def _identify_photo(
    garden_id: str,
    goal: dict[str, Any],
    media_id: str,
    photo: PhotoResolution,
    *,
    task: dict[str, Any] | None = None,
    user_note: str = "",
) -> IdentityOutcome:
    """Reads the stake, applies the identity policy, and turns the decision into a proposal and/or
    a note for the model (ADR-0034 §2–4). Never changes identity on its own, except linking a
    photo to the plant the gardener already chose when nothing contradicts it."""
    goal_id = goal["goal_id"]
    read = _read_stake(garden_id, media_id, photo) or pid.NO_READ
    expected_plant = (task or {}).get("plant_id") or goal.get("plant_id")
    expected_stake = _plant_stake_no(garden_id, expected_plant)
    decision = pid.decide(
        read,
        _plant_for_stake(garden_id),
        expected_plant_id=expected_plant,
        expected_stake_no=expected_stake,
        stake_range=_stake_range(garden_id),
    )
    logger.info(
        "identity_decision goal_id=%s media_id=%s kind=%s read=%s expected=%s",
        goal_id,
        media_id,
        decision.kind,
        decision.stake_no,
        expected_stake,
    )
    n, exp = decision.stake_no, expected_stake
    subject = f"'{task.get('title', '')}'" if task else "this goal"

    if decision.kind in ("match", "no_stake"):
        if expected_plant:
            _link_media_to_plant(
                garden_id,
                media_id,
                expected_plant,
                "user",
                goal_id=goal_id,
                # A check-in photo joins the history as the confirmed check-in, not twice.
                add_to_history=task is None,
            )
        if task:
            action_id = _propose_action(
                garden_id,
                goal_id,
                "complete_task",
                {"task_id": task["task_id"], "media_id": media_id, "note": user_note or None},
                f"Mark '{task.get('title', '')}' done{f' for #{exp}' if exp else ''}?",
            )
            return IdentityOutcome(
                action_id=action_id,
                note=(
                    f"(Check-in received for task '{task.get('title', '')}' with a photo. A "
                    "Confirm button to mark it done has been shown to the gardener — give brief "
                    "feedback on the photo, and don't say the task is done yet.)"
                ),
            )
        if decision.kind == "no_stake" and not expected_plant:
            return IdentityOutcome(
                note="(No plant stake number is visible in this photo. If it matters which "
                "numbered plant this is, ask the gardener.)"
            )
        return IdentityOutcome()

    if decision.kind == "mismatch" and decision.plant_id:
        action_id = _propose_action(
            garden_id,
            goal_id,
            "set_plant",
            {"media_id": media_id, "plant_id": decision.plant_id},
            f"This photo shows {_label(n)}, but {subject} is for {_label(exp)}. "
            f"Is it {_label(n)}'s photo?",
        )
        return IdentityOutcome(
            action_id=action_id,
            note=(
                f"(Identity check: the stake in this photo reads {_label(n)}, but {subject} is "
                f"for {_label(exp)}. A question asking whether it is {_label(n)}'s photo has been "
                f"shown with a Confirm button. Don't treat the photo as {_label(exp)} and don't "
                "mark anything done until they answer.)"
            ),
        )

    if decision.kind in ("mismatch", "out_of_range"):
        return IdentityOutcome(
            note=(
                f"(Identity check: the stake in this photo seems to read {_label(n)}, which isn't "
                f"a plant in this garden{f' (expected {_label(exp)})' if exp else ''}. It may be a "
                "misread — ask the gardener which plant this is before relying on the photo.)"
            )
        )

    if decision.kind == "confident":
        if AUTO_LINK_ENABLED:
            _get_table().update_item(
                Key=_key(garden_id, f"GOAL#{goal_id}"),
                UpdateExpression="SET plant_id = :p",
                ExpressionAttributeValues={":p": decision.plant_id},
            )
            _link_media_to_plant(garden_id, media_id, decision.plant_id, "auto", goal_id=goal_id)
            return IdentityOutcome(
                note=f"(Filed this photo and goal under {_label(n)} from its stake number. "
                "Mention it briefly; the gardener can tell you if it's a different plant.)"
            )
        action_id = _propose_action(
            garden_id,
            goal_id,
            "set_goal_plant",
            {"plant_id": decision.plant_id, "media_id": media_id},
            f"This looks like {_label(n)}. Link this goal and photo to {_label(n)}?",
        )
        return IdentityOutcome(
            action_id=action_id,
            note=f"(The stake in this photo reads {_label(n)}. A Confirm button to link this goal "
            f"to {_label(n)} has been shown. Treat it as {_label(n)} provisionally.)",
        )

    if decision.kind == "ask":
        options = [c for c in decision.candidates if c.plant_id]
        if not options:
            return IdentityOutcome()
        first = options[0]
        listed = " or ".join(_label(c.stake_no) for c in options)
        action_id = _propose_action(
            garden_id,
            goal_id,
            "set_goal_plant",
            {
                "plant_id": first.plant_id,
                "media_id": media_id,
                "candidates": [{"stake_no": c.stake_no, "plant_id": c.plant_id} for c in options],
            },
            f"Is this {listed}? Confirm for {_label(first.stake_no)}, or tell me the number.",
        )
        return IdentityOutcome(
            action_id=action_id,
            note=f"(The stake number is hard to read — it could be {listed}. A question has been "
            "shown; don't assume which plant this is.)",
        )

    if decision.kind == "missing_plant":
        action_id = _propose_action(
            garden_id,
            goal_id,
            "create_plant",
            {"stake_no": n, "media_id": media_id},
            f"There's no plant {_label(n)} in this garden yet. Add {_label(n)} as a new plant?",
        )
        return IdentityOutcome(
            action_id=action_id,
            note=f"(The stake reads {_label(n)}, which isn't in this garden yet — a numbered "
            "plant may be missing, or it's a misread. A question has been shown.)",
        )
    return IdentityOutcome()


def _propose_from_model(
    garden_id: str,
    goal: dict[str, Any],
    tasks: list[dict[str, Any]],
    proposed: ProposedAction,
    recent_media_id: str | None = None,
) -> str | None:
    """Turns a model-proposed action into a validated proposal (ADR-0034 §3), or nothing."""
    goal_id = goal["goal_id"]
    if proposed.type == "complete_task" and proposed.task_title:
        wanted = proposed.task_title.strip().lower()
        task = next(
            (
                t
                for t in tasks
                if t.get("status") == "pending" and t.get("title", "").strip().lower() == wanted
            ),
            None,
        )
        if task:
            return _propose_action(
                garden_id,
                goal_id,
                "complete_task",
                {"task_id": task["task_id"]},
                f"Mark '{task.get('title', '')}' done?",
            )
    if proposed.type == "link_plant" and proposed.stake_no:
        plant_id = _plant_for_stake(garden_id)(proposed.stake_no)
        if plant_id:
            media_ids = goal.get("media_ids") or []
            media_id = recent_media_id or (media_ids[0] if media_ids else None)
            if goal.get("plant_id") and media_id:
                return _propose_action(
                    garden_id,
                    goal_id,
                    "set_plant",
                    {"media_id": media_id, "plant_id": plant_id},
                    f"Move this goal's photo to {_label(proposed.stake_no)}?",
                )
            return _propose_action(
                garden_id,
                goal_id,
                "set_goal_plant",
                {"plant_id": plant_id, "media_id": media_id},
                f"Link this goal to {_label(proposed.stake_no)}?",
            )
    return None


def _update_goal(garden_id: str, goal_id: str, **fields: Any) -> None:
    _get_table().update_item(
        Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
    )


def _write_event(garden_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort — an Event is an audit-log entry for the Activity screen (Phase 6), never
    something a turn's success should depend on. Duplicated in garden_handler.py rather than
    shared: app/api and app/orchestrator are separate deployable units (no cross-package Python
    imports in this monorepo)."""
    event_id = uuid.uuid4().hex
    created_at = datetime.now(UTC).isoformat()
    _get_table().put_item(
        Item={
            "pk": f"GARDEN#{garden_id}",
            "sk": f"EVENT#{created_at}#{event_id}",
            "event_id": event_id,
            "type": event_type,
            "payload": payload,
            "created_at": created_at,
        }
    )


def _write_message(
    garden_id: str, goal_id: str, *, role: str, content: str, action_id: str | None = None
) -> str:
    """Writes a chat Message and returns its id. `action_id` (ADR-0034) links an assistant reply
    to the proposed action it asks the gardener to confirm."""
    message_id = uuid.uuid4().hex
    created_at = datetime.now(UTC).isoformat()
    item = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"GOALMSG#{goal_id}#{created_at}#{message_id}",
        "message_id": message_id,
        "goal_id": goal_id,
        "role": role,
        "content": content,
        "created_at": created_at,
    }
    if action_id:
        item["action_id"] = action_id
    _get_table().put_item(Item=item)
    return message_id


def _write_plan_and_tasks(
    garden_id: str,
    goal_id: str,
    plan: PlanProposal,
    plant_id: str | None = None,
    trace: list[dict[str, Any]] | None = None,
) -> None:
    """Writes/replaces the goal's Plan + Task set. A revision always reflects the latest
    proposal for *remaining* work — any previously-proposed, not-yet-`done` task for this goal is
    cleared first. `TASK#{goal_id}#*` (not the flat `TASK#{task_id}` data-architecture.md §2
    originally sketched) is what makes that a single cheap prefix Query, not a table scan or a
    GSI — that sketch's key was optimized for the TasksDueIndex GSI, which is Phase 7+ work and
    doesn't exist yet.

    A `done` task is deliberately **never** deleted/replaced here (PA-05) — it's the historical
    record that a check-in happened, complete with its photo/feedback, and revising the plan
    going forward shouldn't silently erase that a real bug found live: every revision (chat- or,
    now, check-in-triggered) used to wipe the *entire* task list unconditionally, discarding any
    completed check-in's `status`/`media_id`/`feedback`. Preserving `done` tasks untouched is what
    makes a check-in turn safe to also revise the plan (PA-05's scope decision), not just describe
    the recommendation in text.

    `plant_id` (the Goal's own, if any) is stamped onto every new Task it proposes — this is what
    lets a later task check-in (garden_handler.py::post_task_checkin) tag its Media record with
    the right plant without a second lookup, completing the Plant/Goal/Task/Media chain.

    `trace` (PA-05): the specialist-consultation record for *this* turn, stored on the Plan item
    so `getGoalDetail` can surface "How this was decided" — always describes the current plan's
    reasoning, whichever kind of turn (submission/chat/check-in) produced it."""
    table = _get_table()
    existing = table.query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"TASK#{goal_id}#")
    )
    for item in existing.get("Items", []):
        if item.get("status") == "done":
            continue
        table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})

    plan_item: dict[str, Any] = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"PLAN#{goal_id}",
        "plan_id": goal_id,
        "goal_id": goal_id,
        "success_criteria": plan.success_criteria,
        "status": "PlanProposed",
    }
    if trace:
        plan_item["trace"] = trace
    table.put_item(Item=plan_item)
    try:
        _write_event(
            garden_id, "plan.updated", {"goalId": goal_id, "successCriteria": plan.success_criteria}
        )
    except Exception:
        logger.exception("Failed to write plan.updated event for goal %s", goal_id)
    for t in plan.tasks:
        task_id = uuid.uuid4().hex
        task_item = {
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
        if plant_id:
            task_item["plant_id"] = plant_id
        table.put_item(Item=task_item)


def _apply_turn_result(
    garden_id: str,
    goal_id: str,
    turn: ChatTurnResult,
    plant_id: str | None = None,
    trace: list[dict[str, Any]] | None = None,
    action_id: str | None = None,
) -> None:
    _write_message(garden_id, goal_id, role="assistant", content=turn.reply, action_id=action_id)
    now = datetime.now(UTC).isoformat()
    if turn.updated_plan is not None:
        _write_plan_and_tasks(garden_id, goal_id, turn.updated_plan, plant_id, trace)
        _update_goal(garden_id, goal_id, status="PlanProposed", updated_at=now)
    else:
        _update_goal(garden_id, goal_id, updated_at=now)


def _update_task_feedback(garden_id: str, goal_id: str, task_id: str, feedback: str) -> None:
    """Stamps a check-in turn's reply onto the checked-in Task (PA-05) so it renders right under
    that task's photo, in addition to appearing in the general chat thread via
    `_apply_turn_result`'s `_write_message` call. Safe to run after a plan revision — a `done`
    task (this one) is never touched by `_write_plan_and_tasks`."""
    _get_table().update_item(
        Key={"pk": f"GARDEN#{garden_id}", "sk": f"TASK#{goal_id}#{task_id}"},
        UpdateExpression="SET feedback = :f",
        ExpressionAttributeValues={":f": feedback},
    )


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


def _build_checkin_prompt(checkin_task: dict[str, Any]) -> str:
    """The new-turn prompt for a task check-in (PA-05/Phase 7.5+) — the resumed session already
    has the full prior conversation/plan; this is only the genuinely new information (a photo was
    just attached to this specific task)."""
    return (
        f"The gardener just checked in on task '{checkin_task.get('title', '')}' "
        f"({checkin_task.get('detail', '')}) with a new photo, attached to this message. "
        "Assess whether this looks like it's progressing toward the plan's success "
        "criteria. Reply with brief, encouraging feedback either way. If the "
        "check-in suggests the plan should change, revise it — completed tasks are "
        "preserved automatically, so it's safe to add or adjust the remaining ones."
    )


def _strip_trailing_unresolved_tool_use(agent: Agent) -> None:
    """Security mitigation (strands-capability-mapping.md's session-resume guidance): a trailing
    assistant message whose content ends in a toolUse block, with no corresponding toolResult
    message after it, would otherwise be replayed/executed without model reasoning if left in
    resumed history — defense-in-depth even though only this orchestrator ever writes to its own
    session today (a future bug/change could violate that invariant).

    No `strip_trailing_tool_use()` function exists in strands-agents==1.55.1 (verified by
    inspection) — this is a hand-rolled implementation of a documented pattern, not an SDK
    guarantee. Called immediately after `Agent(...)` construction: `SnapshotSessionManager`
    restores messages synchronously during `Agent.__init__` (via its `AgentInitializedEvent`
    hook), so `agent.messages` is already populated by the time `Agent(...)` returns."""
    messages = agent.messages
    if not messages:
        return
    last = messages[-1]
    if last.get("role") != "assistant":
        return
    content = last.get("content") or []
    if content and isinstance(content[-1], dict) and "toolUse" in content[-1]:
        agent.messages = messages[:-1]


def _build_orchestrator_agent(
    garden_id: str,
    goal_id: str,
    image_url,
    image_format,
    trace: list[dict[str, Any]] | None = None,
) -> Agent:
    # Phase 7.5+: SnapshotSessionManager keyed by goal_id (data-architecture.md §3.1,
    # strands-capability-mapping.md's recommendation) — every EventBridge wake for this goal_id
    # resumes the same messages/agent state instead of starting from a blank conversation. No
    # {env_name} segment in the prefix: the bucket NAME already physically separates
    # environments, so repeating it in the object-key prefix would be redundant nesting with zero
    # isolation benefit.
    session_manager = SnapshotSessionManager(
        session_id=goal_id,
        storage=S3Storage(bucket=AGENT_STATE_BUCKET_NAME, prefix="orchestrator-sessions/"),
    )
    # ADR-0032: garden context goes in the system prompt, which is rebuilt every invocation and
    # is never written into the session's message history, so it is always current and never
    # duplicated across resumed turns.
    garden_context = _load_garden_context(garden_id)
    system_prompt = (
        f"{ORCHESTRATOR_SYSTEM_PROMPT}\n\n{garden_context}"
        if garden_context
        else ORCHESTRATOR_SYSTEM_PROMPT
    )
    agent = Agent(
        model=BedrockModel(**({"model_id": MODEL_ID} if MODEL_ID else {})),
        system_prompt=system_prompt,
        tools=_build_tools(
            garden_id, image_url, image_format, trace, garden_context=garden_context
        ),
        session_manager=session_manager,
        trace_attributes={"session.id": goal_id, "garden.id": garden_id},
    )
    _strip_trailing_unresolved_tool_use(agent)
    return agent


def _finalize_trace(trace: list[dict[str, Any]], turn: ChatTurnResult, turn_ms: int) -> None:
    """Appends the orchestrator's own synthesis entry to `trace` — its "ms" is the turn's total
    wall-clock time minus whatever the specialist calls it made already accounted for, so the
    trace's per-entry durations sum to the turn's real total (matching how the reinstated
    "How this was decided" section reports "N specialists · Ts · resolved at runtime")."""
    specialist_ms = sum(e["ms"] for e in trace)
    trace.append(
        {
            "agent": "orchestrator",
            "says": _summarize(turn.reply),
            "ms": max(turn_ms - specialist_ms, 0),
            "is_orchestrator": True,
        }
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
    goal.setdefault("goal_id", goal_id)

    media_ids = goal.get("media_ids") or []
    photo = _resolve_photo(garden_id, media_ids)
    image_url, image_format = photo.url, photo.image_format

    # ADR-0034 §4: which plant does the goal's photo show? Runs before the model turn, by rule.
    identity = (
        _identify_photo(garden_id, goal, media_ids[0], photo)
        if image_url and media_ids
        else IdentityOutcome()
    )

    trace: list[dict[str, Any]] = []
    agent = _build_orchestrator_agent(garden_id, goal_id, image_url, image_format, trace)

    message = goal["description"]
    if image_url:
        # The model only sees this text — it has no other way of knowing a photo is actually
        # attached, and won't reliably call a vision-capable specialist without being told.
        message += "\n\n(A photo of the affected plant is attached to this goal.)"
    elif photo.unreadable:
        message += f"\n\n{UNREADABLE_PHOTO_NOTE}"
    if identity.note:
        message += f"\n\n{identity.note}"

    try:
        turn_started = time.monotonic()
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
        if turn.updated_plan is not None:
            _finalize_trace(trace, turn, int((time.monotonic() - turn_started) * 1000))
        action_id = identity.action_id
        if action_id is None and turn.proposed_action is not None:
            action_id = _propose_from_model(garden_id, goal, [], turn.proposed_action)
        _apply_turn_result(
            garden_id, goal_id, turn, goal.get("plant_id"), trace, action_id=action_id
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

    goal.setdefault("goal_id", goal_id)
    # ADR-0034 §3: the newest user message may carry photos and/or a task (a check-in).
    latest = next((m for m in reversed(messages) if m.get("role") == "user"), {})
    message_media = latest.get("media_ids") or []
    checkin_task = (
        next((t for t in tasks if t.get("task_id") == latest.get("task_id")), None)
        if latest.get("task_id")
        else None
    )

    photo = _resolve_photo(garden_id, message_media or goal.get("media_ids") or [])
    image_url, image_format = photo.url, photo.image_format

    identity = IdentityOutcome()
    if message_media and image_url:
        identity = _identify_photo(
            garden_id,
            goal,
            message_media[0],
            photo,
            task=checkin_task,
            user_note=latest.get("content", ""),
        )
    elif checkin_task and not message_media:
        # A text-only check-in ("mulching done today"): no photo, still confirmed with a tap.
        title = checkin_task.get("title", "")
        identity = IdentityOutcome(
            action_id=_propose_action(
                garden_id,
                goal_id,
                "complete_task",
                {"task_id": checkin_task["task_id"], "note": latest.get("content") or None},
                f"Mark '{title}' done?",
            ),
            note=f"(Check-in received for task '{title}' without a photo. A Confirm button to mark "
            "it done has been shown — acknowledge briefly; don't say it's done yet.)",
        )

    trace: list[dict[str, Any]] = []
    agent = _build_orchestrator_agent(garden_id, goal_id, image_url, image_format, trace)
    transcript = _format_transcript(goal, plan, tasks, messages)
    if message_media and image_url:
        transcript += "\n\n(The gardener's latest message has a photo attached.)"
    if photo.unreadable:
        transcript += f"\n\n{UNREADABLE_PHOTO_NOTE}"
    if identity.note:
        transcript += f"\n\n{identity.note}"

    try:
        turn_started = time.monotonic()
        turn = _run_turn(agent, transcript, synthesize_fallback_plan=plan is None)
        logger.info(
            "chat_turn_result garden_id=%s goal_id=%s has_plan=%s reply=%r",
            garden_id,
            goal_id,
            turn.updated_plan is not None,
            turn.reply[:500],
        )
        if turn.updated_plan is not None:
            _finalize_trace(trace, turn, int((time.monotonic() - turn_started) * 1000))
        action_id = identity.action_id
        if action_id is None and turn.proposed_action is not None:
            action_id = _propose_from_model(
                garden_id,
                goal,
                tasks,
                turn.proposed_action,
                recent_media_id=message_media[0] if message_media else None,
            )
        elif action_id and checkin_task:
            _set_action_feedback(garden_id, goal_id, action_id, turn.reply)
        _apply_turn_result(
            garden_id, goal_id, turn, goal.get("plant_id"), trace, action_id=action_id
        )
    except Exception as e:
        logger.exception("chat_turn_failed garden_id=%s goal_id=%s", garden_id, goal_id)
        _write_message(
            garden_id,
            goal_id,
            role="assistant",
            content=f"Sorry, something went wrong processing that ({e}). Please try again.",
        )


def handle_task_checkin_received(detail: dict[str, Any]) -> None:
    """`task.checkin.received` (PA-05): the third kind of turn — a check-in is "just a turn" like
    goal submission or a chat message, assessing the task's own new photo against the plan and,
    same as a chat turn, optionally revising it (safe because `_write_plan_and_tasks` now
    preserves `done` tasks untouched). The reply is written to the chat thread exactly like any
    other turn (`_apply_turn_result`) and additionally stamped onto the checked-in Task's
    `feedback` field so it renders right under that task's check-in photo."""
    garden_id = detail["gardenId"]
    goal_id = detail["goalId"]
    task_id = detail["taskId"]

    goal = _load_goal(garden_id, goal_id)
    if not goal:
        logger.error("goal_not_found garden_id=%s goal_id=%s", garden_id, goal_id)
        return

    plan = _load_plan(garden_id, goal_id)
    tasks = _load_tasks(garden_id, goal_id)
    checkin_task = next((t for t in tasks if t.get("task_id") == task_id), None)
    if not checkin_task:
        logger.error(
            "checkin_task_not_found garden_id=%s goal_id=%s task_id=%s", garden_id, goal_id, task_id
        )
        return
    messages = _load_messages(garden_id, goal_id)

    photo = _resolve_photo(
        garden_id, [checkin_task["media_id"]] if checkin_task.get("media_id") else []
    )
    image_url, image_format = photo.url, photo.image_format

    trace: list[dict[str, Any]] = []
    agent = _build_orchestrator_agent(garden_id, goal_id, image_url, image_format, trace)
    transcript = _format_transcript(goal, plan, tasks, messages, checkin_task=checkin_task)
    if photo.unreadable:
        transcript += f"\n\n{UNREADABLE_PHOTO_NOTE}"

    try:
        turn_started = time.monotonic()
        turn = _run_turn(agent, transcript, synthesize_fallback_plan=False)
        logger.info(
            "checkin_feedback_result garden_id=%s goal_id=%s task_id=%s has_plan=%s reply=%r",
            garden_id,
            goal_id,
            task_id,
            turn.updated_plan is not None,
            turn.reply[:500],
        )
        if turn.updated_plan is not None:
            _finalize_trace(trace, turn, int((time.monotonic() - turn_started) * 1000))
        _apply_turn_result(garden_id, goal_id, turn, goal.get("plant_id"), trace)
        _update_task_feedback(garden_id, goal_id, task_id, turn.reply)
    except Exception:
        logger.exception(
            "checkin_feedback_failed garden_id=%s goal_id=%s task_id=%s",
            garden_id,
            goal_id,
            task_id,
        )


def _bump_task_due_date(garden_id: str, goal_id: str, task_id: str) -> None:
    """Pushes due_date/gsi1sk forward by FOLLOWUP_OFFSET_DAYS. Correctness-critical: without
    this, the tracker would re-emit followup.due for the SAME task on every subsequent tick until
    the gardener checks in, since the task would remain visible in TasksDueIndex at its old
    (now-past) due_date."""
    due = (datetime.now(UTC) + timedelta(days=FOLLOWUP_OFFSET_DAYS)).isoformat()
    _get_table().update_item(
        Key={"pk": f"GARDEN#{garden_id}", "sk": f"TASK#{goal_id}#{task_id}"},
        UpdateExpression="SET due_date = :d, gsi1pk = :p, gsi1sk = :d",
        ExpressionAttributeValues={":d": due, ":p": "TASK_STATUS#pending"},
    )


def handle_followup_due(detail: dict[str, Any]) -> None:
    """`followup.due` (Phase 7.5+): the tracker/scheduler found a pending task past its due-date.
    Deliberately NOT "just a turn" like the other three handlers — no Agent/BedrockModel, no
    specialist consultation, no _run_turn. A proactive nudge is a deterministic, non-model action:
    write a chat message, log an Activity event, and push the due-date forward so this same task
    doesn't refire next tick before the gardener responds."""
    garden_id = detail["gardenId"]
    goal_id = detail["goalId"]
    task_id = detail["taskId"]

    task_item = (
        _get_table()
        .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"TASK#{goal_id}#{task_id}"})
        .get("Item")
    )
    if not task_item or task_item.get("status") != "pending":
        # Missing, or already checked in between the tracker's query and this invocation (a real
        # race given the tracker's own at-least-once/no-dedupe posture) — post_task_checkin
        # already removed gsi1pk/gsi1sk/due_date in that case; re-bumping here would incorrectly
        # resurrect a done task into the sparse index.
        logger.info(
            "followup_due_skipped garden_id=%s goal_id=%s task_id=%s status=%s",
            garden_id,
            goal_id,
            task_id,
            task_item.get("status") if task_item else "not_found",
        )
        return

    try:
        _write_message(
            garden_id,
            goal_id,
            role="assistant",
            content=(
                f"Just checking in — how's '{task_item.get('title', '')}' going? Check in with "
                "a quick photo whenever you get to it."
            ),
        )
    except Exception:
        logger.exception(
            "followup_due_message_failed garden_id=%s goal_id=%s task_id=%s",
            garden_id,
            goal_id,
            task_id,
        )

    try:
        _write_event(
            garden_id,
            "task.followup_due",
            {"goalId": goal_id, "taskId": task_id, "taskTitle": task_item.get("title", "")},
        )
    except Exception:
        logger.exception(
            "followup_due_event_failed garden_id=%s goal_id=%s task_id=%s",
            garden_id,
            goal_id,
            task_id,
        )

    try:
        _bump_task_due_date(garden_id, goal_id, task_id)
    except Exception:
        # The one failure mode that must be loud: silence here directly causes the "refires
        # forever" bug this whole design otherwise prevents. Never re-raise though — a raise
        # would trigger EventBridge's automatic retry, re-running the message/event writes above
        # too (double-nudging), which is worse than logging and moving on.
        logger.exception(
            "followup_due_bump_failed garden_id=%s goal_id=%s task_id=%s",
            garden_id,
            goal_id,
            task_id,
        )


def handler(event: dict[str, Any], _context: Any) -> None:
    detail_type = event.get("detail-type")
    detail = event.get("detail") or {}
    if detail_type == "goal.submitted":
        handle_goal_submitted(detail)
    elif detail_type == "goal.message.received":
        handle_goal_message_received(detail)
    elif detail_type == "task.checkin.received":
        handle_task_checkin_received(detail)
    elif detail_type == "followup.due":
        handle_followup_due(detail)
    else:
        logger.warning("unhandled_event detail_type=%s", detail_type)
