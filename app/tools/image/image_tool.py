"""Image Tool Lambda (ADR-0033) — stateless, reusable image processing behind an IAM-authenticated
Lambda Function URL, the same "tools are APIs" shape as `app/tools/weather/` (architecture §4.2).

Two operations (`POST /inspect`, `POST /resize`, JSON bodies; contract in `openapi.yaml`):

- **inspect**: the real format (from magic bytes — never the declared type or the file extension),
  width, height and size of an image.
- **resize**: decode, apply EXIF orientation, convert to RGB, scale so the long edge is at most
  `maxLongEdge` (never upscaled), encode as JPEG stepping the quality down (85 → 70, then shrinking
  further if needed) until at most `maxBytes`, and strip all metadata (EXIF incl. GPS) unless
  `keepMetadata` is true. One decode can produce several outputs (e.g. analysis copy + thumbnail).

The tool **owns no data and holds no S3/DynamoDB permission** (ADR-0013: tools are capabilities,
not data owners). The caller passes the source as a presigned GET URL (or small inline base64), and
each output is either PUT to a caller-supplied presigned URL or returned inline as base64.

Backend callers (the Ingestion Lambda, scripts) may invoke the function directly with
`lambda:InvokeFunction`, passing the same Function-URL-shaped event this handler parses.

Limits: 10 MB source (ADR-0033 owner decision), 60 MP decompression-bomb guard.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

try:  # HEIC/HEIF (iPhone photos). Optional so unit tests run without the native wheel.
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except ImportError:  # pragma: no cover - exercised only where the wheel is missing
    HEIF_SUPPORTED = False

# force=True: same Lambda-pre-attached-root-logger fix as the other Lambdas in this repo.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.tools.image")

MAX_SOURCE_BYTES = 10 * 1024 * 1024  # ADR-0033: uploads up to 10 MB
MAX_PIXELS = 60_000_000  # decompression-bomb guard
MAX_OUTPUTS = 4
MIN_LONG_EDGE = 64
MAX_LONG_EDGE = 8000  # Bedrock Converse's per-side image ceiling; never produce more
MIN_OUTPUT_BYTES = 50_000
JPEG_QUALITIES = (85, 80, 75, 70)
SHRINK_STEP = 0.85
MAX_SHRINK_ROUNDS = 8
HTTP_TIMEOUT_SECONDS = 15

# Pillow's own guard warns above MAX_IMAGE_PIXELS and errors above twice that; we check the
# header size ourselves (below) before decoding, so keep Pillow's limit consistent with ours.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


class ToolError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def detect_format(data: bytes) -> str | None:
    """The real image format from magic bytes, or None if it isn't a recognised image."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if data[:2] == b"BM":
        return "bmp"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"):
            return "heic"
        if brand in (b"avif", b"avis"):
            return "avif"
    return None


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > MAX_SOURCE_BYTES:
                raise ToolError(413, "source image is larger than 10 MB")
            data = resp.read(MAX_SOURCE_BYTES + 1)
    except ToolError:
        raise
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise ToolError(502, f"could not fetch the source image ({e.__class__.__name__})") from e
    if len(data) > MAX_SOURCE_BYTES:
        raise ToolError(413, "source image is larger than 10 MB")
    return data


def _http_put(url: str, data: bytes) -> None:
    # The caller signs the presigned PUT with ContentType image/jpeg; the header must match.
    req = urllib.request.Request(
        url, data=data, method="PUT", headers={"Content-Type": "image/jpeg"}
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
            if resp.status >= 300:
                raise ToolError(502, f"could not store an output (HTTP {resp.status})")
    except ToolError:
        raise
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise ToolError(502, f"could not store an output ({e.__class__.__name__})") from e


def _load_source(source: Any) -> bytes:
    if not isinstance(source, dict):
        raise ToolError(400, "source must be an object with url or base64")
    if isinstance(source.get("url"), str) and source["url"]:
        return _http_get(source["url"])
    if isinstance(source.get("base64"), str) and source["base64"]:
        try:
            data = base64.b64decode(source["base64"], validate=True)
        except (binascii.Error, ValueError) as e:
            raise ToolError(400, "source.base64 is not valid base64") from e
        if len(data) > MAX_SOURCE_BYTES:
            raise ToolError(413, "source image is larger than 10 MB")
        return data
    raise ToolError(400, "source must be an object with url or base64")


def _open(data: bytes) -> tuple[Image.Image, str]:
    fmt = detect_format(data)
    if fmt is None:
        raise ToolError(422, "not an image (unrecognised file signature)")
    if fmt in ("heic", "avif") and not HEIF_SUPPORTED:
        raise ToolError(422, f"unsupported format: {fmt}")
    try:
        img = Image.open(io.BytesIO(data))
        width, height = img.size  # header only — no pixel decode yet
        if width * height > MAX_PIXELS:
            raise ToolError(422, f"image is too large ({width}x{height} pixels)")
        img.load()
    except ToolError:
        raise
    except Image.DecompressionBombError as e:
        raise ToolError(422, "image is too large (pixel count)") from e
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise ToolError(422, f"corrupt or unreadable {fmt} image") from e
    return img, fmt


def _to_rgb(img: Image.Image) -> Image.Image:
    """EXIF-orient and flatten to RGB (transparent areas become white, not black)."""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def _scaled(img: Image.Image, max_long_edge: int) -> Image.Image:
    width, height = img.size
    long_edge = max(width, height)
    if long_edge <= max_long_edge:
        return img
    ratio = max_long_edge / long_edge
    size = (max(1, round(width * ratio)), max(1, round(height * ratio)))
    return img.resize(size, Image.Resampling.LANCZOS)


def encode_jpeg(img: Image.Image, max_long_edge: int, max_bytes: int, exif: bytes | None = None):
    """JPEG at most `max_long_edge` on its long side and at most `max_bytes`: step the quality down,
    then shrink, until it fits. Returns (bytes, width, height, quality)."""
    current = _scaled(img, max_long_edge)
    for _ in range(MAX_SHRINK_ROUNDS + 1):
        for quality in JPEG_QUALITIES:
            buf = io.BytesIO()
            kwargs: dict[str, Any] = {"quality": quality, "optimize": True, "progressive": True}
            if exif:
                kwargs["exif"] = exif
            current.save(buf, "JPEG", **kwargs)
            data = buf.getvalue()
            if len(data) <= max_bytes:
                return data, current.width, current.height, quality
        next_edge = max(MIN_LONG_EDGE, int(max(current.size) * SHRINK_STEP))
        if next_edge == max(current.size):
            break
        current = _scaled(current, next_edge)
    raise ToolError(422, f"could not fit the image under {max_bytes} bytes")


def _parse_outputs(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ToolError(400, "outputs must be a non-empty array")
    if len(raw) > MAX_OUTPUTS:
        raise ToolError(400, f"at most {MAX_OUTPUTS} outputs per call")
    outputs = []
    for i, o in enumerate(raw):
        if not isinstance(o, dict):
            raise ToolError(400, f"outputs[{i}] must be an object")
        edge, max_bytes = o.get("maxLongEdge"), o.get("maxBytes")
        if (
            not isinstance(edge, int)
            or isinstance(edge, bool)
            or not (MIN_LONG_EDGE <= edge <= MAX_LONG_EDGE)
        ):
            raise ToolError(
                400, f"outputs[{i}].maxLongEdge must be an integer {MIN_LONG_EDGE}-{MAX_LONG_EDGE}"
            )
        if (
            not isinstance(max_bytes, int)
            or isinstance(max_bytes, bool)
            or not (MIN_OUTPUT_BYTES <= max_bytes <= MAX_SOURCE_BYTES)
        ):
            raise ToolError(
                400,
                f"outputs[{i}].maxBytes must be an integer {MIN_OUTPUT_BYTES}-{MAX_SOURCE_BYTES}",
            )
        dest = o.get("destinationUrl")
        if dest is not None and (not isinstance(dest, str) or not dest.startswith("https://")):
            raise ToolError(400, f"outputs[{i}].destinationUrl must be an https URL")
        outputs.append(
            {
                "name": str(o.get("name") or f"output{i}"),
                "maxLongEdge": edge,
                "maxBytes": max_bytes,
                "destinationUrl": dest,
                "keepMetadata": bool(o.get("keepMetadata", False)),
            }
        )
    return outputs


EXIF_IFD = 0x8769
EXIF_DATETIME_ORIGINAL = 36867
EXIF_DATETIME_DIGITIZED = 36868
EARLIEST_PLAUSIBLE_YEAR = 2000


def taken_at(img: Image.Image, now: datetime | None = None) -> str | None:
    """The camera's capture time (EXIF DateTimeOriginal, else DateTimeDigitized) as an ISO local
    time without a zone, or None when absent or implausible (before 2000, or in the future). Read
    from the original, before any output strips metadata (ADR-0035 §5)."""
    try:
        exif_ifd = img.getexif().get_ifd(EXIF_IFD)
    except Exception:
        return None
    now = now or datetime.now(UTC).replace(tzinfo=None)
    for tag in (EXIF_DATETIME_ORIGINAL, EXIF_DATETIME_DIGITIZED):
        raw = exif_ifd.get(tag)
        if not isinstance(raw, str):
            continue
        try:
            value = datetime.strptime(raw.strip().rstrip("\x00")[:19], "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        # A day of slack: the camera's clock is local time, `now` is UTC.
        if value.year >= EARLIEST_PLAUSIBLE_YEAR and value <= now + timedelta(days=1):
            return value.isoformat()
    return None


def _with_taken_at(result: dict[str, Any], img: Image.Image) -> dict[str, Any]:
    when = taken_at(img)
    if when:
        result["takenAt"] = when
    return result


def inspect(payload: dict[str, Any]) -> dict[str, Any]:
    data = _load_source(payload.get("source"))
    img, fmt = _open(data)
    oriented = ImageOps.exif_transpose(img)
    return _with_taken_at(
        {
            "detectedFormat": fmt,
            "width": oriented.width,
            "height": oriented.height,
            "bytes": len(data),
        },
        img,
    )


def resize(payload: dict[str, Any]) -> dict[str, Any]:
    outputs = _parse_outputs(payload.get("outputs"))
    data = _load_source(payload.get("source"))
    img, fmt = _open(data)
    exif = img.info.get("exif")
    rgb = _to_rgb(img)
    results = []
    for o in outputs:
        jpeg, width, height, quality = encode_jpeg(
            rgb, o["maxLongEdge"], o["maxBytes"], exif if o["keepMetadata"] else None
        )
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
    return _with_taken_at(
        {
            "detectedFormat": fmt,
            "width": rgb.width,
            "height": rgb.height,
            "bytes": len(data),
            "outputs": results,
        },
        img,
    )


ROUTES = {"/inspect": inspect, "/resize": resize}


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
        logger.warning("image_tool_error path=%s status=%d message=%s", path, e.status, e.message)
        return _response(e.status, {"message": e.message})
    except (json.JSONDecodeError, UnicodeDecodeError, binascii.Error):
        return _response(400, {"message": "body must be valid JSON"})
    except Exception:
        logger.exception("image_tool_unhandled path=%s", path)
        return _response(500, {"message": "internal error"})
    logger.info(
        "image_tool_ok path=%s format=%s outputs=%s",
        path,
        result.get("detectedFormat"),
        [(o["name"], o["width"], o["height"], o["bytes"]) for o in result.get("outputs", [])],
    )
    return _response(200, result)
