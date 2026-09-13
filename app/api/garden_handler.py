"""Garden Client API handler (OB-01, OB-02, WS-03) — gardens, media, plants, and goal intake.

Implements every operation in app/api/openapi.yaml, invoked by API Gateway's Lambda proxy
integration (ClientApiStack, ADR-0011) — one Lambda for the whole Client API for now (ADR-0011's
"one Lambda for both operations for now" note, extended as new operations land):

- `createGarden`/`getGarden` (OB-01): both DynamoDB records a Garden needs written in one
  transaction (data-architecture.md §2) — canonical `GARDEN#{id}/METADATA` + the
  `USER#{user_id}/GARDEN#{id}` ownership index, never one without the other.
- `createMediaUpload` (OB-02): issues a presigned S3 PUT URL against `FoundationStack`'s media
  bucket and writes the `Media` record immediately (`GARDEN#{id}/MEDIA#{media_id}`) — this is
  the only step with the `s3_key`/`content_type` needed to write it (data-architecture.md §4).
- `createPlant` (OB-02): writes the `Plant` record (`GARDEN#{id}/PLANT#{plant_id}`) and, when a
  `mediaId` from a prior `createMediaUpload` call is given, atomically links that Media record
  to this plant (`plant_id` set via the same transaction) — so a Plant is never left pointing at
  a Media record that doesn't actually exist.
- `listPlants`/`deletePlant`: complete the Plant lifecycle so the frontend can stop hardcoding
  plant data. `listPlants` queries every `PLANT#*` item under the garden's partition (a single
  `Query`, not a table `Scan` — cheap even as a garden's item count grows). `deletePlant` removes
  the Plant item only; it deliberately does **not** cascade-delete the Plant's linked Media/S3
  object — that photo may still be referenced elsewhere (data-architecture.md's Media records
  are independently keyed), and no story has asked for real cleanup semantics yet.
- `createGoal` (WS-03): persists a `Goal` record (status `Intake`) and publishes a
  `goal.submitted` EventBridge event — `AgentCoreStack`'s rule routes it to the orchestrator
  Lambda asynchronously (ADR-0004/ADR-0012). **Known trade-off:** the DynamoDB write and the
  EventBridge publish aren't atomic (no cross-service transaction exists for this); if the
  write succeeds but the publish fails, the Goal record persists with no event ever fired. This
  epic is explicitly scoped to "prove the pipe" (walking-skeleton.md), not production-hardened
  exactly-once delivery — an outbox/saga pattern is future work if this gap ever matters in
  practice.
- `listGoals`/`getGoalDetail` (PA-01/PA-02/PA-03): read the real `Plan`/`Task`/`Message` records
  the orchestrator now writes (`app/orchestrator/orchestrator.py`), instead of the free-text
  `orchestrator_result` field earlier stories used. `getGoalDetail` also generates a fresh
  presigned **GET** url per attached Media record — never stored/reused, same posture as OB-02's
  upload URLs, just the read-side mirror.
- `postGoalMessage` (PA-02): writes a `role="user"` Message and publishes `goal.message.received`
  — the orchestrator resumes the conversation asynchronously, same posture as `createGoal`.
- `approvePlan` (PA-02): a synchronous, deterministic status flip (`Plan`/`Goal` → `Approved`) —
  approving a plan needs no model reasoning, so this never goes through the orchestrator/
  EventBridge at all, unlike `postGoalMessage`.

No auth yet (ADR-0004's seam is still open): `X-User-Id` is a per-browser anonymous identifier
the frontend generates and persists (garden-onboarding.md's stories), not a verified identity.
This handler only requires the header be present.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
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
# GOAL_MESSAGE_RECEIVED_DETAIL_TYPE — those are the source/detail-types its EventBridge rules
# pattern-match on.
GOAL_EVENT_SOURCE = "tendril.client-api"
GOAL_SUBMITTED_DETAIL_TYPE = "goal.submitted"
GOAL_MESSAGE_RECEIVED_DETAIL_TYPE = "goal.message.received"

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
    if not isinstance(name, str) or not name.strip():
        return _error(400, "name is required")
    if not isinstance(geolocation, str) or not geolocation.strip():
        return _error(400, "geolocation is required")
    if vision is not None and not isinstance(vision, str):
        return _error(400, "vision must be a string if provided")

    garden_id = str(uuid.uuid4())
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
    ownership_item = {
        "pk": f"USER#{user_id}",
        "sk": f"GARDEN#{garden_id}",
        "garden_id": garden_id,
        "name": name,
    }

    try:
        _get_client().transact_write_items(
            TransactItems=[
                {"Put": {"TableName": APP_TABLE_NAME, "Item": _to_dynamo(garden_item)}},
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

    return _response(201, {"plantId": plant_id})


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

    plants = [
        {
            "plantId": item["plant_id"],
            "species": item["species"],
            "variety": item.get("variety", ""),
            "stage": item.get("stage", "new"),
        }
        for item in resp.get("Items", [])
    ]
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
    if not isinstance(description, str) or not description.strip():
        return _error(400, "description is required")
    if media_ids is not None and (
        not isinstance(media_ids, list) or not all(isinstance(m, str) for m in media_ids)
    ):
        return _error(400, "mediaIds must be an array of strings if provided")

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

    try:
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

    tasks = [
        {
            "taskId": t["task_id"],
            "title": t["title"],
            "detail": t["detail"],
            "scope": t.get("scope", "plant"),
            "status": t.get("status", "pending"),
        }
        for t in tasks_resp.get("Items", [])
    ]

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

    return _response(200, {"planId": plan_id, "status": "Approved"})


def get_garden(event: dict[str, Any]) -> dict[str, Any]:
    garden_id = (event.get("pathParameters") or {}).get("gardenId")
    if not garden_id:
        return _error(400, "gardenId is required")

    try:
        resp = _get_table().get_item(Key={"pk": f"GARDEN#{garden_id}", "sk": "METADATA"})
    except Exception:
        logger.exception("Failed to read garden %s", garden_id)
        return _error(500, "could not read garden")

    item = resp.get("Item")
    if not item:
        return _error(404, "garden not found")

    return _response(
        200,
        {
            "gardenId": item["garden_id"],
            "name": item["name"],
            "geolocation": item["geolocation"],
            "vision": item.get("vision", ""),
            "ownerUserId": item["owner_user_id"],
            "createdAt": item["created_at"],
        },
    )


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
        if method == "GET" and resource == "/gardens/{gardenId}":
            return get_garden(event)
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
        if method == "POST" and resource == "/gardens/{gardenId}/plans/{planId}/approve":
            return approve_plan(event)
        return _error(404, f"no route for {method} {resource}")
    except Exception:
        logger.exception("Unhandled error for %s %s", method, resource)
        return _error(500, "internal error")
