"""Media Tool Lambda (ADR-0038 §7): poster frames for short videos, same "tools are APIs" shape as
the Image Tool (`app/tools/image/`). One operation, `POST /poster` (contract in `openapi.yaml`):

- downloads the video from a presigned GET URL (at most 100 MB) to `/tmp`;
- reads its container format (MP4 or QuickTime, from the `ftyp` box, never the file name),
  duration and frame size from ffmpeg's header probe;
- refuses videos longer than `maxDurationSeconds` (default 60, the owner's limit);
- grabs one frame (at 1 s, or the middle of a shorter clip; ffmpeg applies the phone's rotation)
  and encodes each requested output as a JPEG of at most `maxLongEdge` / `maxBytes`, PUT to a
  caller-supplied presigned URL (or returned inline as base64).

The tool owns no data and holds no S3/DynamoDB permission (ADR-0013). ffmpeg comes from the
`imageio-ffmpeg` wheel, which bundles a static binary for the ARM64 Lambda image.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from typing import Any

import imageio_ffmpeg
from PIL import Image

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.tools.media")

MAX_SOURCE_BYTES = 100 * 1024 * 1024  # ADR-0038 §7 owner decision
DEFAULT_MAX_DURATION_SECONDS = 60
MAX_OUTPUTS = 4
MAX_LONG_EDGE = 8000
MIN_MAX_BYTES = 20_000
JPEG_QUALITIES = (85, 80, 75, 70)
MAX_SHRINK_ROUNDS = 8
HTTP_TIMEOUT_SECONDS = 20
FFMPEG_TIMEOUT_SECONDS = 20
DURATION = re.compile(r"Duration:\s*(\d+):(\d{2}):(\d{2}(?:\.\d+)?)")
VIDEO_SIZE = re.compile(r"Video:.*?\b(\d{2,5})x(\d{2,5})\b")
ROTATION = re.compile(r"(?:rotate\s*:\s*|rotation of\s*)(-?\d+)")


class ToolError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def detect_format(head: bytes) -> str | None:
    """`mp4` or `mov` from the ISO-BMFF `ftyp` box (bytes 4..12), or None if not a video we take."""
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    return "mov" if head[8:12] == b"qt  " else "mp4"


def _download(url: str, path: str) -> int:
    """Streams the video to `path`, refusing more than MAX_SOURCE_BYTES. Returns its size."""
    if not url.startswith("https://"):
        raise ToolError(400, "source.url must be an https URL")
    total = 0
    try:
        with (
            urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SECONDS) as resp,
            open(path, "wb") as f,
        ):
            while chunk := resp.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_SOURCE_BYTES:
                    raise ToolError(413, "video is larger than 100 MB")
                f.write(chunk)
    except urllib.error.HTTPError as e:
        raise ToolError(400, f"could not fetch the source ({e.code})") from e
    except urllib.error.URLError as e:
        raise ToolError(502, "could not fetch the source") from e
    return total


def probe(path: str) -> dict[str, Any]:
    """Duration (s), width and height from ffmpeg's stream header (no full decode). Width/height are
    as displayed: a 90°/270° rotation tag swaps them."""
    proc = subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        timeout=FFMPEG_TIMEOUT_SECONDS,
    )
    info = proc.stderr
    duration = DURATION.search(info)
    size = VIDEO_SIZE.search(info)
    if not duration or not size:
        raise ToolError(422, "not a readable video")
    h, m, s = duration.groups()
    width, height = int(size.group(1)), int(size.group(2))
    rotation = ROTATION.search(info)
    if rotation and abs(int(rotation.group(1))) % 180 == 90:
        width, height = height, width
    return {
        "durationSeconds": round(int(h) * 3600 + int(m) * 60 + float(s), 2),
        "width": width,
        "height": height,
    }


def grab_frame(path: str, at_seconds: float) -> Image.Image:
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "frame.png")
        proc = subprocess.run(
            [
                imageio_ffmpeg.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{at_seconds:.2f}",
                "-i",
                path,
                "-frames:v",
                "1",
                "-y",
                out,
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0 or not os.path.exists(out):
            raise ToolError(422, "could not read a frame from the video")
        img = Image.open(out)
        img.load()
        return img.convert("RGB")


def _scaled(img: Image.Image, max_long_edge: int) -> Image.Image:
    width, height = img.size
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return img
    ratio = max_long_edge / long_edge
    return img.resize(
        (max(1, round(width * ratio)), max(1, round(height * ratio))), Image.Resampling.LANCZOS
    )


def encode_jpeg(img: Image.Image, max_long_edge: int, max_bytes: int):
    """Same rule as the Image Tool: step the quality down, then shrink, until it fits."""
    current = _scaled(img, max_long_edge)
    for _ in range(MAX_SHRINK_ROUNDS + 1):
        for quality in JPEG_QUALITIES:
            buf = io.BytesIO()
            current.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
            if buf.tell() <= max_bytes:
                return buf.getvalue(), current.width, current.height, quality
        current = current.resize(
            (max(1, int(current.width * 0.8)), max(1, int(current.height * 0.8))),
            Image.Resampling.LANCZOS,
        )
    raise ToolError(422, "could not fit the frame under maxBytes")


def _http_put(url: str, data: bytes) -> None:
    req = urllib.request.Request(
        url, data=data, method="PUT", headers={"Content-Type": "image/jpeg"}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS):
            pass
    except urllib.error.URLError as e:
        raise ToolError(502, "could not store an output") from e


def _parse_outputs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_OUTPUTS:
        raise ToolError(400, f"outputs must be a list of 1-{MAX_OUTPUTS} items")
    outputs = []
    for i, o in enumerate(raw):
        if not isinstance(o, dict):
            raise ToolError(400, f"outputs[{i}] must be an object")
        edge, max_bytes = o.get("maxLongEdge"), o.get("maxBytes")
        if not isinstance(edge, int) or not 16 <= edge <= MAX_LONG_EDGE:
            raise ToolError(400, f"outputs[{i}].maxLongEdge must be 16-{MAX_LONG_EDGE}")
        if not isinstance(max_bytes, int) or max_bytes < MIN_MAX_BYTES:
            raise ToolError(400, f"outputs[{i}].maxBytes must be at least {MIN_MAX_BYTES}")
        dest = o.get("destinationUrl")
        if dest is not None and (not isinstance(dest, str) or not dest.startswith("https://")):
            raise ToolError(400, f"outputs[{i}].destinationUrl must be an https URL")
        outputs.append(
            {
                "name": str(o.get("name") or f"output{i}"),
                "maxLongEdge": edge,
                "maxBytes": max_bytes,
                "destinationUrl": dest,
            }
        )
    return outputs


def poster(payload: dict[str, Any]) -> dict[str, Any]:
    outputs = _parse_outputs(payload.get("outputs"))
    source = payload.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("url"), str):
        raise ToolError(400, "source must be an object with url")
    max_duration = payload.get("maxDurationSeconds", DEFAULT_MAX_DURATION_SECONDS)
    if not isinstance(max_duration, (int, float)) or max_duration <= 0:
        raise ToolError(400, "maxDurationSeconds must be a positive number")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "source")
        size = _download(source["url"], path)
        with open(path, "rb") as f:
            fmt = detect_format(f.read(12))
        if fmt is None:
            raise ToolError(422, "not an MP4 or MOV video")
        info = probe(path)
        if info["durationSeconds"] > max_duration:
            raise ToolError(422, f"video is longer than {int(max_duration)} seconds")
        frame = grab_frame(path, min(1.0, info["durationSeconds"] / 2))
    results = []
    for o in outputs:
        jpeg, width, height, quality = encode_jpeg(frame, o["maxLongEdge"], o["maxBytes"])
        result: dict[str, Any] = {
            "name": o["name"],
            "format": "jpeg",
            "width": width,
            "height": height,
            "bytes": len(jpeg),
            "quality": quality,
        }
        if o["destinationUrl"]:
            _http_put(o["destinationUrl"], jpeg)
            result["stored"] = True
        else:
            result["base64"] = base64.b64encode(jpeg).decode("ascii")
        results.append(result)
    return {"detectedFormat": fmt, "bytes": size, **info, "outputs": results}


ROUTES = {"/poster": poster}


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "POST")
    path = event.get("rawPath") or "/"
    operation = ROUTES.get(path)
    if method != "POST" or operation is None:
        return _response(404, {"message": f"no route for {method} {path}"})
    try:
        raw = event.get("body") or "{}"
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw).decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ToolError(400, "body must be a JSON object")
        result = operation(payload)
    except ToolError as e:
        # Never log URLs: presigned URLs carry credentials-equivalent signatures.
        logger.warning("media_tool_error path=%s status=%d message=%s", path, e.status, e.message)
        return _response(e.status, {"message": e.message})
    except (json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        return _response(400, {"message": "body must be valid JSON"})
    except subprocess.TimeoutExpired:
        return _response(422, {"message": "video processing timed out"})
    except Exception:
        logger.exception("media_tool_unhandled path=%s", path)
        return _response(500, {"message": "internal error"})
    logger.info(
        "media_tool_ok path=%s format=%s duration=%s",
        path,
        result.get("detectedFormat"),
        result.get("durationSeconds"),
    )
    return _response(200, result)
