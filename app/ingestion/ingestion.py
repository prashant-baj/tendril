"""Ingestion Lambda (ADR-0033) — processes every uploaded photo once, on upload.

Triggered by S3 `ObjectCreated` on the media bucket, prefix `uploads/` only (processed files go under
`derived/`, which never matches, so this function cannot trigger itself). For each upload it:

1. signs a short-lived GET for the original and short-lived PUTs for
   `derived/{garden_id}/{media_id}/analysis.jpg` and `…/thumb.jpg`;
2. calls the Image Tool's `resize` once with two outputs (analysis copy 2048 px / 1.5 MB,
   thumbnail 512 px) — the tool itself holds no S3 permission (ADR-0013);
3. records the result on the `Media` item: `processing_status` (`ready` | `failed`),
   `detected_format`, dimensions, size, `analysis_s3_key`, `thumb_s3_key`, or `processing_error`.

**Videos (ADR-0038 §7):** an upload ending in `.mp4`/`.mov`/`.m4v` goes to the Media Tool's `poster`
instead: the same two outputs, made from one frame, plus the duration. Videos may be up to 100 MB
and 60 seconds.

Permanent problems (not an image, over 10 MB, corrupt, unsupported) mark the photo `failed` and
return normally. Transient problems (the tool unreachable, 5xx, throttling) raise, so S3's
asynchronous-invoke retries and the SQS failure destination take over.

`parse_key` also tolerates the pre-ADR-0033 flat layout `{garden_id}/{media_id}/{file}`, but in
practice old photos are backfilled by `scripts/backfill_media.py`, which copies each original under
`uploads/` so it arrives through the normal trigger (this role can only read `uploads/*`).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import UTC, datetime
from typing import Any

import boto3

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.ingestion")

APP_TABLE_NAME = os.getenv("APP_TABLE_NAME")  # injected by AgentCoreStack; never hardcoded
MEDIA_BUCKET_NAME = os.getenv("MEDIA_BUCKET_NAME")
IMAGE_TOOL_FUNCTION_NAME = os.getenv("IMAGE_TOOL_FUNCTION_NAME")
MEDIA_TOOL_FUNCTION_NAME = os.getenv("MEDIA_TOOL_FUNCTION_NAME")  # ADR-0038: video posters

UPLOADS_PREFIX = "uploads/"
DERIVED_PREFIX = "derived/"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # ADR-0033 owner decision
MAX_VIDEO_UPLOAD_BYTES = 100 * 1024 * 1024  # ADR-0038 §7 owner decision
MAX_VIDEO_SECONDS = 60
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v")
PRESIGN_SECONDS = 300
ANALYSIS = {"name": "analysis", "maxLongEdge": 2048, "maxBytes": 1_500_000}
THUMB = {"name": "thumb", "maxLongEdge": 512, "maxBytes": 200_000}
PERMANENT_TOOL_STATUSES = {400, 413, 422}

_s3 = None
_lambda = None
_table = None


def _get_s3():
    global _s3
    if _s3 is None:
        # SigV4 + regional endpoint: presigned URLs must be valid for the bucket's region.
        from botocore.config import Config

        _s3 = boto3.client("s3", config=Config(signature_version="s3v4"))
    return _s3


def _get_lambda():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb").Table(APP_TABLE_NAME)
    return _table


class TransientError(Exception):
    """Worth retrying (S3 async-invoke retries, then the failure destination)."""


def parse_key(key: str) -> tuple[str, str] | None:
    """(garden_id, media_id) from `uploads/{g}/{m}/{file}` or the legacy `{g}/{m}/{file}`."""
    if key.startswith(DERIVED_PREFIX):
        return None  # never process our own output
    parts = key.split("/")
    if parts[0] + "/" == UPLOADS_PREFIX:
        parts = parts[1:]
    if len(parts) < 3 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def derived_keys(garden_id: str, media_id: str) -> tuple[str, str]:
    base = f"{DERIVED_PREFIX}{garden_id}/{media_id}"
    return f"{base}/analysis.jpg", f"{base}/thumb.jpg"


def _presign(method: str, key: str) -> str:
    params: dict[str, Any] = {"Bucket": MEDIA_BUCKET_NAME, "Key": key}
    if method == "put_object":
        params["ContentType"] = "image/jpeg"
    return _get_s3().generate_presigned_url(method, Params=params, ExpiresIn=PRESIGN_SECONDS)


def is_video(key: str) -> bool:
    return key.lower().endswith(VIDEO_EXTENSIONS)


def _call_tool(
    body: dict[str, Any], *, path: str = "/resize", function_name: str | None = None
) -> tuple[int, dict[str, Any]]:
    """Invoke a tool (the Image Tool by default) directly with the Function-URL-shaped event it
    parses."""
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": "POST"}},
        "body": json.dumps(body),
    }
    try:
        resp = _get_lambda().invoke(
            FunctionName=function_name or IMAGE_TOOL_FUNCTION_NAME,
            Payload=json.dumps(event).encode("utf-8"),
        )
    except Exception as e:  # throttling, network, permissions
        raise TransientError(f"image tool invoke failed: {e.__class__.__name__}") from e
    payload = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise TransientError(f"image tool error: {payload.get('errorType', 'unknown')}")
    status = int(payload.get("statusCode", 500))
    try:
        result = json.loads(payload.get("body") or "{}")
    except json.JSONDecodeError:
        result = {}
    return status, result


def _record(garden_id: str, media_id: str, fields: dict[str, Any], remove: list[str]) -> bool:
    """Conditional update of the Media item; False if it doesn't exist (never creates one)."""
    fields = {**fields, "processed_at": datetime.now(UTC).isoformat()}
    names = {f"#{k}": k for k in fields} | {f"#{k}": k for k in remove}
    values = {f":{k}": v for k, v in fields.items()}
    expr = "SET " + ", ".join(f"#{k} = :{k}" for k in fields)
    if remove:
        expr += " REMOVE " + ", ".join(f"#{k}" for k in remove)
    try:
        _get_table().update_item(
            Key={"pk": f"GARDEN#{garden_id}", "sk": f"MEDIA#{media_id}"},
            UpdateExpression=expr,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(pk)",
        )
    except Exception as e:
        code = (getattr(e, "response", None) or {}).get("Error", {}).get("Code")
        if code == "ConditionalCheckFailedException":
            logger.warning("media_record_missing garden_id=%s media_id=%s", garden_id, media_id)
            return False
        raise
    return True


def _mark_failed(garden_id: str, media_id: str, message: str) -> None:
    logger.warning("media_failed garden_id=%s media_id=%s reason=%s", garden_id, media_id, message)
    _record(
        garden_id,
        media_id,
        {"processing_status": "failed", "processing_error": message[:300]},
        remove=["analysis_s3_key", "thumb_s3_key"],
    )


def process_object(key: str, size: int | None) -> str:
    """Processes one uploaded object. Returns the outcome (`ready`, `failed`, `skipped`)."""
    ids = parse_key(key)
    if ids is None:
        logger.info("ingestion_skip_key reason=unrecognised_layout")
        return "skipped"
    garden_id, media_id = ids
    video = is_video(key)
    limit = MAX_VIDEO_UPLOAD_BYTES if video else MAX_UPLOAD_BYTES
    if size is not None and size > limit:
        _mark_failed(garden_id, media_id, "larger than 100 MB" if video else "larger than 10 MB")
        return "failed"

    analysis_key, thumb_key = derived_keys(garden_id, media_id)
    body: dict[str, Any] = {
        "source": {"url": _presign("get_object", key)},
        "outputs": [
            dict(ANALYSIS, destinationUrl=_presign("put_object", analysis_key)),
            dict(THUMB, destinationUrl=_presign("put_object", thumb_key)),
        ],
    }
    if video:
        # ADR-0038 §7: a poster frame (analysis copy + thumbnail) from the Media Tool.
        body["maxDurationSeconds"] = MAX_VIDEO_SECONDS
        status, result = _call_tool(body, path="/poster", function_name=MEDIA_TOOL_FUNCTION_NAME)
    else:
        status, result = _call_tool(body)
    if status in PERMANENT_TOOL_STATUSES:
        _mark_failed(garden_id, media_id, result.get("message") or f"rejected ({status})")
        return "failed"
    if status != 200:
        raise TransientError(f"{'media' if video else 'image'} tool returned {status}")

    analysis = next((o for o in result.get("outputs", []) if o.get("name") == "analysis"), {})
    written = _record(
        garden_id,
        media_id,
        {
            "processing_status": "ready",
            "detected_format": result.get("detectedFormat"),
            "width": result.get("width"),
            "height": result.get("height"),
            "bytes": result.get("bytes"),
            "analysis_s3_key": analysis_key,
            "thumb_s3_key": thumb_key,
            "analysis_bytes": analysis.get("bytes"),
            # ADR-0035 §5: the photo's own capture time dates its plant-history entry.
            **({"taken_at": result["takenAt"]} if result.get("takenAt") else {}),
            # ADR-0038 §7: a video's facts (its poster frame is the analysis copy/thumbnail).
            **(
                {"media_kind": "video", "duration_seconds": result.get("durationSeconds")}
                if video
                else {}
            ),
        },
        remove=["processing_error"],
    )
    logger.info(
        "media_ready garden_id=%s media_id=%s format=%s size=%sx%s analysis_bytes=%s recorded=%s",
        garden_id,
        media_id,
        result.get("detectedFormat"),
        result.get("width"),
        result.get("height"),
        analysis.get("bytes"),
        written,
    )
    return "ready" if written else "skipped"


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    for record in event.get("Records", []):
        obj = (record.get("s3") or {}).get("object") or {}
        # S3 event keys are URL-encoded (spaces arrive as '+').
        key = urllib.parse.unquote_plus(obj.get("key", ""))
        outcome = process_object(key, obj.get("size"))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {"outcomes": outcomes}
