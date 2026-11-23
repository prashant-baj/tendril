"""Unit tests for the Image Tool (ADR-0033). No network: `_http_get`/`_http_put` are monkeypatched.

Fixtures are generated in memory with Pillow so the tests cover the real failure seen in dev on
2026-11-23 (a 4.6 MB PNG named .jpg) without committing binary files.
"""

import base64
import io
import json
import os

import image_tool as tool
import pytest
from PIL import Image


def _png(size=(3000, 2000), mode="RGB", color=(30, 120, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, "PNG")
    return buf.getvalue()


def _noise(size=(4000, 3000)) -> Image.Image:
    # Random pixels compress badly: forces quality stepping and extra shrinking.
    return Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))


def _jpeg(img: Image.Image, **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def _event(path, body, method="POST"):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": json.dumps(body),
    }


def _call(path, body):
    resp = tool.handler(_event(path, body), None)
    return resp["statusCode"], json.loads(resp["body"])


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


ANALYSIS = {"name": "analysis", "maxLongEdge": 2048, "maxBytes": 1_500_000}
THUMB = {"name": "thumb", "maxLongEdge": 512, "maxBytes": 200_000}


# --- format detection -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("head", "fmt"),
    [
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "jpeg"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "png"),
        (b"GIF89a\x00\x00", "gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00", "heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00", "heic"),
        (b"\x00\x00\x00\x18ftypavif\x00\x00", "avif"),
        (b"II*\x00\x08\x00\x00\x00", "tiff"),
        (b"%PDF-1.7\n", None),
        (b"hello world", None),
    ],
)
def test_detect_format_uses_magic_bytes(head, fmt):
    assert tool.detect_format(head) == fmt


# --- the dev failure: PNG named .jpg ---------------------------------------------------------


def test_inspect_reports_png_even_when_caller_thinks_it_is_jpeg():
    status, body = _call("/inspect", {"source": {"base64": _b64(_png())}})
    assert status == 200
    assert body == {"detectedFormat": "png", "width": 3000, "height": 2000, "bytes": body["bytes"]}


def test_resize_turns_a_big_png_into_jpeg_analysis_copy_and_thumbnail():
    status, body = _call(
        "/resize", {"source": {"base64": _b64(_png())}, "outputs": [ANALYSIS, THUMB]}
    )
    assert status == 200
    assert body["detectedFormat"] == "png"
    analysis, thumb = body["outputs"]
    assert (analysis["width"], analysis["height"]) == (2048, 1365)
    assert max(thumb["width"], thumb["height"]) == 512
    for out, limit in ((analysis, 1_500_000), (thumb, 200_000)):
        data = base64.b64decode(out["base64"])
        assert tool.detect_format(data) == "jpeg"
        assert len(data) == out["bytes"] <= limit


def test_resize_never_upscales_a_small_image():
    status, body = _call(
        "/resize", {"source": {"base64": _b64(_png((800, 600)))}, "outputs": [ANALYSIS]}
    )
    assert status == 200
    assert (body["outputs"][0]["width"], body["outputs"][0]["height"]) == (800, 600)


def test_resize_fits_hard_to_compress_photos_under_max_bytes():
    data = _jpeg(_noise((2600, 2000)), quality=85)
    assert len(data) < tool.MAX_SOURCE_BYTES  # a valid upload, just very hard to compress
    status, body = _call("/resize", {"source": {"base64": _b64(data)}, "outputs": [ANALYSIS]})
    assert status == 200
    out = body["outputs"][0]
    assert out["bytes"] <= 1_500_000
    assert max(out["width"], out["height"]) <= 2048
    assert out["quality"] in tool.JPEG_QUALITIES


# --- orientation, transparency, metadata ------------------------------------------------------


def _with_orientation(img: Image.Image, orientation: int, gps: bool = False) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation  # Orientation
    if gps:
        exif[0x8825] = {2: (19.0, 57.0, 18.0)}  # GPSInfo latitude
    return _jpeg(img, exif=exif.tobytes())


def test_exif_orientation_is_applied():
    # Stored landscape 400x200 with "rotate 90 CW" -> displayed portrait 200x400.
    data = _with_orientation(Image.new("RGB", (400, 200), (10, 10, 10)), orientation=6)
    status, body = _call("/inspect", {"source": {"base64": _b64(data)}})
    assert (body["width"], body["height"]) == (200, 400)
    status, body = _call("/resize", {"source": {"base64": _b64(data)}, "outputs": [ANALYSIS]})
    assert (body["outputs"][0]["width"], body["outputs"][0]["height"]) == (200, 400)


def test_metadata_including_gps_is_stripped_by_default():
    data = _with_orientation(Image.new("RGB", (400, 200)), orientation=1, gps=True)
    _, body = _call("/resize", {"source": {"base64": _b64(data)}, "outputs": [ANALYSIS]})
    out = Image.open(io.BytesIO(base64.b64decode(body["outputs"][0]["base64"])))
    assert "exif" not in out.info


def test_keep_metadata_preserves_exif_when_asked():
    data = _with_orientation(Image.new("RGB", (400, 200)), orientation=1, gps=True)
    spec = dict(ANALYSIS, keepMetadata=True)
    _, body = _call("/resize", {"source": {"base64": _b64(data)}, "outputs": [spec]})
    out = Image.open(io.BytesIO(base64.b64decode(body["outputs"][0]["base64"])))
    assert "exif" in out.info


def test_transparent_png_becomes_white_not_black():
    data = _png((100, 100), mode="RGBA", color=(0, 0, 0, 0))
    _, body = _call("/resize", {"source": {"base64": _b64(data)}, "outputs": [ANALYSIS]})
    out = Image.open(io.BytesIO(base64.b64decode(body["outputs"][0]["base64"]))).convert("RGB")
    assert min(out.getpixel((50, 50))) > 240


# --- limits and bad input --------------------------------------------------------------------


def test_source_over_10mb_is_rejected():
    big = b"\xff\xd8\xff" + b"\x00" * (tool.MAX_SOURCE_BYTES + 10)
    status, body = _call("/resize", {"source": {"base64": _b64(big)}, "outputs": [ANALYSIS]})
    assert status == 413
    assert "10 MB" in body["message"]


def test_url_source_over_10mb_is_rejected(monkeypatch):
    def fake_get(url):
        raise tool.ToolError(413, "source image is larger than 10 MB")

    monkeypatch.setattr(tool, "_http_get", fake_get)
    status, _ = _call("/inspect", {"source": {"url": "https://bucket/key?sig"}})
    assert status == 413


def test_decompression_bomb_is_rejected():
    data = _png((8000, 8000), mode="L", color=0)  # 64 MP, tiny file
    status, body = _call("/resize", {"source": {"base64": _b64(data)}, "outputs": [ANALYSIS]})
    assert status == 422
    assert "too large" in body["message"]


def test_not_an_image_is_rejected():
    status, body = _call("/inspect", {"source": {"base64": _b64(b"%PDF-1.7 not a photo")}})
    assert status == 422
    assert "not an image" in body["message"]


def test_corrupt_image_is_rejected():
    truncated = _png()[:200]
    status, body = _call("/resize", {"source": {"base64": _b64(truncated)}, "outputs": [ANALYSIS]})
    assert status == 422
    assert "corrupt" in body["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"outputs": [ANALYSIS]},  # no source
        {"source": {"base64": "!!notbase64!!"}, "outputs": [ANALYSIS]},
        {"source": {"base64": "AAAA"}, "outputs": []},
        {"source": {"base64": "AAAA"}, "outputs": [ANALYSIS] * 5},
        {"source": {"base64": "AAAA"}, "outputs": [{"maxLongEdge": 10, "maxBytes": 1_000_000}]},
        {"source": {"base64": "AAAA"}, "outputs": [{"maxLongEdge": 2048, "maxBytes": 10}]},
        {"source": {"base64": "AAAA"}, "outputs": [dict(ANALYSIS, destinationUrl="http://x")]},
    ],
)
def test_invalid_requests_are_400(body):
    status, _ = _call("/resize", body)
    assert status == 400


def test_unknown_route_and_bad_json():
    assert tool.handler(_event("/rotate", {}), None)["statusCode"] == 404
    assert tool.handler(_event("/resize", {}, method="GET"), None)["statusCode"] == 404
    bad = {"rawPath": "/inspect", "requestContext": {"http": {"method": "POST"}}, "body": "{"}
    assert tool.handler(bad, None)["statusCode"] == 400


# --- presigned URL I/O ------------------------------------------------------------------------


def test_outputs_are_put_to_destination_urls_and_not_returned_inline(monkeypatch):
    puts = []
    monkeypatch.setattr(tool, "_http_get", lambda url: _png())
    monkeypatch.setattr(tool, "_http_put", lambda url, data: puts.append((url, data)))
    body = {
        "source": {"url": "https://bucket/uploads/g/m/photo.jpg?sig"},
        "outputs": [
            dict(ANALYSIS, destinationUrl="https://bucket/derived/g/m/analysis.jpg?sig"),
            dict(THUMB, destinationUrl="https://bucket/derived/g/m/thumb.jpg?sig"),
        ],
    }
    status, result = _call("/resize", body)
    assert status == 200
    assert [u for u, _ in puts] == [
        "https://bucket/derived/g/m/analysis.jpg?sig",
        "https://bucket/derived/g/m/thumb.jpg?sig",
    ]
    assert all(tool.detect_format(d) == "jpeg" for _, d in puts)
    assert all(o["stored"] and "base64" not in o for o in result["outputs"])


def test_storage_failure_is_502_and_urls_are_not_echoed(monkeypatch):
    monkeypatch.setattr(tool, "_http_get", lambda url: _png((300, 200)))

    def failing_put(url, data):
        raise tool.ToolError(502, "could not store an output (HTTP 403)")

    monkeypatch.setattr(tool, "_http_put", failing_put)
    body = {
        "source": {"url": "https://bucket/k?X-Amz-Signature=secret"},
        "outputs": [dict(ANALYSIS, destinationUrl="https://bucket/d?X-Amz-Signature=secret2")],
    }
    status, result = _call("/resize", body)
    assert status == 502
    assert "secret" not in json.dumps(result)
