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

No auth yet (ADR-0004's seam is still open): `X-User-Id` is a per-browser anonymous identifier
the frontend generates and persists (garden-onboarding.md's stories), not a verified identity.
This handler only requires the header be present.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
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
    if not isinstance(content_type, str) or not content_type.strip():
        return _error(400, "contentType is required")
    if not isinstance(file_name, str) or not file_name.strip():
        return _error(400, "fileName is required")

    media_id = str(uuid.uuid4())
    s3_key = f"{garden_id}/{media_id}/{file_name}"

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
                "content_type": content_type,
                "uploaded_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception:
        logger.exception("Failed to create media upload for garden %s", garden_id)
        return _error(500, "could not create upload URL")

    return _response(201, {"uploadUrl": upload_url, "mediaId": media_id})


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

    plant_id = str(uuid.uuid4())
    plant_item = {
        "pk": f"GARDEN#{garden_id}",
        "sk": f"PLANT#{plant_id}",
        "plant_id": plant_id,
        "garden_id": garden_id,
        "species": species,
        "stage": "new",
    }
    if variety:
        plant_item["variety"] = variety
    if media_id:
        # Stored on the Plant itself (not just Media.plant_id) so list_plants/this response can
        # look up the linked photo by direct GetItem — mirrors Goal.media_ids' role for goals.
        plant_item["media_id"] = media_id

    try:
        if media_id:
            _get_client().transact_write_items(
                TransactItems=[
                    {"Put": {"TableName": APP_TABLE_NAME, "Item": _to_dynamo(plant_item)}},
                    {
                        "Update": {
                            "TableName": APP_TABLE_NAME,
                            "Key": _to_dynamo(
                                {"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}
                            ),
                            "UpdateExpression": "SET plant_id = :pid",
                            "ExpressionAttributeValues": {":pid": {"S": plant_id}},
                            "ConditionExpression": "attribute_exists(pk)",
                        }
                    },
                ]
            )
        else:
            _get_table().put_item(Item=plant_item)
    except Exception as e:
        logger.error(
            "Failed to write plant %s for garden %s: %s | response=%s",
            plant_id,
            garden_id,
            e,
            getattr(e, "response", None),
        )
        return _error(500, "could not create plant")

    result: dict[str, Any] = {"plantId": plant_id}
    if media_id:
        media_item = (
            _get_table()
            .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"})
            .get("Item")
        )
        if media_item:
            result["photoUrl"] = _generate_download_url(media_item["s3_key"])
    return _response(201, result)


def list_plants(event: dict[str, Any]) -> dict[str, Any]:
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        resp = _get_table().query(
            KeyConditionExpression=Key("pk").eq(f"GARDEN#{garden_id}")
            & Key("sk").begins_with("PLANT#")
        )
    except Exception:
        logger.exception("Failed to list plants for garden %s", garden_id)
        return _error(500, "could not list plants")

    plants = []
    for item in resp.get("Items", []):
        plant = {
            "plantId": item["plant_id"],
            "species": item["species"],
            "variety": item.get("variety", ""),
            "stage": item.get("stage", "new"),
        }
        if item.get("media_id"):
            media_item = (
                _get_table()
                .get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{item['media_id']}"})
                .get("Item")
            )
            if media_item:
                plant["photoUrl"] = _generate_download_url(media_item["s3_key"])
        plants.append(plant)
    return _response(200, plants)


def delete_plant(event: dict[str, Any]) -> dict[str, Any]:
    user_id = _get_header(event.get("headers"), "X-User-Id")
    if not user_id:
        return _error(400, "X-User-Id header is required")

    path_params = event.get("pathParameters") or {}
    garden_id = path_params.get("gardenId")
    plant_id = path_params.get("plantId")
    if not garden_id or not plant_id:
        return _error(400, "gardenId and plantId are required")

    try:
        # Deliberately does not cascade-delete the Plant's linked Media/S3 object — see the
        # module docstring's note on why.
        _get_table().delete_item(Key={"pk": f"GARDEN#{garden_id}", "sk": f"PLANT#{plant_id}"})
    except Exception:
        logger.exception("Failed to delete plant %s for garden %s", plant_id, garden_id)
        return _error(500, "could not delete plant")

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

    return _response(202, {"goalId": goal_id, "status": "Intake"})


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
                "downloadUrl": _generate_download_url(task_media_item["s3_key"]),
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
        {"role": m["role"], "content": m["content"], "createdAt": m["created_at"]}
        for m in sorted(messages_resp.get("Items", []), key=lambda m: m["sk"])
    ]

    media = []
    for media_id in goal_item.get("media_ids") or []:
        media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}
        ).get("Item")
        if media_item:
            media.append(
                {"mediaId": media_id, "downloadUrl": _generate_download_url(media_item["s3_key"])}
            )

    detail: dict[str, Any] = {
        "goal": _goal_from_item(goal_item),
        "tasks": tasks,
        "media": media,
        "messages": messages,
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

    content = payload.get("content")
    if not isinstance(content, str) or not content.strip():
        return _error(400, "content is required")

    message_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()

    try:
        _get_table().put_item(
            Item={
                "pk": f"GARDEN#{garden_id}",
                "sk": f"GOALMSG#{goal_id}#{created_at}#{message_id}",
                "message_id": message_id,
                "goal_id": goal_id,
                "role": "user",
                "content": content,
                "created_at": created_at,
            }
        )
        _get_events().put_events(
            Entries=[
                {
                    "Source": GOAL_EVENT_SOURCE,
                    "DetailType": GOAL_MESSAGE_RECEIVED_DETAIL_TYPE,
                    "Detail": json.dumps({"gardenId": garden_id, "goalId": goal_id}),
                }
            ]
        )
    except Exception:
        logger.exception("Failed to send message for goal %s", goal_id)
        return _error(500, "could not send message")

    return {"statusCode": 202, "headers": {"Access-Control-Allow-Origin": "*"}, "body": ""}


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
    """Attaches a check-in photo to a task — completes the Plant/Goal/Task/Media traceability
    chain and marks the task done in the same write. One check-in per task, not a repeatable
    progress log — that's the Phase 7+ tracker/outcome loop, not this. Also clears the task's
    TasksDueIndex attributes (Phase 7.5+) — a done task must never still appear in "which tasks
    are overdue" once it's been checked in."""
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

    media_update_expr = "SET task_id = :tid, goal_id = :gid"
    media_values: dict[str, Any] = {":tid": {"S": task_id}, ":gid": {"S": goal_id}}
    if task_item.get("plant_id"):
        media_update_expr += ", plant_id = :pid"
        media_values[":pid"] = {"S": task_item["plant_id"]}

    try:
        _get_client().transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": APP_TABLE_NAME,
                        "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": task_sk}),
                        # REMOVE is a safe no-op for tasks that never had these attributes (e.g.
                        # a plan never approved through approve_plan) — no ConditionExpression
                        # change needed. Keeps TasksDueIndex sparse (Phase 7.5+): a checked-in
                        # task drops out of "awaiting follow-up" immediately.
                        "UpdateExpression": (
                            "SET #status = :status, media_id = :mid REMOVE gsi1pk, gsi1sk, due_date"
                        ),
                        "ExpressionAttributeNames": {"#status": "status"},
                        "ExpressionAttributeValues": {
                            ":status": {"S": "done"},
                            ":mid": {"S": media_id},
                        },
                        "ConditionExpression": "attribute_exists(pk)",
                    }
                },
                {
                    "Update": {
                        "TableName": APP_TABLE_NAME,
                        "Key": _to_dynamo({"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"}),
                        "UpdateExpression": media_update_expr,
                        "ExpressionAttributeValues": media_values,
                        "ConditionExpression": "attribute_exists(pk)",
                    }
                },
            ]
        )
    except Exception:
        logger.exception("Failed to check in task %s for garden %s", task_id, garden_id)
        return _error(500, "could not check in task")

    # Best-effort, deliberately NOT folded into the transaction's try/except above (PA-05): the
    # check-in's core contract (done + photo linked) already committed successfully at this
    # point and is useful on its own — losing just the bonus agent-feedback event shouldn't turn
    # an already-successful check-in into a 500, unlike goal.submitted where a lost event leaves
    # the goal permanently stuck with no other path forward.
    try:
        _get_events().put_events(
            Entries=[
                {
                    "Source": GOAL_EVENT_SOURCE,
                    "DetailType": TASK_CHECKIN_RECEIVED_DETAIL_TYPE,
                    "Detail": json.dumps(
                        {"gardenId": garden_id, "goalId": goal_id, "taskId": task_id}
                    ),
                }
            ]
        )
    except Exception:
        logger.exception(
            "Failed to publish task.checkin.received for task %s garden %s", task_id, garden_id
        )

    try:
        _write_event(
            garden_id,
            "task.checkin",
            {"goalId": goal_id, "taskId": task_id, "taskTitle": task_item.get("title", "")},
        )
    except Exception:
        logger.exception("Failed to write task.checkin event for task %s", task_id)

    return _response(200, {"taskId": task_id, "status": "done"})


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
    if item.get("media_id"):
        media_item = table.get_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{item['media_id']}"}
        ).get("Item")
        if media_item:
            result["photoUrl"] = _generate_download_url(media_item["s3_key"])
    return _response(200, result)


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
        if method == "GET" and resource == "/gardens/{gardenId}/weather":
            return get_garden_weather(event)
        if method == "POST" and resource == "/gardens/{gardenId}/media":
            return create_media_upload(event)
        if method == "POST" and resource == "/gardens/{gardenId}/plants":
            return create_plant(event)
        if method == "GET" and resource == "/gardens/{gardenId}/plants":
            return list_plants(event)
        if method == "DELETE" and resource == "/gardens/{gardenId}/plants/{plantId}":
            return delete_plant(event)
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
        if method == "GET" and resource == "/gardens/{gardenId}/tasks":
            return list_tasks(event)
        if method == "GET" and resource == "/gardens/{gardenId}/activity":
            return list_activity(event)
        return _error(404, f"no route for {method} {resource}")
    except Exception:
        logger.exception("Unhandled error for %s %s", method, resource)
        return _error(500, "internal error")
