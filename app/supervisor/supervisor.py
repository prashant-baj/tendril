"""Supervisor Lambda (ADR-0039): the garden-level agent above the orchestrator. First slice:
assess farm activities (ADR-0038) and keep their schedules sensible.

Triggers (EventBridge, asynchronous):

- `activity.planned` {gardenId, activityId} — assess a planned or edited activity; may propose a
  schedule.
- `activity.logged` {gardenId, logId} — assess work that was done (photos go to the vision
  specialist); may propose the next due date.
- `supervisor.daily` {} — once a day (06:00 IST): for each garden with planned work, (re)assess
  anything not yet assessed or changed since, then review what's due against the forecast and
  recent work and propose moves. Daily proposals are written only when
  `SUPERVISOR_PROPOSALS_ENABLED` is on (ADR-0039 §6: after the scheduling evals pass).

What it may write (ADR-0039 §4):

- `assessment` on an Activity / activity log — directly, no confirm (information only, ADR-0037);
- garden-level proposals `ACTION#garden#{id}` — only the Client API's confirm endpoint applies them.

It never changes plants, tasks, goals or statuses, never deletes, and never sends personal data
(notification recipients) to a model. Specialists are consulted read-only, at most
`MAX_CONSULTATIONS` per run. Due-date arithmetic stays in code (Client API).
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import boto3
import supervisor_rollup as rollup
from boto3.dynamodb.conditions import Attr, Key
from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.supervisor")

APP_TABLE_NAME = os.getenv("APP_TABLE_NAME")  # injected by AgentCoreStack; never hardcoded
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")
MODEL_ID = os.getenv("MODEL_ID") or None
MAX_TOKENS = int(os.getenv("SUPERVISOR_MAX_TOKENS") or 8192)
PROMPT_NAME = os.getenv("PROMPT_NAME")
GUARDRAIL_NAME = os.getenv("GUARDRAIL_NAME")
AGENT_MANIFEST: dict[str, dict[str, Any]] = json.loads(os.getenv("AGENT_MANIFEST", "{}"))
PROPOSALS_ENABLED = os.getenv("SUPERVISOR_PROPOSALS_ENABLED", "false").lower() == "true"
MAX_CONSULTATIONS = 2
MAX_DAILY_ASSESSMENTS = 10
GARDEN_ACTION_PREFIX = "ACTION#garden#"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT = 8

DEFAULT_SYSTEM_PROMPT = (
    "You are Tendril's Supervisor for a farm. You judge garden-wide work (inputs such as "
    "fertilizers, drenches and sprays, and operations such as watering, leaching, mulching and "
    "weeding) for its benefit to the farm and for any issues or harm, using the garden summary "
    "you are given: soil and irrigation, recent work and its timing, plants on the watch list, "
    "and the forecast. Be concrete and brief. Flag interactions and spacing problems with recent "
    "work, weather risks (rain soon after a spray or drench), soil risks (over-watering clay, salt "
    "crust) and dose concerns. Say 'unknown' when the summary doesn't give enough to judge. You "
    "never change anything yourself: schedule ideas are proposals the owner confirms."
)

_table = None
_s3 = None
_agentcore = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(APP_TABLE_NAME)
    return _table


def _get_s3():
    global _s3
    if _s3 is None:
        from botocore.config import Config

        _s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _s3


def _get_agentcore():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore")
    return _agentcore


# --- structured output: required fields only (Nova 2 Lite skips optional nested ones) -----------


class Conflict(BaseModel):
    conflicting_work: str = Field(description="The other activity or input it conflicts with.")
    reason: str = Field(description="Why, in one short sentence.")


class AssessmentOutput(BaseModel):
    verdict: Literal["beneficial", "caution", "harmful", "unknown"] = Field(
        description="'beneficial' if it helps and no real risk; 'caution' if it helps but timing, "
        "dose, weather or soil make it risky; 'harmful' if it is likely to damage plants or soil; "
        "'unknown' if the summary isn't enough to judge."
    )
    summary: str = Field(description="One sentence for the owner.")
    benefits: list[str] = Field(description="Short phrases; empty list if none.")
    risks: list[str] = Field(description="Short phrases; empty list if none.")
    conflicts: list[Conflict] = Field(
        description="Clashes with recent or planned work (spacing, interactions); empty if none."
    )
    suggestion: str = Field(description="A safer timing/dose/alternative, or '' if none.")
    proposal: Literal["none", "set_schedule", "move_due"] = Field(
        description="'set_schedule' to suggest a repeat interval for recurring work that has none "
        "or a poor one; 'move_due' to suggest a better next due date; 'none' otherwise."
    )
    min_days: int = Field(description="For set_schedule: minimum days between repeats, else 0.")
    max_days: int = Field(description="For set_schedule: maximum days between repeats, else 0.")
    due_on: str = Field(description="For move_due: the suggested date YYYY-MM-DD, else ''.")
    proposal_reason: str = Field(description="One sentence for the owner, or ''.")


class AgendaItem(BaseModel):
    type: Literal["move_due", "pause_activity", "resume_activity", "set_schedule"] = Field(
        description="'move_due' to shift work a few days (e.g. past forecast rain or heat) — the "
        "usual fix for a temporary problem; 'pause_activity' only when the work should stop "
        "until further notice; 'resume_activity' for paused work that should restart; "
        "'set_schedule' to change how often it repeats."
    )
    activity_id: str = Field(description="The id of a planned activity from the summary.")
    due_on: str = Field(description="For move_due: YYYY-MM-DD, else ''.")
    min_days: int = Field(description="For set_schedule, else 0.")
    max_days: int = Field(description="For set_schedule, else 0.")
    reason: str = Field(description="One sentence for the owner.")


class AgendaOutput(BaseModel):
    summary: str = Field(description="One or two sentences on the state of the farm work today.")
    suggestions: list[AgendaItem] = Field(
        description="At most 3 suggestions, only where a change clearly helps; empty otherwise."
    )


# --- model, prompt, guardrail, specialists -------------------------------------------------------


def _resolve_by_name(list_call, key: str, name: str) -> str | None:
    token = None
    while True:
        resp = list_call(**({"nextToken": token} if token else {}))
        for summary in resp.get(key, []):
            if summary.get("name") == name:
                return summary.get("id")
        token = resp.get("nextToken")
        if not token:
            return None


def load_system_prompt() -> str:
    """ADR-0006: the externalized prompt by its stable name; the built-in default if unavailable."""
    if not PROMPT_NAME:
        return DEFAULT_SYSTEM_PROMPT
    try:
        client = boto3.client("bedrock-agent")
        prompt_id = _resolve_by_name(client.list_prompts, "promptSummaries", PROMPT_NAME)
        if not prompt_id:
            return DEFAULT_SYSTEM_PROMPT
        resp = client.get_prompt(promptIdentifier=prompt_id)
        variants = resp.get("variants") or []
        variant = next(
            (v for v in variants if v.get("name") == resp.get("defaultVariant")), variants[0]
        )
        return variant["templateConfiguration"]["text"]["text"]
    except Exception as e:
        logger.warning("prompt_load_failed name=%s error=%s", PROMPT_NAME, e)
        return DEFAULT_SYSTEM_PROMPT


def build_model() -> BedrockModel:
    """ADR-0008: the externalized guardrail by name, fail-open like the specialists."""
    kwargs: dict[str, Any] = {"max_tokens": MAX_TOKENS}
    if MODEL_ID:
        kwargs["model_id"] = MODEL_ID
    if GUARDRAIL_NAME:
        try:
            guardrail_id = _resolve_by_name(
                boto3.client("bedrock").list_guardrails, "guardrails", GUARDRAIL_NAME
            )
            if guardrail_id:
                kwargs["guardrail_id"] = guardrail_id
                kwargs["guardrail_version"] = "DRAFT"
        except Exception as e:
            logger.warning("guardrail_resolve_failed name=%s error=%s", GUARDRAIL_NAME, e)
    return BedrockModel(**kwargs)


def specialist_tools(garden_id: str, image_url: str | None, budget: list[int]) -> list:
    """Read-only consultation of the registry's specialists (ADR-0039 §4), capped per run."""
    tools = []
    for name, entry in AGENT_MANIFEST.items():
        if entry.get("internal"):
            continue

        def make(
            name: str = name, arn: str = entry["arn"], description: str = entry["description"]
        ):
            @tool(name=name, description=f"Consult (read-only): {description}")
            def consult(question: str) -> str:
                if budget[0] >= MAX_CONSULTATIONS:
                    return "Consultation limit reached for this run; decide with what you have."
                budget[0] += 1
                payload: dict[str, Any] = {"prompt": question, "gardenId": garden_id}
                if image_url:
                    payload["imageUrl"], payload["imageFormat"] = image_url, "jpeg"
                started = time.monotonic()
                try:
                    resp = _get_agentcore().invoke_agent_runtime(
                        agentRuntimeArn=arn,
                        runtimeSessionId=uuid.uuid4().hex + uuid.uuid4().hex,
                        payload=json.dumps(payload).encode("utf-8"),
                    )
                    text = json.loads(resp["response"].read()).get("result", "")
                    logger.info(
                        "supervisor_consult name=%s ms=%d",
                        name,
                        int((time.monotonic() - started) * 1000),
                    )
                    return text
                except Exception as e:
                    logger.exception("supervisor_consult_failed name=%s", name)
                    return f"The {name} specialist is unavailable ({e})."

            return consult

        tools.append(make())
    return tools


def run_structured(prompt: str, model_cls, *, garden_id: str, image_url: str | None = None):
    """One turn: free reasoning with read-only specialist tools, then structured extraction on
    the same conversation. Returns the model object, or None if structuring failed."""
    budget = [0]
    agent = Agent(
        model=build_model(),
        system_prompt=load_system_prompt(),
        tools=specialist_tools(garden_id, image_url, budget),
        callback_handler=None,
    )
    agent(prompt)
    try:
        result = agent(structured_output_model=model_cls)
        return result.structured_output
    except Exception:
        logger.exception("supervisor_structured_output_failed garden_id=%s", garden_id)
        return None


# --- data loading ----------------------------------------------------------------------------


def _query_prefix(garden_id: str, prefix: str, **kwargs) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    query = {
        "KeyConditionExpression": Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(prefix),
        **kwargs,
    }
    while True:
        resp = _get_table().query(**query)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        query["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _get(garden_id: str, sk: str) -> dict[str, Any] | None:
    return _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": sk}).get("Item")


def _forecast(geolocation: str) -> list[dict[str, Any]] | None:
    """3-day forecast (rain matters for sprays and drenches). The garden stores free text, so
    try the whole string, then each comma-separated part. Best-effort; None on any failure."""
    try:
        coords = None
        for query in [geolocation, *[p.strip() for p in geolocation.split(",")]]:
            if not query:
                continue
            url = f"{GEOCODING_URL}?{urllib.parse.urlencode({'name': query, 'count': 1})}"
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
                results = json.loads(resp.read()).get("results") or []
            if results:
                coords = results[0]["latitude"], results[0]["longitude"]
                break
        if not coords:
            return None
        url = (
            f"{FORECAST_URL}?latitude={coords[0]}&longitude={coords[1]}"
            "&daily=temperature_2m_max,precipitation_sum,precipitation_probability_max"
            "&forecast_days=3&timezone=auto"
        )
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
            daily = json.loads(resp.read()).get("daily") or {}
        return [
            {
                "date": d,
                "tmax": daily.get("temperature_2m_max", [None])[i],
                "rain_mm": daily.get("precipitation_sum", [0])[i],
                "rain_chance": daily.get("precipitation_probability_max", [None])[i],
            }
            for i, d in enumerate(daily.get("time") or [])
        ]
    except Exception as e:
        logger.warning("forecast_failed error=%s", e)
        return None


def garden_rollup(
    garden_id: str, today: str
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """(rollup text, garden metadata, activities)."""
    metadata = _get(garden_id, "METADATA")
    if not metadata:
        return "", None, []
    plants = _query_prefix(garden_id, "PLANT#")
    activities = _query_prefix(garden_id, "ACTIVITY#")
    logs = _query_prefix(garden_id, "ACTLOG#")
    notes: dict[str, str] = {}
    for p in plants:
        if p.get("latest_status") == "watch":
            entries = (
                _get_table()
                .query(
                    KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
                    & Key("sk").begins_with(f"PLANTLOG#{p['plant_id']}#"),
                    ScanIndexForward=False,
                    Limit=5,
                )
                .get("Items", [])
            )
            note = next((e.get("note") for e in entries if e.get("note")), "")
            notes[p["plant_id"]] = note[:200]
    text = rollup.build_rollup(
        metadata=metadata,
        plants=plants,
        notes_by_plant=notes,
        logs=logs,
        activities=activities,
        forecast=_forecast(metadata.get("geolocation", "")),
        today=today,
    )
    return text, metadata, activities


def _first_photo_url(garden_id: str, media_ids: list[str]) -> str | None:
    """The analysis copy (or a video's poster frame) of the first processed photo, presigned."""
    for media_id in media_ids:
        media = _get(garden_id, f"MEDIA#{media_id}")
        if media and media.get("processing_status") == "ready" and media.get("analysis_s3_key"):
            return _get_s3().generate_presigned_url(
                "get_object",
                Params={"Bucket": MEDIA_BUCKET_NAME, "Key": media["analysis_s3_key"]},
                ExpiresIn=600,
            )
    return None


# --- writes ---------------------------------------------------------------------------------


def assessment_item(out: AssessmentOutput, version: int | None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "verdict": out.verdict,
        "summary": out.summary[:500],
        "benefits": [b[:200] for b in out.benefits[:6]],
        "risks": [r[:200] for r in out.risks[:6]],
        "conflicts": [
            {"with": c.conflicting_work[:120], "reason": c.reason[:200]} for c in out.conflicts[:4]
        ],
        "assessed_at": datetime.now(UTC).isoformat(),
        "assessed_by": "supervisor",
        "model": MODEL_ID or "default",
    }
    if out.suggestion.strip():
        item["suggestion"] = out.suggestion.strip()[:300]
    if version is not None:
        item["assessed_version"] = int(version)
    return item


def write_assessment(garden_id: str, sk: str, assessment: dict[str, Any]) -> None:
    """Only the `assessment` attribute, and only on an item that still exists (ADR-0039 §4)."""
    _get_table().update_item(
        Key={"pk": f"GARDEN#{garden_id}", "sk": sk},
        UpdateExpression="SET assessment = :a",
        ExpressionAttributeValues={":a": assessment},
        ConditionExpression="attribute_exists(pk)",
    )


def validate_proposal(
    kind: str, params: dict[str, Any], activity: dict[str, Any] | None, today: str
) -> str | None:
    """Code checks before a proposal is stored (ADR-0039 §5). Returns the reason it's invalid."""
    if kind != "create_activity" and not activity:
        return "unknown activity"
    if kind == "set_schedule":
        low, high = (
            params.get("schedule", {}).get("minDays"),
            params.get("schedule", {}).get("maxDays"),
        )
        if not (isinstance(low, int) and isinstance(high, int) and 1 <= low <= high <= 365):
            return "bad interval"
    if kind == "move_due" and not rollup.valid_due_on(params.get("due_on", ""), today):
        return "bad date"
    if kind == "pause_activity" and activity.get("status") != "active":
        return "not active"
    if kind == "resume_activity" and activity.get("status") != "paused":
        return "not paused"
    return None


def propose(
    garden_id: str,
    kind: str,
    params: dict[str, Any],
    summary: str,
    reason: str,
    activity: dict[str, Any] | None,
    today: str,
) -> str | None:
    error = validate_proposal(kind, params, activity, today)
    if error:
        logger.warning("supervisor_proposal_dropped type=%s reason=%s", kind, error)
        return None
    activity_id = (activity or {}).get("activity_id")
    # A newer proposal on the same activity supersedes older open ones.
    for old in _query_prefix(garden_id, GARDEN_ACTION_PREFIX):
        if (
            old.get("status") == "proposed"
            and activity_id
            and old.get("activity_id") == activity_id
        ):
            try:
                _get_table().update_item(
                    Key={"pk": old["pk"], "sk": old["sk"]},
                    UpdateExpression="SET #s = :sup",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":sup": "superseded", ":p": "proposed"},
                    ConditionExpression="#s = :p",
                )
            except Exception:
                logger.warning("supersede_failed sk=%s", old["sk"])
    action_id = uuid.uuid4().hex
    item = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"{GARDEN_ACTION_PREFIX}{action_id}",
        "action_id": action_id,
        "type": kind,
        "params": params,
        "summary": summary[:300],
        "reason": reason[:300],
        "status": "proposed",
        "created_by": "supervisor",
        "created_at": datetime.now(UTC).isoformat(),
    }
    if activity_id:
        item["activity_id"] = activity_id
    _get_table().put_item(Item=item)
    logger.info("supervisor_proposed garden_id=%s type=%s action_id=%s", garden_id, kind, action_id)
    return action_id


def _proposal_from_assessment(
    garden_id: str, out: AssessmentOutput, activity: dict[str, Any] | None, today: str
) -> None:
    if not activity or out.proposal == "none":
        return
    title = activity.get("title", "this activity")
    if out.proposal == "set_schedule":
        propose(
            garden_id,
            "set_schedule",
            {
                "activity_id": activity["activity_id"],
                "schedule": {"minDays": out.min_days, "maxDays": out.max_days},
            },
            f"Repeat '{title}' every {out.min_days}-{out.max_days} days?",
            out.proposal_reason,
            activity,
            today,
        )
    elif out.proposal == "move_due":
        propose(
            garden_id,
            "move_due",
            {"activity_id": activity["activity_id"], "due_on": out.due_on},
            f"Move '{title}' to {out.due_on}?",
            out.proposal_reason,
            activity,
            today,
        )


# --- handlers ---------------------------------------------------------------------------------


def assess(garden_id: str, sk: str, *, logged: bool, today: str) -> AssessmentOutput | None:
    item = _get(garden_id, sk)
    if not item:
        logger.warning("assess_missing garden_id=%s sk=%s", garden_id, sk)
        return None
    version = item.get("version")
    previous = item.get("assessment") or {}
    if logged and previous:
        return None  # a log is assessed once (retried event)
    if not logged and version is not None and previous.get("assessed_version") == version:
        return None  # already assessed this version (retried event)
    summary, metadata, activities = garden_rollup(garden_id, today)
    if not metadata:
        return None
    image_url = _first_photo_url(garden_id, item.get("media_ids") or []) if logged else None
    prompt = (
        f"{summary}\n\n---\n\n{rollup.describe(item, logged=logged, today=today)}\n\n"
        "Assess this work for the farm: its benefits, and any issues or harm (timing against "
        "recent work, interactions, dose, soil, weather, plants on the watch list). "
        + ("A photo of the work is available to the vision specialist. " if image_url else "")
        + "Consult a specialist only if it changes your answer."
    )
    out = run_structured(prompt, AssessmentOutput, garden_id=garden_id, image_url=image_url)
    if out is None:
        out = AssessmentOutput(
            verdict="unknown",
            summary="Couldn't assess this right now.",
            benefits=[],
            risks=[],
            conflicts=[],
            suggestion="",
            proposal="none",
            min_days=0,
            max_days=0,
            due_on="",
            proposal_reason="",
        )
    write_assessment(garden_id, sk, assessment_item(out, version))
    logger.info(
        "supervisor_assessed garden_id=%s sk=%s verdict=%s",
        garden_id,
        sk.split("#")[0],
        out.verdict,
    )
    activity = (
        item
        if not logged
        else (
            _get(garden_id, f"ACTIVITY#{item['activity_id']}") if item.get("activity_id") else None
        )
    )
    _proposal_from_assessment(garden_id, out, activity, today)
    return out


def daily(garden_id: str, today: str) -> None:
    """The daily agenda for one garden (ADR-0039 §2)."""
    activities = [
        a for a in _query_prefix(garden_id, "ACTIVITY#") if a.get("status") in ("active", "paused")
    ]
    if not activities:
        return
    # Catch up: anything never assessed, or changed since its assessment.
    pending = [
        a
        for a in activities
        if (a.get("assessment") or {}).get("assessed_version") != a.get("version")
    ][:MAX_DAILY_ASSESSMENTS]
    for a in pending:
        assess(garden_id, a["sk"], logged=False, today=today)
    summary, metadata, activities = garden_rollup(garden_id, today)
    if not metadata:
        return
    out = run_structured(
        f"{summary}\n\n---\n\nReview today's farm work. Suggest moving, pausing, resuming or "
        "re-spacing a planned activity only where it clearly helps (rain forecast before a spray "
        "or drench, overdue work, work too close to a recent input). Use activity ids from the "
        "summary.",
        AgendaOutput,
        garden_id=garden_id,
    )
    if out is None:
        return
    logger.info(
        "supervisor_daily garden_id=%s suggestions=%d proposals_enabled=%s summary=%r",
        garden_id,
        len(out.suggestions),
        PROPOSALS_ENABLED,
        out.summary[:300],
    )
    if not PROPOSALS_ENABLED:
        return  # ADR-0039 §6: logged only until the scheduling evals pass
    by_id = {a["activity_id"]: a for a in activities}
    for s in out.suggestions[:3]:
        activity = by_id.get(s.activity_id)
        title = (activity or {}).get("title", "this activity")
        if s.type == "move_due":
            params, summary_text = (
                {"activity_id": s.activity_id, "due_on": s.due_on},
                f"Move '{title}' to {s.due_on}?",
            )
        elif s.type == "set_schedule":
            params = {
                "activity_id": s.activity_id,
                "schedule": {"minDays": s.min_days, "maxDays": s.max_days},
            }
            summary_text = f"Repeat '{title}' every {s.min_days}-{s.max_days} days?"
        elif s.type == "pause_activity":
            params, summary_text = {"activity_id": s.activity_id}, f"Pause '{title}'?"
        else:
            params, summary_text = {"activity_id": s.activity_id}, f"Resume '{title}'?"
        propose(garden_id, s.type, params, summary_text, s.reason, activity, today)


def _gardens_with_activities() -> list[str]:
    """Gardens that have planned work. A filtered scan: fine at this scale (a handful of gardens);
    a sparse index would replace it if gardens grow into the thousands."""
    gardens: set[str] = set()
    kwargs: dict[str, Any] = {
        "FilterExpression": Attr("sk").begins_with("ACTIVITY#"),
        "ProjectionExpression": "pk",
    }
    while True:
        resp = _get_table().scan(**kwargs)
        gardens.update(i["pk"].removeprefix("GARDEN#") for i in resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return sorted(gardens)
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    kind = event.get("detail-type")
    detail = event.get("detail") or {}
    today = datetime.now(UTC).date().isoformat()
    logger.info("supervisor_event type=%s", kind)
    if kind == "activity.planned":
        assess(detail["gardenId"], f"ACTIVITY#{detail['activityId']}", logged=False, today=today)
    elif kind == "activity.logged":
        assess(
            detail["gardenId"],
            f"ACTLOG#{detail['doneOn']}#{detail['logId']}",
            logged=True,
            today=today,
        )
    elif kind == "supervisor.daily":
        for garden_id in _gardens_with_activities():
            try:
                daily(garden_id, today)
            except Exception:
                logger.exception("supervisor_daily_failed garden_id=%s", garden_id)
    else:
        logger.warning("supervisor_unknown_event type=%s", kind)
    return {"ok": True}
