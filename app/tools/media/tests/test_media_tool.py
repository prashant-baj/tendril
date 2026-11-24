"""Media Tool (ADR-0038 §7): real ffmpeg on tiny generated videos; the download is stubbed."""

import base64
import io
import json
import shutil
import subprocess

import imageio_ffmpeg
import media_tool as tool
import pytest
from PIL import Image

THUMB = {"name": "thumb", "maxLongEdge": 512, "maxBytes": 200_000}


def _video(path, seconds=3, size="640x360", fmt="mp4"):
    subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size={size}:rate=10",
            "-pix_fmt",
            "yuv420p",
            "-f",
            fmt,
            "-y",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def serve(monkeypatch):
    """Points the tool's download at a local file instead of a presigned URL."""

    def use(path):
        def fake_download(url, dest):
            shutil.copyfile(path, dest)
            return path.stat().st_size

        monkeypatch.setattr(tool, "_download", fake_download)

    return use


def _call(body):
    resp = tool.handler(
        {
            "rawPath": "/poster",
            "requestContext": {"http": {"method": "POST"}},
            "body": json.dumps(body),
        },
        None,
    )
    return resp["statusCode"], json.loads(resp["body"])


def test_detect_format_reads_the_ftyp_box():
    assert tool.detect_format(b"\x00\x00\x00\x18ftypisom0000") == "mp4"
    assert tool.detect_format(b"\x00\x00\x00\x14ftypqt  0000") == "mov"
    assert tool.detect_format(b"\xff\xd8\xff\xe0JFIF\x00\x00\x00\x00") is None


def test_poster_frame_and_video_facts(tmp_path, serve):
    serve(_video(tmp_path / "clip.mp4"))
    status, body = _call({"source": {"url": "https://example/v"}, "outputs": [THUMB]})
    assert status == 200
    assert body["detectedFormat"] == "mp4"
    assert 2.5 <= body["durationSeconds"] <= 3.5
    assert (body["width"], body["height"]) == (640, 360)
    out = body["outputs"][0]
    frame = Image.open(io.BytesIO(base64.b64decode(out["base64"])))
    assert frame.format == "JPEG" and max(frame.size) <= 512


def test_quicktime_is_accepted(tmp_path, serve):
    serve(_video(tmp_path / "clip.mov", fmt="mov"))
    status, body = _call({"source": {"url": "https://example/v"}, "outputs": [THUMB]})
    assert status == 200 and body["detectedFormat"] == "mov"


def test_videos_over_the_limit_are_refused(tmp_path, serve):
    serve(_video(tmp_path / "long.mp4", seconds=3))
    status, body = _call(
        {"source": {"url": "https://example/v"}, "outputs": [THUMB], "maxDurationSeconds": 2}
    )
    assert status == 422 and "longer than 2 seconds" in body["message"]


def test_a_photo_is_not_a_video(tmp_path, serve):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (50, 50)).save(photo, "JPEG")
    serve(photo)
    status, body = _call({"source": {"url": "https://example/v"}, "outputs": [THUMB]})
    assert status == 422 and "MP4 or MOV" in body["message"]


@pytest.mark.parametrize(
    "body",
    [
        {"source": {"url": "https://x"}, "outputs": []},
        {"source": {"url": "https://x"}, "outputs": [{"maxLongEdge": 5, "maxBytes": 100000}]},
        {"source": "nope", "outputs": [THUMB]},
        {"source": {"url": "https://x"}, "outputs": [dict(THUMB, destinationUrl="http://x")]},
    ],
)
def test_invalid_requests_are_400(body):
    assert _call(body)[0] == 400


def test_download_refuses_non_https():
    with pytest.raises(tool.ToolError) as e:
        tool._download("http://example/v", "x")
    assert e.value.status == 400
