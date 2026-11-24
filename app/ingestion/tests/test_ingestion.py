"""Unit tests for the Ingestion Lambda (ADR-0033). No AWS calls: S3 signing, the Image Tool invoke
and the DynamoDB update are faked."""

import io
import json

import ingestion
import pytest

G, M = "g-1", "m-1"
UPLOAD_KEY = f"uploads/{G}/{M}/2026-11-10_plant10.jpg"


class FakeS3:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, method, Params, ExpiresIn):
        self.calls.append((method, Params, ExpiresIn))
        return f"https://bucket/{Params['Key']}?{method}"


class FakeLambda:
    def __init__(self, status=200, body=None, function_error=None, raise_on_invoke=None):
        self.status, self.body = status, body
        self.function_error, self.raise_on_invoke = function_error, raise_on_invoke
        self.events = []

    def invoke(self, FunctionName, Payload):
        if self.raise_on_invoke:
            raise self.raise_on_invoke
        self.events.append((FunctionName, json.loads(Payload)))
        if self.function_error:
            payload = {"errorType": "Runtime.ExitError"}
            return {
                "Payload": io.BytesIO(json.dumps(payload).encode()),
                "FunctionError": "Unhandled",
            }
        payload = {"statusCode": self.status, "body": json.dumps(self.body or {})}
        return {"Payload": io.BytesIO(json.dumps(payload).encode())}


class FakeClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeTable:
    def __init__(self, missing=False):
        self.missing = missing
        self.updates = []

    def update_item(self, **kwargs):
        if self.missing:
            raise FakeClientError("ConditionalCheckFailedException")
        self.updates.append(kwargs)


TOOL_OK = {
    "detectedFormat": "png",
    "width": 3000,
    "height": 2000,
    "bytes": 4_629_020,
    "outputs": [
        {"name": "analysis", "format": "jpeg", "width": 2048, "height": 1365, "bytes": 612_000},
        {"name": "thumb", "format": "jpeg", "width": 512, "height": 341, "bytes": 41_000},
    ],
}


@pytest.fixture
def fakes(monkeypatch):
    s3, lam, table = FakeS3(), FakeLambda(body=TOOL_OK), FakeTable()
    monkeypatch.setattr(ingestion, "_s3", s3)
    monkeypatch.setattr(ingestion, "_lambda", lam)
    monkeypatch.setattr(ingestion, "_table", table)
    monkeypatch.setattr(ingestion, "IMAGE_TOOL_FUNCTION_NAME", "tendril-dev-tool-image")
    monkeypatch.setattr(ingestion, "MEDIA_BUCKET_NAME", "tendril-dev-media")
    return s3, lam, table


def _s3_event(key, size=4_629_020):
    return {
        "Records": [
            {"s3": {"bucket": {"name": "tendril-dev-media"}, "object": {"key": key, "size": size}}}
        ]
    }


def _values(update):
    return update["ExpressionAttributeValues"]


@pytest.mark.parametrize(
    ("key", "ids"),
    [
        (UPLOAD_KEY, (G, M)),
        (f"{G}/{M}/legacy.jpg", (G, M)),  # pre-ADR-0033 layout, for the backfill
        (f"derived/{G}/{M}/analysis.jpg", None),  # never our own output
        ("uploads/only-two/parts", None),
        ("stray.jpg", None),
    ],
)
def test_parse_key(key, ids):
    assert ingestion.parse_key(key) == ids


def test_upload_is_processed_into_analysis_copy_and_thumbnail(fakes):
    s3, lam, table = fakes
    assert ingestion.handler(_s3_event(UPLOAD_KEY), None) == {"outcomes": {"ready": 1}}

    # Signs a GET for the original and image/jpeg PUTs for both derived keys.
    signed = [(m, p["Key"], p.get("ContentType")) for m, p, _ in s3.calls]
    assert signed == [
        ("get_object", UPLOAD_KEY, None),
        ("put_object", f"derived/{G}/{M}/analysis.jpg", "image/jpeg"),
        ("put_object", f"derived/{G}/{M}/thumb.jpg", "image/jpeg"),
    ]
    # One tool call, Function-URL-shaped, with the ADR-0033 sizes.
    ((fn, event),) = lam.events
    assert fn == "tendril-dev-tool-image"
    assert event["rawPath"] == "/resize"
    outputs = json.loads(event["body"])["outputs"]
    assert [(o["name"], o["maxLongEdge"], o["maxBytes"]) for o in outputs] == [
        ("analysis", 2048, 1_500_000),
        ("thumb", 512, 200_000),
    ]
    # Media record updated conditionally with the real format and the derived keys.
    (update,) = table.updates
    assert update["Key"] == {"pk": f"GARDEN#{G}", "sk": f"MEDIA#{M}"}
    assert update["ConditionExpression"] == "attribute_exists(pk)"
    v = _values(update)
    assert v[":processing_status"] == "ready"
    assert v[":detected_format"] == "png"
    assert v[":analysis_s3_key"] == f"derived/{G}/{M}/analysis.jpg"
    assert v[":thumb_s3_key"] == f"derived/{G}/{M}/thumb.jpg"
    assert "REMOVE #processing_error" in update["UpdateExpression"]


def test_url_encoded_keys_are_decoded(fakes):
    s3, _, _ = fakes
    ingestion.handler(_s3_event(f"uploads/{G}/{M}/my+photo%281%29.jpg"), None)
    assert s3.calls[0][1]["Key"] == f"uploads/{G}/{M}/my photo(1).jpg"


def test_over_10mb_is_failed_without_calling_the_tool(fakes):
    _, lam, table = fakes
    result = ingestion.handler(_s3_event(UPLOAD_KEY, size=11 * 1024 * 1024), None)
    assert result == {"outcomes": {"failed": 1}}
    assert lam.events == []
    v = _values(table.updates[0])
    assert v[":processing_status"] == "failed"
    assert "10 MB" in v[":processing_error"]


@pytest.mark.parametrize("status", [400, 413, 422])
def test_permanent_tool_rejections_mark_the_photo_failed(fakes, status):
    _, lam, table = fakes
    lam.status, lam.body = status, {"message": "not an image (unrecognised file signature)"}
    assert ingestion.handler(_s3_event(UPLOAD_KEY), None) == {"outcomes": {"failed": 1}}
    v = _values(table.updates[0])
    assert v[":processing_status"] == "failed"
    assert v[":processing_error"] == "not an image (unrecognised file signature)"


@pytest.mark.parametrize(
    "lam",
    [
        FakeLambda(status=502, body={"message": "could not store an output"}),
        FakeLambda(function_error=True),
        FakeLambda(raise_on_invoke=RuntimeError("throttled")),
    ],
)
def test_transient_failures_raise_for_retry(fakes, monkeypatch, lam):
    _, _, table = fakes
    monkeypatch.setattr(ingestion, "_lambda", lam)
    with pytest.raises(ingestion.TransientError):
        ingestion.handler(_s3_event(UPLOAD_KEY), None)
    assert table.updates == []  # stays pending; S3 retries, then the failure destination


def test_missing_media_record_is_skipped_not_created(fakes, monkeypatch):
    monkeypatch.setattr(ingestion, "_table", FakeTable(missing=True))
    assert ingestion.handler(_s3_event(UPLOAD_KEY), None) == {"outcomes": {"skipped": 1}}


def test_derived_objects_are_ignored(fakes):
    _, lam, table = fakes
    result = ingestion.handler(_s3_event(f"derived/{G}/{M}/analysis.jpg"), None)
    assert result == {"outcomes": {"skipped": 1}}
    assert lam.events == [] and table.updates == []


def test_capture_time_is_recorded_when_the_tool_reports_one(fakes, monkeypatch):
    _, _, table = fakes
    monkeypatch.setattr(
        ingestion, "_lambda", FakeLambda(body=dict(TOOL_OK, takenAt="2026-08-02T07:30:15"))
    )
    ingestion.handler(_s3_event(UPLOAD_KEY), None)
    (update,) = table.updates
    assert _values(update)[":taken_at"] == "2026-08-02T07:30:15"


def test_no_capture_time_means_no_taken_at_field(fakes):
    _, _, table = fakes
    ingestion.handler(_s3_event(UPLOAD_KEY), None)
    (update,) = table.updates
    assert ":taken_at" not in _values(update)


VIDEO_KEY = f"uploads/{G}/{M}/spray.MP4"
POSTER_OK = {
    "detectedFormat": "mp4",
    "bytes": 40_000_000,
    "durationSeconds": 14.2,
    "width": 1080,
    "height": 1920,
    "outputs": [
        {"name": "analysis", "format": "jpeg", "width": 1152, "height": 2048, "bytes": 380_000},
        {"name": "thumb", "format": "jpeg", "width": 288, "height": 512, "bytes": 30_000},
    ],
}


def test_videos_go_to_the_media_tool_for_a_poster_frame(fakes, monkeypatch):
    _, _, table = fakes
    lam = FakeLambda(body=POSTER_OK)
    monkeypatch.setattr(ingestion, "_lambda", lam)
    monkeypatch.setattr(ingestion, "MEDIA_TOOL_FUNCTION_NAME", "tendril-dev-tool-media")
    assert ingestion.handler(_s3_event(VIDEO_KEY, size=40_000_000), None) == {
        "outcomes": {"ready": 1}
    }
    ((fn, event),) = lam.events
    assert fn == "tendril-dev-tool-media" and event["rawPath"] == "/poster"
    assert json.loads(event["body"])["maxDurationSeconds"] == 60
    (update,) = table.updates
    v = _values(update)
    assert v[":media_kind"] == "video" and v[":duration_seconds"] == 14.2
    assert v[":thumb_s3_key"] == f"derived/{G}/{M}/thumb.jpg"


def test_videos_may_be_up_to_100_mb(fakes):
    _, lam, table = fakes
    assert ingestion.handler(_s3_event(VIDEO_KEY, size=101 * 1024 * 1024), None) == {
        "outcomes": {"failed": 1}
    }
    assert lam.events == []
    assert _values(table.updates[0])[":processing_error"] == "larger than 100 MB"


def test_a_video_over_60_seconds_is_marked_failed(fakes, monkeypatch):
    _, _, table = fakes
    monkeypatch.setattr(
        ingestion,
        "_lambda",
        FakeLambda(status=422, body={"message": "video is longer than 60 seconds"}),
    )
    assert ingestion.handler(_s3_event(VIDEO_KEY, size=1000), None) == {"outcomes": {"failed": 1}}
    assert "longer than 60 seconds" in _values(table.updates[0])[":processing_error"]
