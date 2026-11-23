"""Garden Client API handler (OB-01, OB-02, WS-03) — gardens, media, plants, and goal intake.

Implements every operation in app/api/openapi.yaml, invoked by API Gateway's Lambda proxy
integration (ClientApiStack, ADR-0011) — one Lambda for the whole Client API for now (ADR-0011's
"one Lambda for both operations for now" note, extended as new operations land):

- `createGarden`/`getGarden` (OB-01): both DynamoDB records a Garden needs written in one
  transaction (data-architecture.md §2) — canonical `GARDEN#{id}/METADATA` + the
  `USER#{user_id}/GARDEN#{id}` ownership index, never one without the other. Accepts an optional
  client-supplied `gardenId` — the only entity whose id can come from the frontend, needed
  because a garden *photo* has to be uploaded (`createMediaUpload`, needs an existing `gardenId`)
  before the garden itself exists; also accepts an optional `mediaId`, mirroring `Plant`/
  `Task.media_id`. `getGarden` resolves a fresh presigned `photoUrl` when one is set.
- `listGardens`: every garden the caller owns (multi-garden switcher) — one cheap `Query` on
  that same ownership index, no GSI/scan.
- `getGardenWeather`: real current weather for a garden's own location — geocodes the Garden's
  free-text `geolocation` via Open-Meteo's free Geocoding API (a Garden never stores lat/lon),
  then calls the same forecast API `app/tools/weather/handler.py` already uses for specialists
  (duplicated, not imported — separate deployable units). Deliberately UI-agnostic (raw
  `temperatureC`/`weatherCode`) — the frontend maps `weatherCode` to presentation.
- `createMediaUpload` (OB-02): issues a presigned S3 PUT URL against `FoundationStack`'s media
  bucket and writes the `Media` record immediately (`GARDEN#{id}/MEDIA#{media_id}`) — this is
  the only step with the `s3_key`/`content_type` needed to write it (data-architecture.md §4).
- `createPlant` (OB-02): writes the `Plant` record (`GARDEN#{id}/PLANT#{plant_id}`) and, when a
  `mediaId` from a prior `createMediaUpload` call is given, atomically links that Media record
  to this plant (`plant_id` set via the same transaction) — so a Plant is never left pointing at
  a Media record that doesn't actually exist.
- `listPlants`/`deletePlant`: complete the Plant lifecycle so the frontend can stop hardcoding
  plant data. `listPlants` queries every `PLANT#*` item under the garden's partition (a single
  `Query`, not a table `Scan` — cheap even as a garden's item count grows), then resolves a fresh
  presigned GET `photoUrl` per plant that has a linked photo (`_generate_download_url`, shared
  with `getGoalDetail`) — OB-03. `deletePlant` removes the Plant item only; it deliberately does
  **not** cascade-delete the Plant's linked Media/S3 object — that photo may still be referenced
  elsewhere (data-architecture.md's Media records are independently keyed), and no story has
  asked for real cleanup semantics yet.
- `createGoal` (WS-03): persists a `Goal` record (status `Intake`) and publishes a
  `goal.submitted` EventBridge event — `AgentCoreStack`'s rule routes it to the orchestrator
  Lambda asynchronously (ADR-0004/ADR-0012). **Known trade-off:** the DynamoDB write and the
  EventBridge publish aren't atomic (no cross-service transaction exists for this); if the
  write succeeds but the publish fails, the Goal record persists with no event ever fired. This
  epic is explicitly scoped to "prove the pipe" (walking-skeleton.md), not production-hardened
  exactly-once delivery — an outbox/saga pattern is future work if this gap ever matters in
  practice. Accepts an optional `plantId` (closing the field data-architecture.md always reserved
  but no code ever set) — when given, every attached Media record is transactionally updated with
  both `goal_id` (always) and `plant_id` (only when the goal has one), completing the
  Plant/Goal/Media traceability chain the same way `createPlant` already does for Plant/Media.
- `postTaskCheckin`: attaches a check-in photo to a `Task` and flips it to `status=done` in one
  transaction — the last leg of the Plant/Goal/Task/Media chain (a `Task` inherits `plant_id`
  from its `Goal` when the orchestrator proposes it, so the check-in's Media update can set
  `plant_id`/`goal_id`/`task_id` all three without a second lookup). Deliberately one check-in
  per task, not a repeatable progress log — that's the Phase 7+ tracker/outcome loop. After that
  transaction commits, also publishes a `task.checkin.received` EventBridge event (best-effort —
  see the inline comment for why this one deliberately doesn't fail the request) so the
  orchestrator can assess the new photo and write feedback back onto the task (PA-05).
- `listGoals`/`getGoalDetail` (PA-01/PA-02/PA-03/PA-05): read the real `Plan`/`Task`/`Message`
  records the orchestrator now writes (`app/orchestrator/orchestrator.py`), instead of the
  free-text `orchestrator_result` field earlier stories used. `getGoalDetail` also generates a
  fresh presigned **GET** url per attached Media record — never stored/reused, same posture as
  OB-02's upload URLs, just the read-side mirror — and surfaces each task's check-in `feedback`
  and the plan's specialist-consultation `trace` (PA-05) when either is present.
- `postGoalMessage` (PA-02): writes a `role="user"` Message and publishes `goal.message.received`
  — the orchestrator resumes the conversation asynchronously, same posture as `createGoal`.
- `approvePlan` (PA-02): a synchronous, deterministic status flip (`Plan`/`Goal` → `Approved`) —
  approving a plan needs no model reasoning, so this never goes through the orchestrator/
  EventBridge at all, unlike `postGoalMessage`.
- `listTasks`/`listActivity` (Phase 6): `listTasks` returns every task across every goal in a
  garden — one cheap `Query` on the shared `TASK#*` prefix, same partition-scoped pattern as
  `listPlants` (no GSI needed; real due-date scheduling is Phase 7+ tracker work, out of scope
  here). `listActivity` reads the new `Event` entity (`data-architecture.md` §2, reserved since
  the original design but never implemented until now) — `_write_event` is a best-effort helper
  (own try/except at every call site) writing one at `createGoal` (`goal.submitted`),
  `approvePlan` (`plan.approved`), `postTaskCheckin` (`task.checkin`), and
  `app/orchestrator/orchestrator.py`'s `_write_plan_and_tasks` (`plan.updated`) — deliberately
  duplicated in both Lambda packages rather than shared, since they're separate deployable units
  (no cross-package Python imports in this monorepo). `Event` is intentionally UI-agnostic (no
  icon/tone/title baked in server-side) — the frontend maps `type` to presentation.

- **Phase A of the coconut-plantation epic (CP-01/CP-02, ADR-0032):** a Garden carries an optional
  structured `context` map (soil, irrigation, intercrop, layout, notes, `stakeRange`,
  `notificationRecipients`) plus `climateZone`, settable on `createGarden` and replaceable via the
  new `updateGarden` (`PATCH /gardens/{id}`). A Plant carries an optional `stakeNo` — the farm's
  own physical label, unique *within a garden* — enforced by a `GARDEN#{id}/STAKE#{no:05d}` lookup
  item written in the same transaction as the Plant (a duplicate → 409). `bulkCreatePlants`
  creates a stake-number range idempotently (existing numbers are skipped). A stake number outside
  the garden's `stakeRange` is accepted but flagged `outOfRange`. `listPlants` orders by stake
  number, follows `LastEvaluatedKey`, and resolves hero photos with one `BatchGetItem`.

No auth yet (ADR-0004's seam is still open): `X-User-Id` is a per-browser anonymous identifier
the frontend generates and persists (garden-onboarding.md's stories), not a verified identity.
This handler only requires the header be present.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import plant_history as ph
from boto3.dynamodb.conditions import Key

# force=True: the standard Lambda Python runtime pre-attaches its own handler to the root
# logger before user code runs, and basicConfig() is a documented no-op once handlers already
# exist — the same silent-logging bug confirmed live in app/orchestrator/orchestrator.py, which
# is the same plain-Lambda base image (ADR-0014). force=True replaces that handler instead of
# being ignored by it.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.api.garden_handler")

APP_TABLE_NAME = os.getenv("APP_TABLE_NAME")  # injected by ClientApiStack; never hardcoded
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")  # injected by ClientApiStack; never hardcoded

# Must match infra/stacks/agentcore_stack.py's GOAL_EVENT_SOURCE/GOAL_SUBMITTED_DETAIL_TYPE/
# GOAL_MESSAGE_RECEIVED_DETAIL_TYPE/TASK_CHECKIN_RECEIVED_DETAIL_TYPE — those are the
# source/detail-types its EventBridge rules pattern-match on.
GOAL_EVENT_SOURCE = "tendril.client-api"
GOAL_SUBMITTED_DETAIL_TYPE = "goal.submitted"
GOAL_MESSAGE_RECEIVED_DETAIL_TYPE = "goal.message.received"
TASK_CHECKIN_RECEIVED_DETAIL_TYPE = "task.checkin.received"

# Phase 7.5+: how far out approve_plan schedules a fresh follow-up for each pending task. Own
# copy, not shared with orchestrator.py's equivalent FOLLOWUP_OFFSET_DAYS constant (separate
# deployable units, no cross-package imports).
APPROVAL_FOLLOWUP_OFFSET_DAYS = 3

# Same free, keyless Open-Meteo endpoints app/tools/weather/handler.py already calls for
# specialists — duplicated here (not shared) since app/api and app/tools/weather are separate
# deployable units, no cross-package Python imports in this monorepo.
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
WEATHER_REQUEST_TIMEOUT_SECONDS = 8

# ADR-0032: Garden `context` block limits — keep the item small and the injected prompt short.
CONTEXT_TEXT_FIELDS = ("soil", "irrigation", "intercrop", "layout", "notes")
CONTEXT_TEXT_MAX_CHARS = 1000
CONTEXT_MAX_RECIPIENTS = 10
RECIPIENT_NAME_MAX_CHARS = 100
E164_PATTERN = re.compile(r"^\+[1-9]\d{6,14}$")
CLIMATE_ZONE_MAX_CHARS = 100
# ADR-0032: bulk-add cap per call (105 palms comfortably fits; bounded so one request can't run
# past API Gateway's 29s integration timeout).
BULK_PLANTS_MAX = 200
STAKE_NO_MAX = 99999  # matches the zero-padded 5-digit STAKE# sort key
# ADR-0033: originals go under `uploads/` (the Ingestion Lambda's trigger prefix), limited to 10 MB.
MEDIA_UPLOADS_PREFIX = "uploads/"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

_table = None  # lazy-initialized so import-time never requires AWS credentials/network
_client = None
_s3 = None
_events = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(APP_TABLE_NAME)
    return _table


def _get_client():
    # NOT the same as `_get_table().meta.client`: a resource's `.meta.client` has DynamoDB's
    # automatic Python<->AttributeValue transform injected (boto3.dynamodb.transform), which
    # re-serializes anything already AttributeValue-shaped — e.g. our pre-serialized
    # {"S": "..."} becomes {"M": {"S": {"S": "..."}}}, a real "Type mismatch...actual: M" bug
    # found via a live TransactWriteItems failure. A plain client has no such transform, so
    # raw AttributeValue dicts from _to_dynamo() pass through unchanged.
    global _client
    if _client is None:
        _client = boto3.client("dynamodb")
    return _client


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def _get_events():
    global _events
    if _events is None:
        _events = boto3.client("events")
    return _events


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        # Lambda proxy integration responses aren't auto-decorated with CORS headers by API
        # Gateway (only the openapi.yaml OPTIONS mock integration is) — the function's actual
        # response has to set Access-Control-Allow-Origin itself, or the browser blocks the
        # frontend (a different origin, ADR-0010) from reading it even on a 2xx response.
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body),
    }


def _error(status_code: int, message: str) -> dict[str, Any]:
    return _response(status_code, {"message": message})


def _get_header(headers: dict[str, str] | None, name: str) -> str | None:
    """API Gateway preserves the caller's header casing — look up case-insensitively."""
    if not headers:
        return None
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _parse_context(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validates an API-shaped (camelCase) Garden `context` block and returns its storage
    (snake_case) form, or an error message (ADR-0032). Every field is optional; an empty object
    is valid and stored as an empty map."""
    if not isinstance(raw, dict):
        return None, "context must be a JSON object"
    allowed = set(CONTEXT_TEXT_FIELDS) | {"stakeRange", "notificationRecipients"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        return None, f"context has unknown field(s): {', '.join(unknown)}"

    stored: dict[str, Any] = {}
    for field in CONTEXT_TEXT_FIELDS:
        value = raw.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None, f"context.{field} must be a string"
        if len(value) > CONTEXT_TEXT_MAX_CHARS:
            return None, f"context.{field} must be at most {CONTEXT_TEXT_MAX_CHARS} characters"
        if value.strip():
            stored[field] = value.strip()

    stake_range = raw.get("stakeRange")
    if stake_range is not None:
        if not isinstance(stake_range, dict):
            return None, "context.stakeRange must be an object with min and max"
        lo, hi = stake_range.get("min"), stake_range.get("max")
        if not (_is_positive_int(lo) and _is_positive_int(hi)) or lo > hi or hi > STAKE_NO_MAX:
            return None, (
                f"context.stakeRange needs integer min <= max, each between 1 and {STAKE_NO_MAX}"
            )
        stored["stake_range"] = {"min": lo, "max": hi}

    recipients = raw.get("notificationRecipients")
    if recipients is not None:
        if not isinstance(recipients, list):
            return None, "context.notificationRecipients must be an array"
        if len(recipients) > CONTEXT_MAX_RECIPIENTS:
            return None, (
                f"context.notificationRecipients allows at most {CONTEXT_MAX_RECIPIENTS} entries"
            )
        parsed = []
        for r in recipients:
            if not isinstance(r, dict):
                return None, "each notification recipient must be an object"
            name, e164 = r.get("name"), r.get("e164")
            if not isinstance(name, str) or not name.strip():
                return None, "each notification recipient needs a name"
            if len(name) > RECIPIENT_NAME_MAX_CHARS:
                return None, (
                    f"recipient name must be at most {RECIPIENT_NAME_MAX_CHARS} characters"
                )
            if not isinstance(e164, str) or not E164_PATTERN.match(e164):
                # Deliberately doesn't echo the number back — it's personal data.
                return None, "each notification recipient needs an E.164 number, e.g. +919800000000"
            parsed.append({"name": name.strip(), "e164": e164})
        stored["notification_recipients"] = parsed
    return stored, None


def _context_to_api(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Storage (snake_case, DynamoDB Decimal numbers) → API (camelCase, ints) for a Garden's
    `context` block."""
    stored = stored or {}
    out: dict[str, Any] = {f: stored[f] for f in CONTEXT_TEXT_FIELDS if stored.get(f)}
    if stored.get("stake_range"):
        out["stakeRange"] = {
            "min": int(stored["stake_range"]["min"]),
            "max": int(stored["stake_range"]["max"]),
        }
    if stored.get("notification_recipients"):
        out["notificationRecipients"] = [
            {"name": r["name"], "e164": r["e164"]} for r in stored["notification_recipients"]
        ]
    return out


def _parse_climate_zone(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, str):
        return None, "climateZone must be a string if provided"
    if len(raw) > CLIMATE_ZONE_MAX_CHARS:
        return None, f"climateZone must be at most {CLIMATE_ZONE_MAX_CHARS} characters"
    return raw.strip(), None


def _is_positive_int(value: Any) -> bool:
    # bool is an int subclass in Python — `true` must not pass as stake number 1.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _stake_sk(stake_no: int) -> str:
    """Zero-padded so STAKE# items sort numerically under the garden partition (ADR-0032)."""
    return f"STAKE#{stake_no:05d}"


def _is_transaction_condition_failure(error: Exception, index: int) -> bool:
    """True when a TransactWriteItems call was cancelled because the item at `index` failed its
    ConditionExpression — the lookup-item uniqueness check (ADR-0032)."""
    response = getattr(error, "response", None) or {}
    if response.get("Error", {}).get("Code") != "TransactionCanceledException":
        return False
    reasons = response.get("CancellationReasons") or []
    return index < len(reasons) and reasons[index].get("Code") == "ConditionalCheckFailed"


def _load_stake_range(garden_id: str) -> dict[str, int] | None:
    """The garden's optional `context.stakeRange` (ints), or None. Best-effort: a read failure
    just means the out-of-range flag can't be computed, never a failed plant write."""
    try:
        item = (
            _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"}).get("Item")
        )
    except Exception:
        logger.exception("Failed to read stake range for garden %s", garden_id)
        return None
    stake_range = ((item or {}).get("context") or {}).get("stake_range")
    if not stake_range:
        return None
    return {"min": int(stake_range["min"]), "max": int(stake_range["max"])}


def _out_of_range(stake_no: int, stake_range: dict[str, int] | None) -> bool:
    return bool(stake_range) and not (stake_range["min"] <= stake_no <= stake_range["max"])


def create_garden(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    name = payload.get("name")
    geolocation = payload.get("geolocation")
    vision = payload.get("vision")
    client_garden_id = payload.get("gardenId")
    media_id = payload.get("mediaId")
    if not isinstance(name, str) or not name.strip():
        return _error(400, "name is required")
    if not isinstance(geolocation, str) or not geolocation.strip():
        return _error(400, "geolocation is required")
    if vision is not None and not isinstance(vision, str):
        return _error(400, "vision must be a string if provided")
    if client_garden_id is not None and not isinstance(client_garden_id, str):
        return _error(400, "gardenId must be a string if provided")
    if media_id is not None and not isinstance(media_id, str):
        return _error(400, "mediaId must be a string if provided")
    context = None
    if payload.get("context") is not None:
        context, err = _parse_context(payload["context"])
        if err:
            return _error(400, err)
    climate_zone = None
    if payload.get("climateZone") is not None:
        climate_zone, err = _parse_climate_zone(payload["climateZone"])
        if err:
            return _error(400, err)

    # A garden photo (if any) has to be uploaded to `POST /gardens/{gardenId}/media` *before*
    # this call even happens (that endpoint needs an existing gardenId) — the frontend generates
    # one client-side up front for that case and passes it back here. No photo -> unchanged,
    # server generates the id exactly as before.
    garden_id = client_garden_id or str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    garden_item = {
        "pk": f"GARDEN#{garden_id}",
        "sk": "METADATA",
        "garden_id": garden_id,
        "name": name,
        "geolocation": geolocation,
        "owner_user_id": user_id,
        "created_at": created_at,
    }
    if vision:
        garden_item["vision"] = vision
    if media_id:
        garden_item["media_id"] = media_id
    if context is not None:
        garden_item["context"] = context
    if climate_zone:
        garden_item["climate_zone"] = climate_zone
    ownership_item = {
        "pk": f"USER#{user_id}",
        "sk": f"GARDEN#{garden_id}",
        "garden_id": garden_id,
        "name": name,
    }

    try:
        _get_client().transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": APP_TABLE_NAME,
                        "Item": _to_dynamo(garden_item),
                        # Only meaningful for a client-supplied id (a fresh uuid4 never
                        # collides) — guards against a colliding client-generated gardenId.
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {"Put": {"TableName": APP_TABLE_NAME, "Item": _to_dynamo(ownership_item)}},
            ]
        )
    except Exception as e:
        # ClientError.response includes CancellationReasons (with a Code + Message per
        # TransactItem) that logger.exception's plain traceback doesn't surface — critical
        # for diagnosing *which* item/attribute DynamoDB rejected and why.
        logger.error(
            "Failed to write garden %s for user %s: %s | response=%s",
            garden_id,
            user_id,
            e,
            getattr(e, "response", None),
        )
        return _error(500, "could not create garden")

    return _response(201, {"gardenId": garden_id})


def list_gardens(event: dict[str, Any]) -> dict[str, Any]:
    """Every garden the caller owns (multi-garden switcher) — one cheap Query on the
    `USER#{user_id}/GARDEN#{garden_id}` ownership index `create_garden` already writes, no
    GSI/scan needed (data-architecture.md §2's stated purpose for that index record)."""
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    try:
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq(f"USER#{user_id}")
            & Key("sk").begins_with("GARDEN#")
        )
    except Exception:
        logger.exception("Failed to list gardens for user %s", user_id)
        return _error(500, "could not list gardens")

    gardens = [
        {"gardenId": item["garden_id"], "name": item["name"]} for item in resp.get("Items", [])
    ]
    return _response(200, gardens)


def create_media_upload(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    content_type = payload.get("contentType")
    file_name = payload.get("fileName")
    size_bytes = payload.get("sizeBytes")
    if not isinstance(content_type, str) or not content_type.strip():
        return _error(400, "contentType is required")
    if not isinstance(file_name, str) or not file_name.strip():
        return _error(400, "fileName is required")
    # ADR-0033: uploads are limited to 10 MB. A presigned PUT can't enforce size, so the
    # declared size is checked here and the Ingestion Lambda / Image Tool are the backstop.
    if not _is_positive_int(size_bytes):
        return _error(400, "sizeBytes is required (the file size in bytes)")
    if size_bytes > MAX_UPLOAD_BYTES:
        return _error(400, "photo is larger than 10 MB")

    media_id = str(uuid.uuid4())
    # Only the base name: a '/' in the client's file name must not change the key layout that the
    # Ingestion Lambda parses (`uploads/{garden_id}/{media_id}/{file}`).
    safe_name = file_name.replace("\\", "/").rsplit("/", 1)[-1].strip() or "photo"
    s3_key = f"{MEDIA_UPLOADS_PREFIX}{garden_id}/{media_id}/{safe_name}"

    try:
        upload_url = _get_s3().generate_presigned_url(
            "put_object",
            Params={"Bucket": MEDIA_BUCKET_NAME, "Key": s3_key, "ContentType": content_type},
            ExpiresIn=900,
        )
        # Written now, not deferred to create_plant: this is the only step that has the
        # s3_key/content_type. create_plant links plant_id onto this same record later if the
        # upload is actually attached to a plant (data-architecture.md §2/§4).
        _get_table().put_item(
            Item={
                "pk": f"GARDEN#{garden_id}",
                "sk": f"MEDIA#{media_id}",
                "media_id": media_id,
                "garden_id": garden_id,
                "s3_key": s3_key,
                # As declared by the client — never trusted for analysis; the Ingestion Lambda
                # records the real `detected_format` (ADR-0033).
                "content_type": content_type,
                "size_bytes": size_bytes,
                "processing_status": "pending",
                "uploaded_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception:
        logger.exception("Failed to create media upload for garden %s", garden_id)
        return _error(500, "could not create upload URL")

    return _response(201, {"uploadUrl": upload_url, "mediaId": media_id})


class DuplicateStakeError(Exception):
    """The garden already has a plant with this stake number (ADR-0032)."""


STAGE_MAX_CHARS = 50


def _parse_stage(raw: Any) -> tuple[str, str | None]:
    if raw is None:
        return "new", None
    if not isinstance(raw, str) or not raw.strip():
        return "", "stage must be a non-empty string if provided"
    if len(raw) > STAGE_MAX_CHARS:
        return "", f"stage must be at most {STAGE_MAX_CHARS} characters"
    return raw.strip(), None


def _write_plant(
    garden_id: str,
    *,
    species: str,
    variety: str | None,
    stage: str,
    stake_no: int | None,
    media_id: str | None,
) -> str:
    """Writes one Plant and returns its new `plant_id`. Shared by `createPlant` and
    `bulkCreatePlants` so both enforce the same rules.

    - No stake number, no photo: a single `put_item` (unchanged from OB-02).
    - Otherwise one transaction: the Plant, plus a `STAKE#` lookup item guarded by
      `attribute_not_exists(pk)` (per-garden uniqueness, ADR-0032) and/or the Media link guarded
      by `attribute_exists(pk)` (OB-02). Raises `DuplicateStakeError` when the stake lookup's
      condition is what cancelled the transaction; any other failure propagates.
    """
    plant_id = str(uuid.uuid4())
    plant_item: dict[str, Any] = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"PLANT#{plant_id}",
        "plant_id": plant_id,
        "garden_id": garden_id,
        "species": species,
        "stage": stage,
    }
    if variety:
        plant_item["variety"] = variety
    if stake_no is not None:
        plant_item["stake_no"] = stake_no
    if media_id:
        # Stored on the Plant itself (not just Media.plant_id) so list_plants/this response can
        # look up the linked photo by direct key — mirrors Goal.media_ids' role for goals.
        plant_item["media_id"] = media_id

    if stake_no is None and not media_id:
        _get_table().put_item(Item=plant_item)
        return plant_id

    items: list[dict[str, Any]] = [
        {"Put": {"TableName": APP_TABLE_NAME, "Item": _to_dynamo(plant_item)}}
    ]
    stake_index = None
    if stake_no is not None:
        stake_index = len(items)
        items.append(
            {
                "Put": {
                    "TableName": APP_TABLE_NAME,
                    "Item": _to_dynamo(
                        {
                            "pk": f"GARDEN#{garden_id}",
                            "sk": _stake_sk(stake_no),
                            "stake_no": stake_no,
                            "plant_id": plant_id,
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(pk)",
                }
            }
        )
    if media_id:
        items.append(
            {
                "Update": {
                    "TableName": APP_TABLE_NAME,
                    "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}),
                    "UpdateExpression": "SET plant_id = :pid",
                    "ExpressionAttributeValues": {":pid": {"S": plant_id}},
                    "ConditionExpression": "attribute_exists(pk)",
                }
            }
        )
    try:
        _get_client().transact_write_items(TransactItems=items)
    except Exception as e:
        if stake_index is not None and _is_transaction_condition_failure(e, stake_index):
            raise DuplicateStakeError() from e
        raise
    return plant_id


def create_plant(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    species = payload.get("species")
    variety = payload.get("variety")
    media_id = payload.get("mediaId")
    if not isinstance(species, str) or not species.strip():
        return _error(400, "species is required")
    if variety is not None and not isinstance(variety, str):
        return _error(400, "variety must be a string if provided")
    if media_id is not None and not isinstance(media_id, str):
        return _error(400, "mediaId must be a string if provided")
    stage, err = _parse_stage(payload.get("stage"))
    if err:
        return _error(400, err)
    stake_no = payload.get("stakeNo")
    if stake_no is not None and not (_is_positive_int(stake_no) and stake_no <= STAKE_NO_MAX):
        return _error(400, f"stakeNo must be an integer between 1 and {STAKE_NO_MAX}")

    try:
        plant_id = _write_plant(
            garden_id,
            species=species,
            variety=variety,
            stage=stage,
            stake_no=stake_no,
            media_id=media_id,
        )
    except DuplicateStakeError:
        return _error(409, f"stake number {stake_no} is already used in this garden")
    except Exception as e:
        logger.error(
            "Failed to write plant for garden %s: %s | response=%s",
            garden_id,
            e,
            getattr(e, "response", None),
        )
        return _error(500, "could not create plant")

    result: dict[str, Any] = {"plantId": plant_id}
    if stake_no is not None:
        result["stakeNo"] = stake_no
        if _out_of_range(stake_no, _load_stake_range(garden_id)):
            result["outOfRange"] = True
    if media_id:
        media_item = (
            _get_table()
            .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"})
            .get("Item")
        )
        if media_item:
            result["photoUrl"] = _generate_download_url(_display_key(media_item, "thumb"))
        # ADR-0035: the plant's first photo opens its history.
        _append_photo_entries_best_effort(garden_id, plant_id, [media_id])
    return _response(201, result)


def list_plants(event: dict[str, Any]) -> dict[str, Any]:
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        items = _query_all(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("PLANT#")
        )
        s3_keys = _batch_get_media_keys(
            garden_id, [item["media_id"] for item in items if item.get("media_id")]
        )
    except Exception:
        logger.exception("Failed to list plants for garden %s", garden_id)
        return _error(500, "could not list plants")

    # ADR-0032: block/grid order — stake number ascending, unnumbered plants last (stable, so
    # unnumbered plants keep the query's own order among themselves).
    items.sort(key=lambda i: (i.get("stake_no") is None, int(i.get("stake_no") or 0)))

    return _response(
        200, [_plant_to_api(item, s3_keys.get(item.get("media_id") or "")) for item in items]
    )


def _plant_to_api(item: dict[str, Any], photo_key: str | None) -> dict[str, Any]:
    """The `Plant` schema, shared by listPlants and getPlant. Latest state is denormalized onto the
    Plant by every history write (ADR-0035 §4), so neither reads history to build it."""
    plant: dict[str, Any] = {
        "plantId": item["plant_id"],
        "species": item["species"],
        "variety": item.get("variety", ""),
        "stage": item.get("stage", "new"),
    }
    if item.get("stake_no") is not None:
        plant["stakeNo"] = int(item["stake_no"])
    if photo_key:
        plant["photoUrl"] = _generate_download_url(photo_key)
    if item.get("latest_status"):
        plant["latestStatus"] = item["latest_status"]
    if item.get("latest_observed_on"):
        plant["latestObservedOn"] = item["latest_observed_on"]
    if item.get("history_count") is not None:
        plant["historyCount"] = max(0, int(item["history_count"]))
    return plant


def _plant_path(event: dict[str, Any]) -> tuple[str, str] | None:
    p = event.get("pathParameters") or {}
    ids = (p.get("gardenId"), p.get("plantId"))
    return ids if all(ids) else None  # type: ignore[return-value]


def get_plant(event: dict[str, Any]) -> dict[str, Any]:
    """`GET /gardens/{gardenId}/plants/{plantId}` (CP-05, ADR-0035 §6): the plant plus one page of
    its dated history — one `Query` on its `PLANTLOG#` prefix, never a scan."""
    ids = _plant_path(event)
    if not ids:
        return _error(400, "gardenId and plantId are required")
    garden_id, plant_id = ids
    qs = event.get("queryStringParameters") or {}
    limit, err = ph.parse_limit(qs.get("limit"))
    if err:
        return _error(400, err)
    order = qs.get("order") or "newest"
    if order not in ("newest", "oldest"):
        return _error(400, "order must be newest or oldest")
    include_retracted = qs.get("includeRetracted") == "true"
    start_sk, err = ph.decode_cursor(qs.get("cursor"), plant_id)
    if err:
        return _error(400, err)

    table = _get_table()
    try:
        plant_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"}
        ).get("Item")
        if not plant_item:
            return _error(404, "plant not found")
        query: dict[str, Any] = {
            "KeyConditionExpression": Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(ph.prefix(plant_id)),
            "ScanIndexForward": order == "oldest",
            "Limit": limit,
        }
        if start_sk:
            query["ExclusiveStartKey"] = {"pk": f"GARDEN#{garden_id}", "sk": start_sk}
        page = table.query(**query)
        entries = ph.visible_page(page.get("Items", []), include_retracted)
        media_ids = [m for e in entries for m in e.get("media_ids") or []]
        if plant_item.get("media_id"):
            media_ids.append(plant_item["media_id"])
        media = _batch_get_media(garden_id, media_ids)
    except Exception:
        logger.exception("Failed to read plant %s for garden %s", plant_id, garden_id)
        return _error(500, "could not read plant")

    def photos(entry: dict[str, Any]) -> list[dict[str, str]]:
        out = []
        for mid in entry.get("media_ids") or []:
            item = media.get(mid)
            if item:
                out.append(
                    {
                        "mediaId": mid,
                        "thumbUrl": _generate_download_url(_display_key(item, "thumb")),
                        "url": _generate_download_url(_display_key(item)),
                    }
                )
        return out

    hero = media.get(plant_item.get("media_id") or "")
    history: dict[str, Any] = {"entries": [ph.entry_to_api(e, photos(e)) for e in entries]}
    next_cursor = ph.encode_cursor(page.get("LastEvaluatedKey"))
    if next_cursor:
        history["nextCursor"] = next_cursor
    return _response(
        200,
        {
            "plant": {
                "historyCount": 0,
                **_plant_to_api(plant_item, _display_key(hero, "thumb") if hero else None),
            },
            "history": history,
        },
    )


def update_plant(event: dict[str, Any]) -> dict[str, Any]:
    """`PATCH /gardens/{gardenId}/plants/{plantId}`: changes the plant's `stage` — e.g. `removed`
    for a palm that died, which keeps its history (deletePlant does not; ADR-0035 §8)."""
    ids = _plant_path(event)
    if not ids:
        return _error(400, "gardenId and plantId are required")
    garden_id, plant_id = ids
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict) or "stage" not in payload:
        return _error(400, "stage is required")
    stage, err = _parse_stage(payload.get("stage"))
    if err:
        return _error(400, err)
    try:
        item = (
            _get_table()
            .update_item(
                Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"},
                UpdateExpression="SET stage = :s",
                ExpressionAttributeValues={":s": stage},
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_NEW",
            )
            .get("Attributes")
        )
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return _error(404, "plant not found")
        logger.exception("Failed to update plant %s", plant_id)
        return _error(500, "could not update plant")
    hero = _get_media(garden_id, item["media_id"]) if item and item.get("media_id") else None
    return _response(200, _plant_to_api(item or {}, _display_key(hero, "thumb") if hero else None))


def post_plant_history(event: dict[str, Any]) -> dict[str, Any]:
    """`POST /gardens/{gardenId}/plants/{plantId}/history` (CP-03, ADR-0035 §2): the gardener logs a
    note and/or status, optionally with photos, dated `observedOn` (default: the photo's own date,
    else today). Append-only; photos must be this garden's and not filed under another plant."""
    ids = _plant_path(event)
    if not ids:
        return _error(400, "gardenId and plantId are required")
    garden_id, plant_id = ids
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")
    note = payload.get("note")
    status = payload.get("status")
    media_ids = payload.get("mediaIds") or []
    if note is not None and (not isinstance(note, str) or len(note) > ph.NOTE_MAX_CHARS):
        return _error(400, f"note must be text of at most {ph.NOTE_MAX_CHARS} characters")
    note = (note or "").strip() or None
    if status is not None and status not in ph.STATUSES:
        return _error(400, "status must be one of: " + ", ".join(ph.STATUSES))
    if (
        not isinstance(media_ids, list)
        or len(media_ids) > ph.MAX_MEDIA_PER_ENTRY
        or not all(isinstance(m, str) and m for m in media_ids)
    ):
        return _error(400, f"mediaIds must be at most {ph.MAX_MEDIA_PER_ENTRY} ids")
    media_ids = list(dict.fromkeys(media_ids))
    if not (note or status or media_ids):
        return _error(400, "add a note, a status or a photo")
    observed_on, err = ph.parse_observed_on(payload.get("observedOn"))
    if err:
        return _error(400, err)

    try:
        media_items = [_get_media(garden_id, m) for m in media_ids]
    except Exception:
        logger.exception("Failed to read media for plant %s history", plant_id)
        return _error(500, "could not save")
    for mid, item in zip(media_ids, media_items, strict=True):
        if not item:
            return _error(400, f"unknown mediaId {mid}")
        if item.get("plant_id") and item["plant_id"] != plant_id:
            return _error(409, "a photo is filed under another plant — move it from its goal chat")
    first = media_items[0] if media_items else None
    entry = ph.new_entry(
        garden_id,
        plant_id,
        kind="log",
        source="user",
        observed_on=observed_on or (ph.observed_on_for_media(first) if first else ph.today()),
        observed_at=None if observed_on else ph.observed_at_for_media(first),
        media_ids=media_ids,
        status=status,
        note=note,
    )
    links = [
        _media_link_item(garden_id, mid, {"plant_id": plant_id, "plant_link_source": "user"})
        for mid in media_ids
    ]
    try:
        written = _append_entry(
            garden_id, entry, media_id=media_ids[0] if media_ids else None, extra_items=links
        )
    except Exception:
        logger.exception("Failed to append history for plant %s", plant_id)
        return _error(500, "could not save")
    if not written:
        return _error(404, "plant not found")
    photos = [
        {
            "mediaId": mid,
            "thumbUrl": _generate_download_url(_display_key(item, "thumb")),
            "url": _generate_download_url(_display_key(item)),
        }
        for mid, item in zip(media_ids, media_items, strict=True)
        if item
    ]
    return _response(201, ph.entry_to_api(entry, photos))


def _query_all(**kwargs: Any) -> list[dict[str, Any]]:
    """`Table.query` following `LastEvaluatedKey` to the end, so a 1 MB page boundary never
    silently truncates the result (ADR-0032, plantation scale)."""
    items: list[dict[str, Any]] = []
    while True:
        resp = _get_table().query(**kwargs)
        items.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            return items
        kwargs["ExclusiveStartKey"] = last_key


BATCH_GET_MAX_KEYS = 100  # DynamoDB BatchGetItem's per-request limit
BATCH_GET_MAX_ATTEMPTS = 5


def _batch_get_media_keys(garden_id: str, media_ids: list[str]) -> dict[str, str]:
    """media_id -> display key (thumbnail when processed, else the original) — plant cards show
    the thumbnail once ingestion made one (ADR-0033)."""
    return {
        mid: _display_key(item, "thumb")
        for mid, item in _batch_get_media(garden_id, media_ids).items()
    }


def _batch_get_media(garden_id: str, media_ids: list[str]) -> dict[str, dict[str, Any]]:
    """media_id -> Media item (display fields only) for the given ids, via `BatchGetItem` in chunks
    of 100 instead of one `GetItem` each (ADR-0032). Retries `UnprocessedKeys` a bounded number of
    times; a Media record that's genuinely missing is simply absent from the result."""
    from boto3.dynamodb.types import TypeDeserializer

    deserializer = TypeDeserializer()
    result: dict[str, dict[str, Any]] = {}
    unique_ids = list(dict.fromkeys(media_ids))
    for start in range(0, len(unique_ids), BATCH_GET_MAX_KEYS):
        chunk = unique_ids[start : start + BATCH_GET_MAX_KEYS]
        request: dict[str, Any] = {
            APP_TABLE_NAME: {
                "Keys": [
                    _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{mid}"}) for mid in chunk
                ],
                "ProjectionExpression": (
                    "media_id, s3_key, processing_status, thumb_s3_key, analysis_s3_key"
                ),
            }
        }
        for _ in range(BATCH_GET_MAX_ATTEMPTS):
            resp = _get_client().batch_get_item(RequestItems=request)
            for raw in resp.get("Responses", {}).get(APP_TABLE_NAME, []):
                item = {k: deserializer.deserialize(v) for k, v in raw.items()}
                if item.get("media_id") and item.get("s3_key"):
                    result[item["media_id"]] = item
            request = resp.get("UnprocessedKeys") or {}
            if not request:
                break
        else:
            logger.warning(
                "batch_get_media_unprocessed garden_id=%s remaining=%d",
                garden_id,
                len(request.get(APP_TABLE_NAME, {}).get("Keys", [])),
            )
    return result


def bulk_create_plants(event: dict[str, Any]) -> dict[str, Any]:
    """`POST /gardens/{gardenId}/plants/bulk` (CP-02, ADR-0032): creates one Plant per stake
    number in `[stakeFrom, stakeTo]`, each through the same transactional write as `createPlant`.
    Idempotent — a stake number already used in this garden lands in `skipped`, never duplicated
    or overwritten — so a partial run can simply be retried."""
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    species = payload.get("species")
    variety = payload.get("variety")
    if not isinstance(species, str) or not species.strip():
        return _error(400, "species is required")
    if variety is not None and not isinstance(variety, str):
        return _error(400, "variety must be a string if provided")
    stage, err = _parse_stage(payload.get("stage"))
    if err:
        return _error(400, err)
    stake_from, stake_to = payload.get("stakeFrom"), payload.get("stakeTo")
    if not (_is_positive_int(stake_from) and _is_positive_int(stake_to)):
        return _error(400, "stakeFrom and stakeTo must be positive integers")
    if stake_from > stake_to or stake_to > STAKE_NO_MAX:
        return _error(400, f"stakeFrom must be <= stakeTo, and stakeTo <= {STAKE_NO_MAX}")
    if stake_to - stake_from + 1 > BULK_PLANTS_MAX:
        return _error(400, f"at most {BULK_PLANTS_MAX} plants per call")

    try:
        garden = (
            _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"}).get("Item")
        )
    except Exception:
        logger.exception("Failed to read garden %s for bulk plant create", garden_id)
        return _error(500, "could not read garden")
    if not garden:
        return _error(404, "garden not found")
    stake_range = (garden.get("context") or {}).get("stake_range")
    stake_range = (
        {"min": int(stake_range["min"]), "max": int(stake_range["max"])} if stake_range else None
    )

    created: list[dict[str, Any]] = []
    skipped: list[int] = []
    for stake_no in range(stake_from, stake_to + 1):
        try:
            plant_id = _write_plant(
                garden_id,
                species=species,
                variety=variety,
                stage=stage,
                stake_no=stake_no,
                media_id=None,
            )
        except DuplicateStakeError:
            skipped.append(stake_no)
            continue
        except Exception as e:
            logger.error(
                "bulk_plant_write_failed garden_id=%s stake_no=%d created=%d: %s | response=%s",
                garden_id,
                stake_no,
                len(created),
                e,
                getattr(e, "response", None),
            )
            # Everything already created stays (idempotent retry picks up where this stopped).
            return _error(500, f"could not create plant #{stake_no}; retry to resume")
        entry: dict[str, Any] = {"plantId": plant_id, "stakeNo": stake_no}
        if _out_of_range(stake_no, stake_range):
            entry["outOfRange"] = True
        created.append(entry)

    return _response(201, {"created": created, "skipped": skipped})


def delete_plant(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    path_params = event.get("pathParameters") or {}
    garden_id = path_params.get("gardenId")
    plant_id = path_params.get("plantId")
    if not garden_id or not plant_id:
        return _error(400, "gardenId and plantId are required")

    plant_key = {"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"}
    try:
        # Deliberately does not cascade-delete the Plant's linked Media/S3 object — see the
        # module docstring's note on why. A numbered plant's STAKE# lookup item (ADR-0032) IS
        # removed, in the same transaction, so its stake number becomes reusable.
        plant = _get_table().get_item(Key=plant_key).get("Item")
        stake_no = (plant or {}).get("stake_no")
        if stake_no is None:
            _get_table().delete_item(Key=plant_key)
        else:
            _get_client().transact_write_items(
                TransactItems=[
                    {"Delete": {"TableName": APP_TABLE_NAME, "Key": _to_dynamo(plant_key)}},
                    {
                        "Delete": {
                            "TableName": APP_TABLE_NAME,
                            "Key": _to_dynamo(
                                {"pk": f"GARDEN#{garden_id}", "sk": _stake_sk(int(stake_no))}
                            ),
                            # Only remove the lookup if it still points at THIS plant.
                            "ConditionExpression": "attribute_not_exists(pk) OR plant_id = :pid",
                            "ExpressionAttributeValues": {":pid": {"S": plant_id}},
                        }
                    },
                ]
            )
    except Exception:
        logger.exception("Failed to delete plant %s for garden %s", plant_id, garden_id)
        return _error(500, "could not delete plant")

    # ADR-0035 §8: deleting means "this plant should not exist", so its history goes too (a palm
    # that died is marked `stage: removed` instead, which keeps it). Photos stay — they're
    # garden-level Media. Best-effort after the plant is gone; a retry of DELETE finishes it.
    try:
        entries = _query_all(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(ph.prefix(plant_id)),
            ProjectionExpression="pk, sk",
        )
        with _get_table().batch_writer() as batch:
            for entry in entries:
                batch.delete_item(Key={"pk": entry["pk"], "sk": entry["sk"]})
    except Exception:
        logger.exception("Failed to delete history of plant %s", plant_id)

    return {
        "statusCode": 204,
        "headers": {"Access-Control-Allow-Origin": "*"},
        "body": "",
    }


def create_goal(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    description = payload.get("description")
    media_ids = payload.get("mediaIds")
    plant_id = payload.get("plantId")
    if not isinstance(description, str) or not description.strip():
        return _error(400, "description is required")
    if media_ids is not None and (
        not isinstance(media_ids, list) or not all(isinstance(m, str) for m in media_ids)
    ):
        return _error(400, "mediaIds must be an array of strings if provided")
    if plant_id is not None and not isinstance(plant_id, str):
        return _error(400, "plantId must be a string if provided")

    goal_id = str(uuid.uuid4())
    goal_item: dict[str, Any] = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"GOAL#{goal_id}",
        "goal_id": goal_id,
        "garden_id": garden_id,
        "description": description,
        # Generic default — the orchestrator sets this once it actually understands the goal
        # (openapi.yaml's Goal.type description).
        "type": "diagnosis",
        "status": "Intake",
    }
    if media_ids:
        goal_item["media_ids"] = media_ids
    if plant_id:
        goal_item["plant_id"] = plant_id

    try:
        if media_ids:
            # Every attached photo learns which goal it's for (and which plant, if the goal has
            # one) in the same transaction that creates the goal — mirrors create_plant's
            # existing Put-goal-plus-Update-media pattern exactly.
            media_updates = []
            for media_id in media_ids:
                update_expr = "SET goal_id = :gid"
                values = {":gid": {"S": goal_id}}
                if plant_id:
                    update_expr += ", plant_id = :pid"
                    values[":pid"] = {"S": plant_id}
                media_updates.append(
                    {
                        "Update": {
                            "TableName": APP_TABLE_NAME,
                            "Key": _to_dynamo(
                                {"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}
                            ),
                            "UpdateExpression": update_expr,
                            "ExpressionAttributeValues": values,
                            "ConditionExpression": "attribute_exists(pk)",
                        }
                    }
                )
            _get_client().transact_write_items(
                TransactItems=[
                    {"Put": {"TableName": APP_TABLE_NAME, "Item": _to_dynamo(goal_item)}},
                    *media_updates,
                ]
            )
        else:
            _get_table().put_item(Item=goal_item)
        _get_events().put_events(
            Entries=[
                {
                    "Source": GOAL_EVENT_SOURCE,
                    "DetailType": GOAL_SUBMITTED_DETAIL_TYPE,
                    "Detail": json.dumps({"gardenId": garden_id, "goalId": goal_id}),
                }
            ]
        )
    except Exception:
        logger.exception("Failed to submit goal for garden %s", garden_id)
        return _error(500, "could not submit goal")

    try:
        _write_event(garden_id, "goal.submitted", {"goalId": goal_id, "description": description})
    except Exception:
        logger.exception("Failed to write goal.submitted event for goal %s", goal_id)
    if plant_id and media_ids:
        # ADR-0035 §2: photos the gardener filed under a plant join its history (id = media id,
        # so the orchestrator's identity "match" on the same photo is a no-op).
        _append_photo_entries_best_effort(garden_id, plant_id, media_ids, goal_id=goal_id)

    return _response(202, {"goalId": goal_id, "status": "Intake"})


def _display_key(media_item: dict[str, Any], variant: str = "analysis") -> str:
    """The S3 key to show for a Media record (ADR-0033): its processed `thumb` or `analysis` copy
    once ingestion has made it — smaller, correctly oriented, without GPS metadata — else the
    original (photos uploaded before ingestion existed, or still processing)."""
    if media_item.get("processing_status") == "ready":
        derived = media_item.get(f"{variant}_s3_key") or media_item.get("analysis_s3_key")
        if derived:
            return derived
    return media_item["s3_key"]


def _generate_download_url(s3_key: str) -> str:
    """Short-lived presigned GET url for a Media record's s3_key — never stored/reused, same
    posture as the upload URL. Shared by getGoalDetail (PA-03) and OB-03's plant-photo AC rather
    than duplicated."""
    return _get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": MEDIA_BUCKET_NAME, "Key": s3_key},
        ExpiresIn=900,
    )


def _goal_from_item(item: dict[str, Any]) -> dict[str, Any]:
    goal = {
        "goalId": item["goal_id"],
        "description": item["description"],
        "type": item.get("type", "diagnosis"),
        "status": item.get("status", "Intake"),
    }
    if item.get("media_ids"):
        goal["mediaIds"] = item["media_ids"]
    if item.get("plant_id"):
        goal["plantId"] = item["plant_id"]
    return goal


def list_goals(event: dict[str, Any]) -> dict[str, Any]:
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("GOAL#")
        )
    except Exception:
        logger.exception("Failed to list goals for garden %s", garden_id)
        return _error(500, "could not list goals")

    return _response(200, [_goal_from_item(item) for item in resp.get("Items", [])])


def _task_from_item(table, garden_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """Shapes one Task item for the API response — shared by `getGoalDetail` (goal-scoped) and
    `listTasks` (cross-goal, Phase 6) so the media/feedback resolution logic isn't duplicated."""
    task: dict[str, Any] = {
        "taskId": item["task_id"],
        "goalId": item["goal_id"],
        "title": item["title"],
        "detail": item["detail"],
        "scope": item.get("scope", "plant"),
        "status": item.get("status", "pending"),
    }
    if item.get("plant_id"):
        task["plantId"] = item["plant_id"]
    if item.get("media_id"):
        task_media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{item['media_id']}"}
        ).get("Item")
        if task_media_item:
            task["media"] = {
                "mediaId": item["media_id"],
                "downloadUrl": _generate_download_url(_display_key(task_media_item)),
            }
    if item.get("feedback"):
        task["feedback"] = item["feedback"]
    return task


def list_tasks(event: dict[str, Any]) -> dict[str, Any]:
    """Every task across every goal in a garden (Phase 6) — one cheap Query on the shared
    `TASK#*` sk prefix under the garden's partition, no GSI needed for this read-only,
    ungrouped-by-date list (real due-date scheduling is Phase 7+ tracker work)."""
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    table = _get_table()
    try:
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("TASK#")
        )
    except Exception:
        logger.exception("Failed to list tasks for garden %s", garden_id)
        return _error(500, "could not list tasks")

    tasks = [_task_from_item(table, garden_id, item) for item in resp.get("Items", [])]
    return _response(200, tasks)


def list_activity(event: dict[str, Any]) -> dict[str, Any]:
    """The garden's event timeline (Phase 6) — newest first. `Event` (data-architecture.md §2)
    is intentionally UI-agnostic: no icon/tone/title baked in server-side, same posture as
    OB-03/PA-05's specialist-icon choice living in the frontend, not the API."""
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("EVENT#"),
            ScanIndexForward=False,
        )
    except Exception:
        logger.exception("Failed to list activity for garden %s", garden_id)
        return _error(500, "could not list activity")

    events = []
    for item in resp.get("Items", []):
        entry: dict[str, Any] = {
            "type": item["type"],
            "payload": item.get("payload", {}),
            "createdAt": item["created_at"],
        }
        if item.get("payload", {}).get("goalId"):
            entry["goalId"] = item["payload"]["goalId"]
        events.append(entry)
    return _response(200, events)


def _write_event(garden_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """Best-effort — an Event is an audit-log entry for the Activity screen (Phase 6), never
    something a request's success should depend on. Callers wrap this in their own try/except
    (or accept that a failure here is swallowed) so a logging hiccup never turns an otherwise
    successful write into a 500."""
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


def _message_from_item(table, garden_id: str, m: dict[str, Any]) -> dict[str, Any]:
    """API shape of a chat Message, with its photos (thumbnails), check-in task and the proposed
    action it asks the gardener to confirm, when present (ADR-0034)."""
    out: dict[str, Any] = {"role": m["role"], "content": m["content"], "createdAt": m["created_at"]}
    if m.get("message_id"):
        out["messageId"] = m["message_id"]
    if m.get("task_id"):
        out["taskId"] = m["task_id"]
    if m.get("action_id"):
        out["actionId"] = m["action_id"]
    photos = []
    for media_id in m.get("media_ids") or []:
        media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}
        ).get("Item")
        if media_item:
            photos.append(
                {
                    "mediaId": media_id,
                    "downloadUrl": _generate_download_url(_display_key(media_item, "thumb")),
                }
            )
    if photos:
        out["media"] = photos
    return out


def _load_actions(table, garden_id: str, goal_id: str) -> list[dict[str, Any]]:
    """Every proposal on this goal (ADR-0034), oldest first. The frontend shows Confirm/Change on
    the `proposed` ones; the rest are history."""
    resp = table.query(
        KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
        & Key("sk").begins_with(f"ACTION#{goal_id}#")
    )
    actions = []
    for a in sorted(resp.get("Items", []), key=lambda i: i.get("created_at", "")):
        if not str(a.get("sk", "")).startswith(f"ACTION#{goal_id}#"):
            continue  # defensive: only real Action items
        params = a.get("params") or {}
        item: dict[str, Any] = {
            "actionId": a["action_id"],
            "type": a["type"],
            "summary": a.get("summary", ""),
            "status": a["status"],
            "createdAt": a.get("created_at", ""),
        }
        if params.get("task_id"):
            item["taskId"] = params["task_id"]
        if params.get("candidates"):
            item["candidates"] = [int(c["stake_no"]) for c in params["candidates"]]
        actions.append(item)
    return actions


def get_goal_detail(event: dict[str, Any]) -> dict[str, Any]:
    path_params = event.get("pathParameters") or {}
    garden_id = path_params.get("gardenId")
    goal_id = path_params.get("goalId")
    if not garden_id or not goal_id:
        return _error(400, "gardenId and goalId are required")

    table = _get_table()
    try:
        goal_item = table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"}).get(
            "Item"
        )
    except Exception:
        logger.exception("Failed to read goal %s for garden %s", goal_id, garden_id)
        return _error(500, "could not read goal")
    if not goal_item:
        return _error(404, "goal not found")

    try:
        plan_item = table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLAN#{goal_id}"}).get(
            "Item"
        )
        tasks_resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(f"TASK#{goal_id}#")
        )
        messages_resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(f"GOALMSG#{goal_id}#")
        )
    except Exception:
        logger.exception("Failed to read plan/tasks/messages for goal %s", goal_id)
        return _error(500, "could not read goal detail")

    tasks = [_task_from_item(table, garden_id, t) for t in tasks_resp.get("Items", [])]

    # sk (GOALMSG#{goalId}#{iso_timestamp}#{messageId}) sorts chronologically already —
    # explicit sort here just guards against a fake/non-ordering table in tests.
    messages = [
        _message_from_item(table, garden_id, m)
        for m in sorted(messages_resp.get("Items", []), key=lambda m: m["sk"])
    ]
    try:
        actions = _load_actions(table, garden_id, goal_id)
    except Exception:
        logger.exception("Failed to read actions for goal %s", goal_id)
        actions = []  # proposals are an add-on; never fail the whole goal view over them

    media = []
    for media_id in goal_item.get("media_ids") or []:
        media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}
        ).get("Item")
        if media_item:
            media.append(
                {
                    "mediaId": media_id,
                    "downloadUrl": _generate_download_url(_display_key(media_item)),
                }
            )

    goal = _goal_from_item(goal_item)
    if goal_item.get("plant_id"):
        # The plant's stake number for the goal header ("#13"), so a confirmed link is visible.
        try:
            plant_item = table.get_item(
                Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{goal_item['plant_id']}"}
            ).get("Item")
        except Exception:
            logger.exception("Failed to read plant for goal %s", goal_id)
            plant_item = None
        if plant_item and plant_item.get("stake_no") is not None:
            goal["plantStakeNo"] = int(plant_item["stake_no"])

    detail: dict[str, Any] = {
        "goal": goal,
        "tasks": tasks,
        "media": media,
        "messages": messages,
        "actions": actions,
    }
    if plan_item:
        detail["plan"] = {
            "planId": plan_item["plan_id"],
            "goalId": plan_item["goal_id"],
            "successCriteria": plan_item["success_criteria"],
            "status": plan_item["status"],
        }
        if plan_item.get("trace"):
            detail["plan"]["trace"] = [
                {
                    "agent": e["agent"],
                    "says": e["says"],
                    # DynamoDB's resource-layer Table deserializes Number attributes as
                    # decimal.Decimal, not int — json.dumps can't serialize that (a real bug
                    # found live: get_goal_detail 500'd the moment a plan actually had a trace).
                    "ms": int(e["ms"]),
                    "isOrchestrator": e.get("is_orchestrator", False),
                }
                for e in plan_item["trace"]
            ]
    return _response(200, detail)


def post_goal_message(event: dict[str, Any]) -> dict[str, Any]:
    path_params = event.get("pathParameters") or {}
    garden_id = path_params.get("gardenId")
    goal_id = path_params.get("goalId")
    if not garden_id or not goal_id:
        return _error(400, "gardenId and goalId are required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    # ADR-0034 §3: a message may carry photos and/or a task (a check-in). Text is optional then.
    content = payload.get("content", "")
    media_ids = payload.get("mediaIds") or []
    task_id = payload.get("taskId")
    if not isinstance(content, str):
        return _error(400, "content must be a string")
    if not isinstance(media_ids, list) or not all(
        isinstance(m, str) and m.strip() for m in media_ids
    ):
        return _error(400, "mediaIds must be an array of ids")
    if len(media_ids) > MAX_MESSAGE_MEDIA:
        return _error(400, f"at most {MAX_MESSAGE_MEDIA} photos per message")
    if task_id is not None and (not isinstance(task_id, str) or not task_id.strip()):
        return _error(400, "taskId must be a string if provided")
    if not content.strip() and not media_ids and not task_id:
        return _error(400, "content is required (or attach a photo or pick a task)")

    table = _get_table()
    try:
        for media_id in media_ids:
            if not table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}).get(
                "Item"
            ):
                return _error(404, "media not found")
        if task_id:
            task = table.get_item(
                Key={"pk": f"GARDEN#{garden_id}", "sk": f"TASK#{goal_id}#{task_id}"}
            ).get("Item")
            if not task:
                return _error(404, "task not found")
            if task.get("status") != "pending":
                return _error(409, "that task is already done")
    except Exception:
        logger.exception("Failed to validate message for goal %s", goal_id)
        return _error(500, "could not send message")

    try:
        message_id = _create_user_message(garden_id, goal_id, content, media_ids, task_id)
    except Exception:
        logger.exception("Failed to send message for goal %s", goal_id)
        return _error(500, "could not send message")

    return _response(202, {"messageId": message_id})


MAX_MESSAGE_MEDIA = 4


def _create_user_message(
    garden_id: str,
    goal_id: str,
    content: str,
    media_ids: list[str],
    task_id: str | None,
) -> str:
    """Writes a user chat Message (optionally with photos and a check-in task, ADR-0034) and
    publishes `goal.message.received`; the orchestrator reloads it from the table."""
    message_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    item: dict[str, Any] = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"GOALMSG#{goal_id}#{created_at}#{message_id}",
        "message_id": message_id,
        "goal_id": goal_id,
        "role": "user",
        "content": content,
        "created_at": created_at,
    }
    if media_ids:
        item["media_ids"] = media_ids
    if task_id:
        item["task_id"] = task_id
    _get_table().put_item(Item=item)
    for media_id in media_ids:
        try:  # traceability: which goal/message a chat photo belongs to (best-effort)
            _get_table().update_item(
                Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"},
                UpdateExpression="SET goal_id = :g, message_id = :m",
                ExpressionAttributeValues={":g": goal_id, ":m": message_id},
                ConditionExpression="attribute_exists(pk)",
            )
        except Exception:
            logger.exception("Failed to link media %s to message %s", media_id, message_id)
    _get_events().put_events(
        Entries=[
            {
                "Source": GOAL_EVENT_SOURCE,
                "DetailType": GOAL_MESSAGE_RECEIVED_DETAIL_TYPE,
                "Detail": json.dumps({"gardenId": garden_id, "goalId": goal_id}),
            }
        ]
    )
    return message_id


def approve_plan(event: dict[str, Any]) -> dict[str, Any]:
    path_params = event.get("pathParameters") or {}
    garden_id = path_params.get("gardenId")
    plan_id = path_params.get("planId")
    if not garden_id or not plan_id:
        return _error(400, "gardenId and planId are required")

    table = _get_table()
    try:
        # plan_id == goal_id (Plan is always 1:1 with its Goal, data-architecture.md §2) — no
        # separate lookup needed to find the Goal record this Plan belongs to.
        plan_item = table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLAN#{plan_id}"}).get(
            "Item"
        )
    except Exception:
        logger.exception("Failed to read plan %s for garden %s", plan_id, garden_id)
        return _error(500, "could not read plan")
    if not plan_item:
        return _error(404, "plan not found")

    try:
        table.update_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLAN#{plan_id}"},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "Approved"},
        )
        table.update_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{plan_id}"},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "Approved"},
        )
    except Exception:
        logger.exception("Failed to approve plan %s for garden %s", plan_id, garden_id)
        return _error(500, "could not approve plan")

    try:
        # plan_id == goal_id (data-architecture.md §2) — reused as-is for the event's goalId.
        _write_event(garden_id, "plan.approved", {"goalId": plan_id, "planId": plan_id})
    except Exception:
        logger.exception("Failed to write plan.approved event for plan %s", plan_id)

    # Phase 7.5+: stamp a follow-up due-date onto every still-pending task for this goal — the
    # tracker/scheduler (app/tracker/) polls TasksDueIndex for exactly these. Best-effort: a
    # stamping failure must never turn a successful approval into a 500; the approval itself
    # (Plan/Goal -> Approved, above) is the correctness-critical part.
    #
    # Plan-revision self-correction: a later chat/check-in turn producing an updated_plan causes
    # _write_plan_and_tasks (orchestrator.py) to delete all non-done tasks and create fresh ones
    # with no due-date, flipping Goal.status back to PlanProposed — which forces re-approval
    # through this SAME endpoint before those new tasks can proceed. Since this queries the
    # CURRENT task set at call time (not a cached snapshot from first approval), calling
    # approve_plan again after a revision naturally re-stamps whatever pending tasks exist then —
    # no special-casing needed for "revision after approval."
    try:
        tasks_resp = table.query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(f"TASK#{plan_id}#")
        )
        due_date = (datetime.now(UTC) + timedelta(days=APPROVAL_FOLLOWUP_OFFSET_DAYS)).isoformat()
        for task_item in tasks_resp.get("Items", []):
            if task_item.get("status") != "pending":
                continue
            table.update_item(
                Key={"pk": task_item["pk"], "sk": task_item["sk"]},
                UpdateExpression="SET due_date = :d, gsi1pk = :p, gsi1sk = :d",
                ExpressionAttributeValues={":d": due_date, ":p": "TASK_STATUS#pending"},
            )
    except Exception:
        logger.exception("Failed to stamp task due-dates for plan %s", plan_id)

    return _response(200, {"planId": plan_id, "status": "Approved"})


def post_task_checkin(event: dict[str, Any]) -> dict[str, Any]:
    """The per-task check-in button (ADR-0034 §3). No longer marks the task done on its own: it
    posts a check-in chat message (photo + task) so the orchestrator can check the photo's plant
    identity and *propose* completing the task; only `confirmAction` then marks it done. Returns
    202 with `pending_confirmation`."""
    path_params = event.get("pathParameters") or {}
    garden_id = path_params.get("gardenId")
    goal_id = path_params.get("goalId")
    task_id = path_params.get("taskId")
    if not garden_id or not goal_id or not task_id:
        return _error(400, "gardenId, goalId, and taskId are required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    media_id = payload.get("mediaId")
    if not isinstance(media_id, str) or not media_id.strip():
        return _error(400, "mediaId is required")
    note = payload.get("note", "")
    if not isinstance(note, str):
        return _error(400, "note must be a string if provided")

    table = _get_table()
    task_sk = f"TASK#{goal_id}#{task_id}"
    try:
        task_item = table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": task_sk}).get("Item")
        media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}
        ).get("Item")
    except Exception:
        logger.exception(
            "Failed to read task %s or media %s for garden %s", task_id, media_id, garden_id
        )
        return _error(500, "could not check in task")
    if not task_item:
        return _error(404, "task not found")
    if not media_item:
        return _error(404, "media not found")
    if task_item.get("status") != "pending":
        return _error(409, "that task is already done")

    try:
        message_id = _create_user_message(
            garden_id,
            goal_id,
            note.strip() or f"Check-in: {task_item.get('title', '')}",
            [media_id],
            task_id,
        )
    except Exception:
        logger.exception("Failed to check in task %s for garden %s", task_id, garden_id)
        return _error(500, "could not check in task")

    return _response(
        202, {"taskId": task_id, "status": "pending_confirmation", "messageId": message_id}
    )


class ActionNotApplicable(Exception):
    """The proposed change can no longer be applied (task already done, plant gone, ...)."""


def _action_params(garden_id: str, goal_id: str, action_id: str) -> tuple[dict, dict] | None:
    item = (
        _get_table()
        .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"ACTION#{goal_id}#{action_id}"})
        .get("Item")
    )
    return (item, item.get("params") or {}) if item else None


def _confirm_action_item(garden_id: str, goal_id: str, action_id: str) -> dict[str, Any]:
    """The action's own status flip, conditional on it still being `proposed` — included in every
    confirm transaction so a change is applied at most once."""
    return {
        "Update": {
            "TableName": APP_TABLE_NAME,
            "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"ACTION#{goal_id}#{action_id}"}),
            "UpdateExpression": "SET #s = :confirmed, resolved_at = :now",
            "ExpressionAttributeNames": {"#s": "status"},
            "ExpressionAttributeValues": _to_dynamo(
                {
                    ":confirmed": "confirmed",
                    ":proposed": "proposed",
                    ":now": datetime.now(UTC).isoformat(),
                }
            ),
            "ConditionExpression": "#s = :proposed",
        }
    }


def _media_link_item(garden_id: str, media_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    names = {f"#{k}": k for k in fields}
    return {
        "Update": {
            "TableName": APP_TABLE_NAME,
            "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}),
            "UpdateExpression": "SET " + ", ".join(f"#{k} = :{k}" for k in fields),
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": _to_dynamo({f":{k}": v for k, v in fields.items()}),
            "ConditionExpression": "attribute_exists(pk)",
        }
    }


def _plant_exists_check(garden_id: str, plant_id: str) -> dict[str, Any]:
    return {
        "ConditionCheck": {
            "TableName": APP_TABLE_NAME,
            "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"}),
            "ConditionExpression": "attribute_exists(pk)",
        }
    }


# --- Plant history (ADR-0035) ------------------------------------------------------------------


def _get_media(garden_id: str, media_id: str) -> dict[str, Any] | None:
    return (
        _get_table()
        .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"})
        .get("Item")
    )


def _entry_put(entry: dict[str, Any]) -> dict[str, Any]:
    """A PlantLog Put; `attribute_not_exists` makes a retried write (same deterministic
    `entry_id`) cancel instead of duplicating (ADR-0035 §1)."""
    return {
        "Put": {
            "TableName": APP_TABLE_NAME,
            "Item": _to_dynamo(entry),
            "ConditionExpression": "attribute_not_exists(pk)",
        }
    }


def _plant_count_item(garden_id: str, plant_id: str, delta: int) -> dict[str, Any]:
    """`history_count += delta` on the Plant — also the guard that the plant exists (one
    transaction can't touch the same item twice, so this replaces a separate ConditionCheck)."""
    return {
        "Update": {
            "TableName": APP_TABLE_NAME,
            "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"}),
            "UpdateExpression": "ADD history_count :d",
            "ExpressionAttributeValues": _to_dynamo({":d": delta}),
            "ConditionExpression": "attribute_exists(pk)",
        }
    }


def _bump_latest(
    garden_id: str,
    plant_id: str,
    observed_on: str,
    *,
    media_id: str | None = None,
    status: str | None = None,
) -> None:
    """Denormalizes the plant's latest state onto the Plant (ADR-0035 §4), each facet only if this
    observation is at least as new as the one already there — so backfilling an old photo never
    replaces a newer one. Best-effort: the entry itself is already written."""
    facets = [("latest_observed_on", {})]
    if media_id:
        facets.append(("latest_photo_on", {"media_id": media_id}))
    if status:
        facets.append(("latest_status_on", {"latest_status": status}))
    for date_field, extra in facets:
        fields = {date_field: observed_on, **extra}
        try:
            _get_table().update_item(
                Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                ExpressionAttributeNames={f"#{k}": k for k in fields},
                ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
                ConditionExpression=(
                    f"attribute_exists(pk) AND (attribute_not_exists(#{date_field}) "
                    f"OR #{date_field} <= :{date_field})"
                ),
            )
        except Exception as e:
            code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
            if code != "ConditionalCheckFailedException":
                logger.exception("Failed to update latest %s on plant %s", date_field, plant_id)


def _release_plant_photo(garden_id: str, previous_plant_id: str | None, media_id: str) -> None:
    """A photo moved away from a plant stops being that plant's current photo (best-effort; only
    if it still is — a newer photo is left alone)."""
    if not previous_plant_id:
        return
    try:
        _get_table().update_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{previous_plant_id}"},
            UpdateExpression="REMOVE media_id",
            ConditionExpression="media_id = :m",
            ExpressionAttributeValues={":m": media_id},
        )
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code != "ConditionalCheckFailedException":
            logger.exception(
                "Failed to release photo %s from plant %s", media_id, previous_plant_id
            )


def _retraction_items(
    garden_id: str, previous_plant_id: str, media_id: str, action_id: str
) -> list[dict[str, Any]]:
    """Moving a photo away from a plant (ADR-0035 §3): for each of that plant's entries still
    showing it, mark the entry (`retracted_media_ids`) and append a `retraction` entry beside it —
    nothing is deleted. Also adjusts the plant's `history_count` for entries left empty."""
    entries = ph.entries_with_media(
        _query_all(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(ph.prefix(previous_plant_id))
        ),
        media_id,
    )
    items: list[dict[str, Any]] = []
    hidden = 0
    for target in entries:
        items.append(
            {
                "Update": {
                    "TableName": APP_TABLE_NAME,
                    "Key": _to_dynamo({"pk": target["pk"], "sk": target["sk"]}),
                    "UpdateExpression": (
                        "SET retracted_media_ids = "
                        "list_append(if_not_exists(retracted_media_ids, :empty), :m)"
                    ),
                    "ExpressionAttributeValues": _to_dynamo({":empty": [], ":m": [media_id]}),
                    "ConditionExpression": "attribute_exists(pk)",
                }
            }
        )
        items.append(
            _entry_put(
                ph.new_entry(
                    garden_id,
                    previous_plant_id,
                    kind="retraction",
                    source="confirmed",
                    observed_on=target["observed_on"],
                    entry_id=f"{action_id}-r-{target['entry_id']}"[:96],
                    media_ids=[media_id],
                    action_id=action_id,
                    retracts=target["entry_id"],
                )
            )
        )
        after = {**target, "retracted_media_ids": [*ph.retracted(target), media_id]}
        if ph.counts_as_visible(target) and not ph.counts_as_visible(after):
            hidden += 1
    if hidden:
        items.append(_plant_count_item(garden_id, previous_plant_id, -hidden))
    return items


def _append_entry(
    garden_id: str,
    entry: dict[str, Any],
    *,
    media_id: str | None = None,
    extra_items: list[dict[str, Any]] | None = None,
) -> bool:
    """Writes one entry outside a confirm (createPlant, createGoal, the history endpoint): the
    entry + `history_count` in one transaction, then the latest-state bump. False when it was
    already written (same deterministic id) or the plant is gone."""
    try:
        _get_client().transact_write_items(
            TransactItems=[
                _entry_put(entry),
                _plant_count_item(garden_id, entry["plant_id"], 1),
                *(extra_items or []),
            ]
        )
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "TransactionCanceledException":
            logger.info("plant_log_skipped entry_id=%s (exists or no plant)", entry["entry_id"])
            return False
        raise
    _bump_latest(
        garden_id,
        entry["plant_id"],
        entry["observed_on"],
        media_id=media_id,
        status=entry.get("status"),
    )
    return True


def _append_photo_entries_best_effort(
    garden_id: str,
    plant_id: str,
    media_ids: list[str],
    *,
    goal_id: str | None = None,
) -> None:
    """A photo the gardener filed to a plant themselves (createPlant/createGoal): one `photo`
    entry each, id = the media id so a later identity "match" on the same photo is a no-op."""
    for media_id in media_ids:
        try:
            media = _get_media(garden_id, media_id)
            if not media:
                continue
            _append_entry(
                garden_id,
                ph.new_entry(
                    garden_id,
                    plant_id,
                    kind="photo",
                    source="user",
                    observed_on=ph.observed_on_for_media(media),
                    observed_at=ph.observed_at_for_media(media),
                    entry_id=media_id,
                    media_ids=[media_id],
                    goal_id=goal_id,
                ),
                media_id=media_id,
            )
        except Exception:
            logger.exception("Failed to add photo %s to plant %s history", media_id, plant_id)


def _transact(items: list[dict[str, Any]]) -> None:
    try:
        _get_client().transact_write_items(TransactItems=items)
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "TransactionCanceledException":
            raise ActionNotApplicable() from e
        raise


def _confirmed_photo_items(
    garden_id: str,
    goal_id: str,
    action_id: str,
    plant_id: str,
    media_id: str,
    media: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """What a confirmed photo-to-plant link writes (ADR-0034 + ADR-0035 §2–3): the photo entry on
    the new plant (+ its count, which also guards the plant exists), the Media link, and — if the
    photo was filed under another plant — that plant's retraction."""
    entry = ph.new_entry(
        garden_id,
        plant_id,
        kind="photo",
        source="confirmed",
        observed_on=ph.observed_on_for_media(media),
        observed_at=ph.observed_at_for_media(media),
        entry_id=action_id,
        media_ids=[media_id],
        goal_id=goal_id,
        action_id=action_id,
    )
    link = _media_link_item(
        garden_id, media_id, {"plant_id": plant_id, "plant_link_source": "confirmed"}
    )
    previous = (media or {}).get("plant_id")
    if previous == plant_id and ph.entries_with_media(
        _query_all(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(ph.prefix(plant_id))
        ),
        media_id,
    ):
        # Already in this plant's history (e.g. filed there by the gardener): confirm the link,
        # don't show the same photo twice.
        return [link, _plant_exists_check(garden_id, plant_id)], entry
    items = [_entry_put(entry), _plant_count_item(garden_id, plant_id, 1), link]
    if previous and previous != plant_id:
        items += _retraction_items(garden_id, previous, media_id, action_id)
    return items, entry


def _after_confirmed_photo(
    garden_id: str, plant_id: str, media_id: str, media: dict[str, Any] | None, entry: dict
) -> None:
    _bump_latest(garden_id, plant_id, entry["observed_on"], media_id=media_id)
    previous = (media or {}).get("plant_id")
    if previous and previous != plant_id:
        _release_plant_photo(garden_id, previous, media_id)


def _apply_complete_task(garden_id: str, goal_id: str, action_id: str, params: dict) -> None:
    task_id, media_id = params["task_id"], params.get("media_id")
    task_sk = f"TASK#{goal_id}#{task_id}"
    task = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": task_sk}).get("Item")
    if not task:
        raise ActionNotApplicable()
    sets = ["#status = :done"]
    values: dict[str, Any] = {":done": "done", ":pending": "pending"}
    if media_id:
        sets.append("media_id = :mid")
        values[":mid"] = media_id
    if params.get("feedback"):
        sets.append("feedback = :fb")
        values[":fb"] = params["feedback"]
    items = [
        _confirm_action_item(garden_id, goal_id, action_id),
        {
            "Update": {
                "TableName": APP_TABLE_NAME,
                "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": task_sk}),
                # Same write the old instant check-in made, now only after a confirmed proposal:
                # done + photo + feedback, and out of TasksDueIndex (Phase 7.5+).
                "UpdateExpression": "SET " + ", ".join(sets) + " REMOVE gsi1pk, gsi1sk, due_date",
                "ExpressionAttributeNames": {"#status": "status"},
                "ExpressionAttributeValues": _to_dynamo(values),
                "ConditionExpression": "#status = :pending",
            }
        },
    ]
    plant_id = task.get("plant_id")
    media = _get_media(garden_id, media_id) if media_id else None
    if media_id:
        fields: dict[str, Any] = {"task_id": task_id, "goal_id": goal_id}
        if plant_id:
            fields |= {"plant_id": plant_id, "plant_link_source": "confirmed"}
        items.append(_media_link_item(garden_id, media_id, fields))
    # ADR-0035 §2: the check-in joins the plant's history in the same transaction — but only if
    # the plant still exists, so a deleted plant never blocks completing its task.
    entry = None
    plant_exists = bool(plant_id) and bool(
        _get_table()
        .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"})
        .get("Item")
    )
    if plant_exists:
        entry = ph.new_entry(
            garden_id,
            plant_id,
            kind="checkin",
            source="confirmed",
            observed_on=ph.observed_on_for_media(media) if media else ph.today(),
            observed_at=ph.observed_at_for_media(media),
            entry_id=action_id,
            media_ids=[media_id] if media_id else None,
            note=(params.get("note") or task.get("title") or "")[: ph.NOTE_MAX_CHARS] or None,
            goal_id=goal_id,
            task_id=task_id,
            action_id=action_id,
        )
        items += [_entry_put(entry), _plant_count_item(garden_id, plant_id, 1)]
    _transact(items)
    if entry:
        _bump_latest(garden_id, plant_id, entry["observed_on"], media_id=media_id)
    try:
        _write_event(
            garden_id,
            "task.checkin",
            {"goalId": goal_id, "taskId": task_id, "taskTitle": task.get("title", "")},
        )
    except Exception:
        logger.exception("Failed to write task.checkin event for task %s", task_id)


def _apply_set_plant(garden_id: str, goal_id: str, action_id: str, params: dict) -> None:
    plant_id, media_id = params["plant_id"], params["media_id"]
    media = _get_media(garden_id, media_id)
    items, entry = _confirmed_photo_items(garden_id, goal_id, action_id, plant_id, media_id, media)
    _transact([_confirm_action_item(garden_id, goal_id, action_id), *items])
    _after_confirmed_photo(garden_id, plant_id, media_id, media, entry)


def _apply_set_goal_plant(garden_id: str, goal_id: str, action_id: str, params: dict) -> None:
    plant_id, media_id = params["plant_id"], params.get("media_id")
    media = _get_media(garden_id, media_id) if media_id else None
    items = [
        _confirm_action_item(garden_id, goal_id, action_id),
        {
            "Update": {
                "TableName": APP_TABLE_NAME,
                "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"GOAL#{goal_id}"}),
                "UpdateExpression": "SET plant_id = :p",
                "ExpressionAttributeValues": _to_dynamo({":p": plant_id}),
                "ConditionExpression": "attribute_exists(pk)",
            }
        },
    ]
    entry = None
    if media_id:
        photo_items, entry = _confirmed_photo_items(
            garden_id, goal_id, action_id, plant_id, media_id, media
        )
        items += photo_items
    else:
        items.append(_plant_exists_check(garden_id, plant_id))
    _transact(items)
    if media_id and entry:
        _after_confirmed_photo(garden_id, plant_id, media_id, media, entry)
    # Tasks proposed before the plant was known inherit it now (best-effort, like Goal → Task).
    try:
        for task in _query_all(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with(f"TASK#{goal_id}#")
        ):
            if not task.get("plant_id"):
                _get_table().update_item(
                    Key={"pk": task["pk"], "sk": task["sk"]},
                    UpdateExpression="SET plant_id = :p",
                    ExpressionAttributeValues={":p": plant_id},
                )
    except Exception:
        logger.exception("Failed to propagate plant %s to goal %s tasks", plant_id, goal_id)


def _apply_create_plant(garden_id: str, goal_id: str, action_id: str, params: dict) -> None:
    stake_no, media_id = params["stake_no"], params.get("media_id")
    # Read before the plant is written: `_write_plant` re-points the photo at the new plant.
    media = _get_media(garden_id, media_id) if media_id else None
    # A plantation's plants share species/variety: copy them from an existing numbered plant.
    template = next(
        (
            p
            for p in _query_all(
                KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
                & Key("sk").begins_with("PLANT#")
            )
            if p.get("stake_no") is not None
        ),
        {},
    )
    try:
        plant_id = _write_plant(
            garden_id,
            species=template.get("species", "Unknown"),
            variety=template.get("variety"),
            stage=template.get("stage", "new"),
            stake_no=stake_no,
            # The photo that revealed the new stake becomes the new plant's photo.
            media_id=media_id,
        )
    except DuplicateStakeError as e:
        raise ActionNotApplicable() from e
    items = [_confirm_action_item(garden_id, goal_id, action_id)]
    entry = None
    if media_id:
        photo_items, entry = _confirmed_photo_items(
            garden_id, goal_id, action_id, plant_id, media_id, media
        )
        items += photo_items
    _transact(items)
    if media_id and entry:
        _after_confirmed_photo(garden_id, plant_id, media_id, media, entry)


ACTION_APPLIERS = {
    "complete_task": _apply_complete_task,
    "set_plant": _apply_set_plant,
    "set_goal_plant": _apply_set_goal_plant,
    "create_plant": _apply_create_plant,
}


def _action_path(event: dict[str, Any]) -> tuple[str, str, str] | None:
    p = event.get("pathParameters") or {}
    ids = (p.get("gardenId"), p.get("goalId"), p.get("actionId"))
    return ids if all(ids) else None


def confirm_action(event: dict[str, Any]) -> dict[str, Any]:
    """`POST …/goals/{goalId}/actions/{actionId}/confirm` (ADR-0034 §3): the only way a proposed
    identity or task change is applied. Synchronous, no model call (the PA-02 approve pattern),
    acts only on `proposed` actions, idempotent for an already-confirmed one."""
    ids = _action_path(event)
    if not ids:
        return _error(400, "gardenId, goalId and actionId are required")
    garden_id, goal_id, action_id = ids
    try:
        found = _action_params(garden_id, goal_id, action_id)
    except Exception:
        logger.exception("Failed to read action %s", action_id)
        return _error(500, "could not confirm")
    if not found:
        return _error(404, "action not found")
    action, params = found
    if action.get("status") == "confirmed":
        return _response(
            200, {"actionId": action_id, "status": "confirmed", "type": action["type"]}
        )
    if action.get("status") != "proposed":
        return _error(409, f"this suggestion is {action.get('status')}")
    applier = ACTION_APPLIERS.get(action.get("type"))
    if applier is None:
        return _error(409, "unknown action type")
    try:
        applier(garden_id, goal_id, action_id, params)
    except ActionNotApplicable:
        return _error(409, "this suggestion no longer applies (it may already be done)")
    except Exception:
        logger.exception("Failed to confirm action %s", action_id)
        return _error(500, "could not confirm")
    return _response(200, {"actionId": action_id, "status": "confirmed", "type": action["type"]})


def reject_action(event: dict[str, Any]) -> dict[str, Any]:
    """`POST …/actions/{actionId}/reject`: dismisses a proposal; changes nothing else."""
    ids = _action_path(event)
    if not ids:
        return _error(400, "gardenId, goalId and actionId are required")
    garden_id, goal_id, action_id = ids
    try:
        _get_table().update_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"ACTION#{goal_id}#{action_id}"},
            UpdateExpression="SET #s = :rejected, resolved_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":rejected": "rejected",
                ":proposed": "proposed",
                ":now": datetime.now(UTC).isoformat(),
            },
            ConditionExpression="#s = :proposed",
        )
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            found = _action_params(garden_id, goal_id, action_id)
            if not found:
                return _error(404, "action not found")
            if found[0].get("status") == "rejected":
                return _response(200, {"actionId": action_id, "status": "rejected"})
            return _error(409, f"this suggestion is {found[0].get('status')}")
        logger.exception("Failed to reject action %s", action_id)
        return _error(500, "could not reject")
    return _response(200, {"actionId": action_id, "status": "rejected"})


def get_garden(event: dict[str, Any]) -> dict[str, Any]:
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    table = _get_table()
    try:
        resp = table.get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"})
    except Exception:
        logger.exception("Failed to read garden %s", garden_id)
        return _error(500, "could not read garden")

    item = resp.get("Item")
    if not item:
        return _error(404, "garden not found")

    result: dict[str, Any] = {
        "gardenId": item["garden_id"],
        "name": item["name"],
        "geolocation": item["geolocation"],
        "vision": item.get("vision", ""),
        "ownerUserId": item["owner_user_id"],
        "createdAt": item["created_at"],
    }
    if item.get("climate_zone"):
        result["climateZone"] = item["climate_zone"]
    if item.get("context") is not None:
        result["context"] = _context_to_api(item["context"])
    if item.get("media_id"):
        media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{item['media_id']}"}
        ).get("Item")
        if media_item:
            result["photoUrl"] = _generate_download_url(_display_key(media_item))
    return _response(200, result)


def update_garden(event: dict[str, Any]) -> dict[str, Any]:
    """`PATCH /gardens/{gardenId}` (CP-01, ADR-0032): sets any of `context` (replaced as a whole
    block — no field-level merge), `climateZone`, `vision`. 404 if the garden doesn't exist; the
    condition makes the update never create a half-formed Garden without its ownership index."""
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _error(400, "body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "body must be a JSON object")

    updates: dict[str, Any] = {}
    if "context" in payload:
        context, err = _parse_context(payload["context"])
        if err:
            return _error(400, err)
        updates["context"] = context
    if "climateZone" in payload:
        climate_zone, err = _parse_climate_zone(payload["climateZone"])
        if err:
            return _error(400, err)
        updates["climate_zone"] = climate_zone
    if "vision" in payload:
        if not isinstance(payload["vision"], str):
            return _error(400, "vision must be a string if provided")
        updates["vision"] = payload["vision"]
    if not updates:
        return _error(400, "provide at least one of context, climateZone, vision")
    updates["updated_at"] = datetime.now(UTC).isoformat()

    names = {f"#{k}": k for k in updates}
    values = {f":{k}": v for k, v in updates.items()}
    try:
        _get_table().update_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"},
            UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in updates),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(pk)",
        )
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            return _error(404, "garden not found")
        logger.exception("Failed to update garden %s", garden_id)
        return _error(500, "could not update garden")

    return get_garden(event)


def _geocode(query: str) -> tuple[float, float] | None:
    """Resolves a free-text location (e.g. "Pune, India") to (lat, lon) — a Garden only ever
    stores that free-text string, never coordinates, so this runs on every weather request
    rather than needing a new Garden field. Returns None if nothing matched."""
    url = f"{GEOCODING_URL}?{urllib.parse.urlencode({'name': query, 'count': 1})}"
    with urllib.request.urlopen(url, timeout=WEATHER_REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = json.loads(resp.read())
    results = data.get("results") or []
    if not results:
        return None
    return results[0]["latitude"], results[0]["longitude"]


def _fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    """Same call app/tools/weather/handler.py's fetch_forecast makes for specialists —
    duplicated, not imported (separate deployable units)."""
    url = (
        f"{FORECAST_URL}?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,precipitation,weather_code"
        "&forecast_days=1"
    )
    with urllib.request.urlopen(url, timeout=WEATHER_REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = json.loads(resp.read())
    current = data.get("current", {})
    return {
        "temperatureC": current.get("temperature_2m"),
        "weatherCode": current.get("weather_code"),
    }


def get_garden_weather(event: dict[str, Any]) -> dict[str, Any]:
    """Real current weather for a garden's own location (deliberately UI-agnostic — no
    icon/label baked in server-side, the frontend maps `weatherCode` to presentation, mirroring
    PA-05/Phase 6's specialist-icon/activity-presentation precedent)."""
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        item = (
            _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"}).get("Item")
        )
    except Exception:
        logger.exception("Failed to read garden %s for weather", garden_id)
        return _error(500, "could not read garden")
    if not item:
        return _error(404, "garden not found")

    try:
        coords = _geocode(item["geolocation"])
    except urllib.error.URLError:
        logger.exception("weather_geocode_failed garden_id=%s", garden_id)
        return _error(502, "upstream geocoding service unavailable")
    if not coords:
        return _error(404, "could not resolve this garden's location")

    try:
        forecast = _fetch_forecast(*coords)
    except urllib.error.URLError:
        logger.exception("weather_forecast_failed garden_id=%s", garden_id)
        return _error(502, "upstream weather service unavailable")

    return _response(200, forecast)


def _to_dynamo(item: dict[str, Any]) -> dict[str, Any]:
    """The plain low-level client (_get_client()) needs raw AttributeValue dicts — it has none
    of the resource layer's automatic Python<->DynamoDB marshalling. Delegate to boto3's own
    serializer rather than hand-rolling type tags."""
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    return {k: serializer.serialize(v) for k, v in item.items()}


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = event.get("httpMethod")
    resource = event.get("resource")
    try:
        if method == "POST" and resource == "/gardens":
            return create_garden(event)
        if method == "GET" and resource == "/gardens":
            return list_gardens(event)
        if method == "GET" and resource == "/gardens/{gardenId}":
            return get_garden(event)
        if method == "PATCH" and resource == "/gardens/{gardenId}":
            return update_garden(event)
        if method == "GET" and resource == "/gardens/{gardenId}/weather":
            return get_garden_weather(event)
        if method == "POST" and resource == "/gardens/{gardenId}/media":
            return create_media_upload(event)
        if method == "POST" and resource == "/gardens/{gardenId}/plants":
            return create_plant(event)
        if method == "GET" and resource == "/gardens/{gardenId}/plants":
            return list_plants(event)
        if method == "POST" and resource == "/gardens/{gardenId}/plants/bulk":
            return bulk_create_plants(event)
        if method == "DELETE" and resource == "/gardens/{gardenId}/plants/{plantId}":
            return delete_plant(event)
        if method == "GET" and resource == "/gardens/{gardenId}/plants/{plantId}":
            return get_plant(event)
        if method == "PATCH" and resource == "/gardens/{gardenId}/plants/{plantId}":
            return update_plant(event)
        if method == "POST" and resource == "/gardens/{gardenId}/plants/{plantId}/history":
            return post_plant_history(event)
        if method == "POST" and resource == "/gardens/{gardenId}/goals":
            return create_goal(event)
        if method == "GET" and resource == "/gardens/{gardenId}/goals":
            return list_goals(event)
        if method == "GET" and resource == "/gardens/{gardenId}/goals/{goalId}":
            return get_goal_detail(event)
        if method == "POST" and resource == "/gardens/{gardenId}/goals/{goalId}/messages":
            return post_goal_message(event)
        if (
            method == "POST"
            and resource == "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins"
        ):
            return post_task_checkin(event)
        if method == "POST" and resource == "/gardens/{gardenId}/plans/{planId}/approve":
            return approve_plan(event)
        if (
            method == "POST"
            and resource == "/gardens/{gardenId}/goals/{goalId}/actions/{actionId}/confirm"
        ):
            return confirm_action(event)
        if (
            method == "POST"
            and resource == "/gardens/{gardenId}/goals/{goalId}/actions/{actionId}/reject"
        ):
            return reject_action(event)
        if method == "GET" and resource == "/gardens/{gardenId}/tasks":
            return list_tasks(event)
        if method == "GET" and resource == "/gardens/{gardenId}/activity":
            return list_activity(event)
        return _error(404, f"no route for {method} {resource}")
    except Exception:
        logger.exception("Unhandled error for %s %s", method, resource)
        return _error(500, "internal error")
