"""Unit tests for the garden Client API handler (OB-01, OB-02, WS-03).

No AWS calls — DynamoDB access is monkeypatched via fake table/client objects, following the
same mocked-boto3-client convention as agents/hello_agent/tests/test_agent.py.

Note: `create_garden` and `get_garden` intentionally use *separate* fakes (`_client` vs.
`_table`) mirroring the real code's split between a plain low-level client (`_get_client()`,
used for `transact_write_items`) and the resource `Table` (`_get_table()`, used for
`get_item`) — a real production bug (`_get_table().meta.client` double-serializing
already-AttributeValue-shaped items into `{"M": {"S": {...}}}`) came from conflating the two,
which a hand-rolled fake mocking only the happy-path shape could not have caught. These fakes
verify request *shape*, not boto3's actual (de)serialization behavior — that gap is exactly
what let the bug through unit tests in the first place; it was only found via a live
invocation against real DynamoDB.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import garden_handler as handler
import pytest


class FakeClient:
    def __init__(
        self,
        raise_on_transact: Exception | None = None,
        transact_errors: list | None = None,
        batch_get_responses: list | None = None,
    ):
        self.raise_on_transact = raise_on_transact
        # Per-call queue: entry i is raised on the i-th transact call (None = succeed) — lets a
        # bulk test fail some stake numbers' transactions but not others.
        self.transact_errors = list(transact_errors) if transact_errors is not None else None
        self.calls: list[list[dict]] = []
        self._batch_get_responses = list(batch_get_responses or [])
        self.batch_get_calls: list[dict] = []

    def transact_write_items(self, TransactItems):
        if self.raise_on_transact:
            raise self.raise_on_transact
        if self.transact_errors:
            err = self.transact_errors.pop(0)
            if err is not None:
                raise err
        self.calls.append(TransactItems)

    def batch_get_item(self, RequestItems):
        self.batch_get_calls.append(RequestItems)
        if self._batch_get_responses:
            return self._batch_get_responses.pop(0)
        return {"Responses": {}}


class FakeClientError(Exception):
    """Mimics botocore's ClientError just enough: a `.response` dict with an Error code and,
    for a cancelled transaction, per-item CancellationReasons."""

    def __init__(self, code: str, reasons: list[str] | None = None):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}
        if reasons is not None:
            self.response["CancellationReasons"] = [{"Code": r} for r in reasons]


def _stake_conflict(n_items: int = 2, stake_index: int = 1) -> FakeClientError:
    reasons = ["None"] * n_items
    reasons[stake_index] = "ConditionalCheckFailed"
    return FakeClientError("TransactionCanceledException", reasons)


def _media_batch_response(*pairs: tuple[str, str]) -> dict:
    """A low-level BatchGetItem response (raw AttributeValues) for (media_id, s3_key) pairs."""
    return {
        "Responses": {
            handler.APP_TABLE_NAME: [
                {"media_id": {"S": mid}, "s3_key": {"S": key}} for mid, key in pairs
            ]
        }
    }


class FakeTable:
    def __init__(
        self,
        get_item_response=None,
        get_item_responses=None,
        raise_on_get=None,
        raise_on_put=None,
        query_response=None,
        query_responses=None,
        raise_on_query=None,
        raise_on_delete=None,
        raise_on_update=None,
    ):
        self._get_item_response = get_item_response or {}
        # sk -> response, checked before falling back to the flat default above — lets a single
        # test model several distinct records (Goal/Plan/Media) behind one FakeTable, mirroring
        # the orchestrator tests' equivalent `responses_by_sk`.
        self._get_item_responses = get_item_responses or {}
        self.raise_on_get = raise_on_get
        self.raise_on_put = raise_on_put
        self.put_calls: list[dict] = []
        self._query_response = query_response or {"Items": []}
        # A queue consumed in call order (get_goal_detail queries tasks then messages, each
        # needing a different result) — falls back to the single flat response above when not
        # given, so every existing single-query test keeps working unchanged.
        self._query_responses = list(query_responses) if query_responses is not None else None
        self.raise_on_query = raise_on_query
        self.raise_on_delete = raise_on_delete
        self.raise_on_update = raise_on_update
        self.delete_calls: list[dict] = []
        self.update_calls: list[dict] = []
        self.query_calls: list[dict] = []

    def get_item(self, Key):
        if self.raise_on_get:
            raise self.raise_on_get
        if Key.get("sk") in self._get_item_responses:
            return self._get_item_responses[Key["sk"]]
        return self._get_item_response

    def put_item(self, Item):
        if self.raise_on_put:
            raise self.raise_on_put
        self.put_calls.append(Item)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        if self.raise_on_query:
            raise self.raise_on_query
        if self._query_responses is not None:
            index = len(self.query_calls) - 1
            if index < len(self._query_responses):
                return self._query_responses[index]
            return {"Items": []}
        return self._query_response

    def delete_item(self, Key):
        if self.raise_on_delete:
            raise self.raise_on_delete
        self.delete_calls.append(Key)

    def update_item(self, **kwargs):
        if self.raise_on_update:
            raise self.raise_on_update
        self.update_calls.append(kwargs)


class FakeS3:
    def __init__(self, raise_on_presign: Exception | None = None):
        self.raise_on_presign = raise_on_presign
        self.presign_calls: list[dict] = []

    def generate_presigned_url(self, operation, Params, ExpiresIn):
        if self.raise_on_presign:
            raise self.raise_on_presign
        self.presign_calls.append(
            {"operation": operation, "Params": Params, "ExpiresIn": ExpiresIn}
        )
        return f"https://example-bucket.s3.amazonaws.com/{Params['Key']}?presigned=1"


class FakeEvents:
    def __init__(self, raise_on_put: Exception | None = None):
        self.raise_on_put = raise_on_put
        self.put_calls: list[list[dict]] = []

    def put_events(self, Entries):
        if self.raise_on_put:
            raise self.raise_on_put
        self.put_calls.append(Entries)


def _event(method, resource, *, body=None, headers=None, path_params=None):
    return {
        "httpMethod": method,
        "resource": resource,
        "body": json.dumps(body) if body is not None else None,
        "headers": headers or {},
        "pathParameters": path_params,
    }


# --- create_garden -----------------------------------------------------------


def test_create_garden_happy_path(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)

    event = _event(
        "POST",
        "/gardens",
        body={"name": "Balcony Kitchen Garden", "geolocation": "Pune", "vision": "fresh veggies"},
        headers={"X-User-Id": "user-1"},
    )
    resp = handler.create_garden(event)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert "gardenId" in body and body["gardenId"]

    # exactly one transaction, with both the canonical + ownership-index Put items
    assert len(fake_client.calls) == 1
    items = fake_client.calls[0]
    assert len(items) == 2
    puts = [i["Put"]["Item"] for i in items]
    pks = {p["pk"]["S"] for p in puts}
    assert pks == {f"GARDEN#{body['gardenId']}", "USER#user-1"}


def test_create_garden_with_client_supplied_id_and_media(monkeypatch):
    # The only entity whose id can come from the frontend — needed because a garden photo has
    # to be uploaded (createMediaUpload needs an existing gardenId) before the garden itself
    # exists.
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)

    event = _event(
        "POST",
        "/gardens",
        body={
            "name": "Farmhouse Garden",
            "geolocation": "Nashik",
            "gardenId": "client-g-1",
            "mediaId": "media-1",
        },
        headers={"X-User-Id": "user-1"},
    )
    resp = handler.create_garden(event)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert body["gardenId"] == "client-g-1"

    items = fake_client.calls[0]
    garden_put = next(i["Put"] for i in items if i["Put"]["Item"]["pk"]["S"] == "GARDEN#client-g-1")
    assert garden_put["Item"]["media_id"]["S"] == "media-1"
    assert garden_put["ConditionExpression"] == "attribute_not_exists(pk)"


def test_create_garden_gardenid_must_be_a_string():
    event = _event(
        "POST",
        "/gardens",
        body={"name": "G", "geolocation": "Pune", "gardenId": 123},
        headers={"X-User-Id": "u"},
    )
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 400


def test_create_garden_mediaid_must_be_a_string():
    event = _event(
        "POST",
        "/gardens",
        body={"name": "G", "geolocation": "Pune", "mediaId": 123},
        headers={"X-User-Id": "u"},
    )
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 400


def test_create_garden_missing_user_id_header(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    event = _event("POST", "/gardens", body={"name": "G", "geolocation": "Pune"}, headers={})
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 400
    assert "X-User-Id" in json.loads(resp["body"])["message"]


def test_create_garden_missing_name(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    event = _event("POST", "/gardens", body={"geolocation": "Pune"}, headers={"X-User-Id": "u"})
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 400


def test_create_garden_missing_geolocation(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    event = _event("POST", "/gardens", body={"name": "G"}, headers={"X-User-Id": "u"})
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 400


def test_create_garden_malformed_json_body(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    event = _event("POST", "/gardens", headers={"X-User-Id": "u"})
    event["body"] = "{not json"
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 400


def test_create_garden_header_lookup_is_case_insensitive(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    event = _event(
        "POST",
        "/gardens",
        body={"name": "G", "geolocation": "Pune"},
        headers={"x-user-id": "user-1"},
    )
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 201


def test_create_garden_dynamo_failure_returns_500(monkeypatch):
    fake_client = FakeClient(raise_on_transact=RuntimeError("boom"))
    monkeypatch.setattr(handler, "_client", fake_client)
    event = _event(
        "POST",
        "/gardens",
        body={"name": "G", "geolocation": "Pune"},
        headers={"X-User-Id": "u"},
    )
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 500


# --- list_gardens (multi-garden switcher) ---------------------------------------


def test_list_gardens_happy_path(monkeypatch):
    fake_table = FakeTable(
        query_response={
            "Items": [
                {"garden_id": "g1", "name": "Balcony Kitchen Garden"},
                {"garden_id": "g2", "name": "Terrace Garden"},
            ]
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event("GET", "/gardens", headers={"X-User-Id": "user-1"})
    resp = handler.list_gardens(event)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == [
        {"gardenId": "g1", "name": "Balcony Kitchen Garden"},
        {"gardenId": "g2", "name": "Terrace Garden"},
    ]
    assert fake_table.query_calls[0]["KeyConditionExpression"] is not None


def test_list_gardens_empty(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens", headers={"X-User-Id": "user-1"})
    resp = handler.list_gardens(event)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == []


def test_list_gardens_missing_user_id_header():
    event = _event("GET", "/gardens", headers={})
    resp = handler.list_gardens(event)
    assert resp["statusCode"] == 400


def test_list_gardens_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_query=RuntimeError("boom")))
    event = _event("GET", "/gardens", headers={"X-User-Id": "user-1"})
    resp = handler.list_gardens(event)
    assert resp["statusCode"] == 500


def test_handler_routes_list_gardens(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens", headers={"X-User-Id": "user-1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


# --- get_garden ----------------------------------------------------------------


def test_get_garden_found(monkeypatch):
    fake_table = FakeTable(
        get_item_response={
            "Item": {
                "garden_id": "g1",
                "name": "Balcony Kitchen Garden",
                "geolocation": "Pune",
                "vision": "fresh veggies",
                "owner_user_id": "user-1",
                "created_at": "2026-09-12T00:00:00+00:00",
            }
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event("GET", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.get_garden(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {
        "gardenId": "g1",
        "name": "Balcony Kitchen Garden",
        "geolocation": "Pune",
        "vision": "fresh veggies",
        "ownerUserId": "user-1",
        "createdAt": "2026-09-12T00:00:00+00:00",
    }


def test_get_garden_resolves_photo_url_when_media_id_set(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "METADATA": {
                "Item": {
                    "garden_id": "g1",
                    "name": "Farmhouse Garden",
                    "geolocation": "Nashik",
                    "owner_user_id": "user-1",
                    "created_at": "2026-09-13T00:00:00+00:00",
                    "media_id": "media-1",
                }
            },
            "MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/banner.jpg"}},
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")

    event = _event("GET", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.get_garden(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "g1/media-1/banner.jpg" in body["photoUrl"]


def test_get_garden_omits_photo_url_when_no_media(monkeypatch):
    fake_table = FakeTable(
        get_item_response={
            "Item": {
                "garden_id": "g1",
                "name": "Balcony Kitchen Garden",
                "geolocation": "Pune",
                "owner_user_id": "user-1",
                "created_at": "2026-09-12T00:00:00+00:00",
            }
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event("GET", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.get_garden(event)
    assert "photoUrl" not in json.loads(resp["body"])


def test_get_garden_not_found(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    event = _event("GET", "/gardens/{gardenId}", path_params={"gardenId": "missing"})
    resp = handler.get_garden(event)
    assert resp["statusCode"] == 404


def test_get_garden_missing_path_param(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event("GET", "/gardens/{gardenId}", path_params=None)
    resp = handler.get_garden(event)
    assert resp["statusCode"] == 400


def test_get_garden_dynamo_failure_returns_500(monkeypatch):
    fake_table = FakeTable(raise_on_get=RuntimeError("boom"))
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event("GET", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.get_garden(event)
    assert resp["statusCode"] == 500


# --- get_garden_weather ----------------------------------------------------------


def test_get_garden_weather_happy_path(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "geolocation": "Pune, India"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_geocode", lambda query: (18.52, 73.86))
    monkeypatch.setattr(
        handler, "_fetch_forecast", lambda lat, lon: {"temperatureC": 29.4, "weatherCode": 1}
    )

    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.get_garden_weather(event)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"temperatureC": 29.4, "weatherCode": 1}


def test_get_garden_weather_garden_not_found(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "missing"})
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 404


def test_get_garden_weather_missing_path_param():
    event = _event("GET", "/gardens/{gardenId}/weather", path_params=None)
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 400


def test_get_garden_weather_geocode_found_nothing(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"garden_id": "g1", "geolocation": "Nowhereland"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_geocode", lambda query: None)

    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 404


def test_get_garden_weather_geocode_failure_returns_502(monkeypatch):
    import urllib.error

    fake_table = FakeTable(get_item_response={"Item": {"garden_id": "g1", "geolocation": "Pune"}})
    monkeypatch.setattr(handler, "_table", fake_table)

    def boom(query):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(handler, "_geocode", boom)
    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 502


def test_get_garden_weather_forecast_failure_returns_502(monkeypatch):
    import urllib.error

    fake_table = FakeTable(get_item_response={"Item": {"garden_id": "g1", "geolocation": "Pune"}})
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_geocode", lambda query: (18.52, 73.86))

    def boom(lat, lon):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(handler, "_fetch_forecast", boom)
    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 502


def test_get_garden_weather_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_get=RuntimeError("boom")))
    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 500


def test_handler_routes_get_garden_weather(monkeypatch):
    fake_table = FakeTable(get_item_response={"Item": {"garden_id": "g1", "geolocation": "Pune"}})
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_geocode", lambda query: (18.52, 73.86))
    monkeypatch.setattr(
        handler, "_fetch_forecast", lambda lat, lon: {"temperatureC": 29.4, "weatherCode": 1}
    )
    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


# --- create_media_upload (OB-02) ------------------------------------------------


def test_create_media_upload_happy_path(monkeypatch):
    fake_s3 = FakeS3()
    fake_table = FakeTable()
    monkeypatch.setattr(handler, "_s3", fake_s3)
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")

    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg", "sizeBytes": 2_000_000},
        headers={"X-User-Id": "user-1"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_media_upload(event)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert "mediaId" in body and body["mediaId"]
    assert body["uploadUrl"].startswith("https://")

    # presigned PUT was requested against the right bucket/key/content-type
    assert len(fake_s3.presign_calls) == 1
    presign = fake_s3.presign_calls[0]
    assert presign["operation"] == "put_object"
    assert presign["Params"]["Bucket"] == "tendril-dev-media"
    assert presign["Params"]["ContentType"] == "image/jpeg"
    assert presign["Params"]["Key"] == f"uploads/g1/{body['mediaId']}/tomato.jpg"

    # the Media record was written immediately (data-architecture.md §4)
    assert len(fake_table.put_calls) == 1
    media_item = fake_table.put_calls[0]
    assert media_item["pk"] == "GARDEN#g1"
    assert media_item["sk"] == f"MEDIA#{body['mediaId']}"
    assert media_item["s3_key"] == f"uploads/g1/{body['mediaId']}/tomato.jpg"
    assert media_item["processing_status"] == "pending"
    assert media_item["size_bytes"] == 2_000_000
    assert media_item["content_type"] == "image/jpeg"


def test_create_media_upload_missing_user_id_header(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg", "sizeBytes": 2_000_000},
        headers={},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_media_upload(event)
    assert resp["statusCode"] == 400


def test_create_media_upload_missing_garden_id(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg", "sizeBytes": 2_000_000},
        headers={"X-User-Id": "u"},
        path_params=None,
    )
    resp = handler.create_media_upload(event)
    assert resp["statusCode"] == 400


def test_create_media_upload_missing_content_type(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"fileName": "tomato.jpg"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_media_upload(event)
    assert resp["statusCode"] == 400


def test_create_media_upload_missing_file_name(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "sizeBytes": 2_000_000},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_media_upload(event)
    assert resp["statusCode"] == 400


def test_create_media_upload_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_s3", FakeS3(raise_on_presign=RuntimeError("boom")))
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg", "sizeBytes": 2_000_000},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_media_upload(event)
    assert resp["statusCode"] == 500


# --- create_plant (OB-02) -------------------------------------------------------


def test_create_plant_without_media(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato", "variety": "Pusa Ruby"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert "plantId" in body and body["plantId"]

    assert len(fake_table.put_calls) == 1
    plant_item = fake_table.put_calls[0]
    assert plant_item["pk"] == "GARDEN#g1"
    assert plant_item["sk"] == f"PLANT#{body['plantId']}"
    assert plant_item["species"] == "Tomato"
    assert plant_item["variety"] == "Pusa Ruby"


def test_create_plant_with_media_links_it_transactionally(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_table", FakeTable())

    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato", "mediaId": "media-1"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])

    assert len(fake_client.calls) == 1
    items = fake_client.calls[0]
    assert len(items) == 2
    put_item = next(i["Put"] for i in items if "Put" in i)["Item"]
    update_item = next(i["Update"] for i in items if "Update" in i)
    assert put_item["sk"]["S"] == f"PLANT#{body['plantId']}"
    assert put_item["media_id"]["S"] == "media-1"
    assert update_item["Key"]["sk"]["S"] == "MEDIA#media-1"
    assert update_item["ExpressionAttributeValues"][":pid"]["S"] == body["plantId"]


def test_create_plant_with_media_response_includes_photo_url(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(get_item_response={"Item": {"s3_key": "g1/media-1/leaf.jpg"}}),
    )
    monkeypatch.setattr(handler, "_s3", FakeS3())

    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato", "mediaId": "media-1"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)

    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])
    assert (
        body["photoUrl"]
        == "https://example-bucket.s3.amazonaws.com/g1/media-1/leaf.jpg?presigned=1"
    )


def test_create_plant_with_media_omits_photo_url_when_media_missing(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    monkeypatch.setattr(handler, "_table", FakeTable())  # no Item -> media not found

    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato", "mediaId": "media-1"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)

    assert resp["statusCode"] == 201
    assert "photoUrl" not in json.loads(resp["body"])


def test_create_plant_missing_species(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)
    assert resp["statusCode"] == 400


def test_create_plant_missing_user_id_header(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato"},
        headers={},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)
    assert resp["statusCode"] == 400


def test_create_plant_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_put=RuntimeError("boom")))
    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)
    assert resp["statusCode"] == 500


def test_create_plant_media_link_failure_returns_500(monkeypatch):
    monkeypatch.setattr(
        handler, "_client", FakeClient(raise_on_transact=RuntimeError("condition failed"))
    )
    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato", "mediaId": "missing-media"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_plant(event)
    assert resp["statusCode"] == 500


# --- list_plants -----------------------------------------------------------------


def test_list_plants_happy_path(monkeypatch):
    fake_table = FakeTable(
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "PLANT#p1",
                    "plant_id": "p1",
                    "species": "Tomato",
                    "variety": "Pusa Ruby",
                    "stage": "fruiting",
                },
                {
                    "pk": "GARDEN#g1",
                    "sk": "PLANT#p2",
                    "plant_id": "p2",
                    "species": "Chilli",
                    "stage": "new",
                },
            ]
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event("GET", "/gardens/{gardenId}/plants", path_params={"gardenId": "g1"})
    resp = handler.list_plants(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == [
        {"plantId": "p1", "species": "Tomato", "variety": "Pusa Ruby", "stage": "fruiting"},
        {"plantId": "p2", "species": "Chilli", "variety": "", "stage": "new"},
    ]


def test_list_plants_resolves_photo_url_for_plants_with_media(monkeypatch):
    fake_table = FakeTable(
        query_response={
            "Items": [
                {
                    "pk": "GARDEN#g1",
                    "sk": "PLANT#p1",
                    "plant_id": "p1",
                    "species": "Tomato",
                    "stage": "new",
                    "media_id": "media-1",
                },
                {
                    "pk": "GARDEN#g1",
                    "sk": "PLANT#p2",
                    "plant_id": "p2",
                    "species": "Chilli",
                    "stage": "new",
                },
            ]
        },
    )
    fake_client = FakeClient(
        batch_get_responses=[_media_batch_response(("media-1", "g1/media-1/leaf.jpg"))]
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_s3", FakeS3())

    event = _event("GET", "/gardens/{gardenId}/plants", path_params={"gardenId": "g1"})
    resp = handler.list_plants(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert (
        body[0]["photoUrl"]
        == "https://example-bucket.s3.amazonaws.com/g1/media-1/leaf.jpg?presigned=1"
    )
    assert "photoUrl" not in body[1]
    # ADR-0032: one BatchGetItem for every plant's photo, not one GetItem per plant.
    assert len(fake_client.batch_get_calls) == 1
    keys = fake_client.batch_get_calls[0][handler.APP_TABLE_NAME]["Keys"]
    assert keys == [{"pk": {"S": "GARDEN#g1"}, "sk": {"S": "MEDIA#media-1"}}]


def test_list_plants_empty(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/plants", path_params={"gardenId": "g1"})
    resp = handler.list_plants(event)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == []


def test_list_plants_missing_garden_id(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event("GET", "/gardens/{gardenId}/plants", path_params=None)
    resp = handler.list_plants(event)
    assert resp["statusCode"] == 400


def test_list_plants_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_query=RuntimeError("boom")))
    event = _event("GET", "/gardens/{gardenId}/plants", path_params={"gardenId": "g1"})
    resp = handler.list_plants(event)
    assert resp["statusCode"] == 500


# --- delete_plant ------------------------------------------------------------------


def test_delete_plant_happy_path(monkeypatch):
    fake_table = FakeTable()
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "DELETE",
        "/gardens/{gardenId}/plants/{plantId}",
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1", "plantId": "p1"},
    )
    resp = handler.delete_plant(event)

    assert resp["statusCode"] == 204
    assert len(fake_table.delete_calls) == 1
    assert fake_table.delete_calls[0] == {"pk": "GARDEN#g1", "sk": "PLANT#p1"}


def test_delete_plant_missing_user_id_header(monkeypatch):
    event = _event(
        "DELETE",
        "/gardens/{gardenId}/plants/{plantId}",
        headers={},
        path_params={"gardenId": "g1", "plantId": "p1"},
    )
    resp = handler.delete_plant(event)
    assert resp["statusCode"] == 400


def test_delete_plant_missing_path_params(monkeypatch):
    event = _event(
        "DELETE",
        "/gardens/{gardenId}/plants/{plantId}",
        headers={"X-User-Id": "u"},
        path_params=None,
    )
    resp = handler.delete_plant(event)
    assert resp["statusCode"] == 400


def test_delete_plant_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_delete=RuntimeError("boom")))
    event = _event(
        "DELETE",
        "/gardens/{gardenId}/plants/{plantId}",
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1", "plantId": "p1"},
    )
    resp = handler.delete_plant(event)
    assert resp["statusCode"] == 500


# --- create_goal (WS-03) --------------------------------------------------------


def test_create_goal_happy_path_without_media(monkeypatch):
    fake_table = FakeTable()
    fake_events = FakeEvents()
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_events", fake_events)

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "leaves turning yellow"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)

    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert "goalId" in body and body["goalId"]
    assert body["status"] == "Intake"

    # 1 Goal Put + 1 best-effort Event Put (Phase 6's goal.submitted activity entry)
    assert len(fake_table.put_calls) == 2
    goal_item = next(i for i in fake_table.put_calls if i["sk"].startswith("GOAL#"))
    assert goal_item["pk"] == "GARDEN#g1"
    assert goal_item["sk"] == f"GOAL#{body['goalId']}"
    assert goal_item["description"] == "leaves turning yellow"
    assert goal_item["status"] == "Intake"
    assert "media_ids" not in goal_item

    event_item = next(i for i in fake_table.put_calls if i["sk"].startswith("EVENT#"))
    assert event_item["type"] == "goal.submitted"
    assert event_item["payload"] == {
        "goalId": body["goalId"],
        "description": "leaves turning yellow",
    }

    # persisted before the event is published (order matters — see module docstring)
    assert len(fake_events.put_calls) == 1
    entries = fake_events.put_calls[0]
    assert len(entries) == 1
    assert entries[0]["Source"] == "tendril.client-api"
    assert entries[0]["DetailType"] == "goal.submitted"
    assert json.loads(entries[0]["Detail"]) == {"gardenId": "g1", "goalId": body["goalId"]}


def test_create_goal_happy_path_with_media_ids(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_events", FakeEvents())
    monkeypatch.setattr(handler, "_table", FakeTable())

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help", "mediaIds": ["media-1", "media-2"]},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)

    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])

    assert len(fake_client.calls) == 1
    items = fake_client.calls[0]
    assert len(items) == 3  # 1 Put (the goal) + 2 Update (one per media_id)
    put_item = next(i["Put"] for i in items if "Put" in i)["Item"]
    assert put_item["media_ids"]["L"] == [{"S": "media-1"}, {"S": "media-2"}]
    update_items = [i["Update"] for i in items if "Update" in i]
    updated_sks = {u["Key"]["sk"]["S"] for u in update_items}
    assert updated_sks == {"MEDIA#media-1", "MEDIA#media-2"}
    for u in update_items:
        # No plantId was given on this goal — media only learns goal_id, not plant_id.
        assert u["ExpressionAttributeValues"][":gid"]["S"] == body["goalId"]
        assert ":pid" not in u["ExpressionAttributeValues"]


def test_create_goal_with_plant_id_propagates_to_media(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_events", FakeEvents())
    monkeypatch.setattr(handler, "_table", FakeTable())

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help", "mediaIds": ["media-1"], "plantId": "plant-1"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)

    assert resp["statusCode"] == 202
    items = fake_client.calls[0]
    put_item = next(i["Put"] for i in items if "Put" in i)["Item"]
    assert put_item["plant_id"]["S"] == "plant-1"
    update_item = next(i["Update"] for i in items if "Update" in i)
    assert update_item["ExpressionAttributeValues"][":pid"]["S"] == "plant-1"


def test_create_goal_plant_id_must_be_a_string():
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help", "plantId": 123},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 400


def test_create_goal_missing_description(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 400


def test_create_goal_invalid_media_ids_type(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help", "mediaIds": "not-a-list"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 400


def test_create_goal_missing_user_id_header(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help"},
        headers={},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 400


def test_create_goal_missing_garden_id(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help"},
        headers={"X-User-Id": "u"},
        path_params=None,
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 400


def test_create_goal_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_put=RuntimeError("boom")))
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 500


def test_create_goal_eventbridge_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    monkeypatch.setattr(handler, "_events", FakeEvents(raise_on_put=RuntimeError("boom")))
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 500


# --- list_goals / get_goal_detail / post_goal_message / approve_plan (PA-01/PA-02/PA-03) ----


def test_list_goals_happy_path(monkeypatch):
    fake_table = FakeTable(
        query_response={
            "Items": [
                {"goal_id": "goal-1", "description": "leaves yellow", "status": "Intake"},
                {
                    "goal_id": "goal-2",
                    "description": "pests",
                    "type": "diagnosis",
                    "status": "PlanProposed",
                    "media_ids": ["m1"],
                },
            ]
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event("GET", "/gardens/{gardenId}/goals", path_params={"gardenId": "g1"})
    resp = handler.list_goals(event)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == [
        {
            "goalId": "goal-1",
            "description": "leaves yellow",
            "type": "diagnosis",
            "status": "Intake",
        },
        {
            "goalId": "goal-2",
            "description": "pests",
            "type": "diagnosis",
            "status": "PlanProposed",
            "mediaIds": ["m1"],
        },
    ]


def test_list_goals_missing_garden_id():
    event = _event("GET", "/gardens/{gardenId}/goals", path_params=None)
    resp = handler.list_goals(event)
    assert resp["statusCode"] == 400


def test_list_goals_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_query=RuntimeError("boom")))
    event = _event("GET", "/gardens/{gardenId}/goals", path_params={"gardenId": "g1"})
    resp = handler.list_goals(event)
    assert resp["statusCode"] == 500


def test_get_goal_detail_not_found(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "missing"},
    )
    resp = handler.get_goal_detail(event)
    assert resp["statusCode"] == 404


def test_get_goal_detail_missing_path_params():
    event = _event("GET", "/gardens/{gardenId}/goals/{goalId}", path_params=None)
    resp = handler.get_goal_detail(event)
    assert resp["statusCode"] == 400


def test_get_goal_detail_without_a_plan_yet(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {
                "Item": {"goal_id": "goal-1", "description": "help", "status": "Decomposing"}
            },
            # No "PLAN#goal-1" entry — falls through to the default {} (no "Item"),
            # matching real DynamoDB's actual "no such item" response shape.
        },
        query_responses=[{"Items": []}, {"Items": []}],  # tasks, then messages
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "plan" not in body
    assert body["tasks"] == []
    assert body["media"] == []
    assert body["messages"] == []


def test_get_goal_detail_with_plan_tasks_media_and_messages(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {
                "Item": {
                    "goal_id": "goal-1",
                    "description": "help",
                    "status": "PlanProposed",
                    "media_ids": ["media-1"],
                }
            },
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "Leaves green again",
                    "status": "PlanProposed",
                }
            },
            "MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/tomato.jpg"}},
        },
        query_responses=[
            {
                "Items": [
                    {
                        "task_id": "t1",
                        "goal_id": "goal-1",
                        "title": "Water",
                        "detail": "Deeply",
                        "scope": "plant",
                    }
                ]
            },
            {
                "Items": [
                    {
                        "role": "user",
                        "content": "help",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "sk": "GOALMSG#goal-1#a",
                    }
                ]
            },
        ],
    )
    fake_s3 = FakeS3()
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_s3", fake_s3)
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["plan"] == {
        "planId": "goal-1",
        "goalId": "goal-1",
        "successCriteria": "Leaves green again",
        "status": "PlanProposed",
    }
    assert body["tasks"] == [
        {
            "taskId": "t1",
            "goalId": "goal-1",
            "title": "Water",
            "detail": "Deeply",
            "scope": "plant",
            "status": "pending",
        }
    ]
    assert len(body["media"]) == 1
    assert body["media"][0]["mediaId"] == "media-1"
    assert "g1/media-1/tomato.jpg" in body["media"][0]["downloadUrl"]
    assert body["messages"] == [
        {"role": "user", "content": "help", "createdAt": "2026-01-01T00:00:00+00:00"}
    ]


def test_post_goal_message_happy_path(monkeypatch):
    fake_table = FakeTable()
    fake_events = FakeEvents()
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_events", fake_events)

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/messages",
        body={"content": "Can we water less often?"},
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.post_goal_message(event)

    assert resp["statusCode"] == 202
    assert len(fake_table.put_calls) == 1
    message_item = fake_table.put_calls[0]
    assert message_item["role"] == "user"
    assert message_item["content"] == "Can we water less often?"
    assert message_item["sk"].startswith("GOALMSG#goal-1#")
    assert len(fake_events.put_calls) == 1
    detail = json.loads(fake_events.put_calls[0][0]["Detail"])
    assert detail == {"gardenId": "g1", "goalId": "goal-1"}
    assert fake_events.put_calls[0][0]["DetailType"] == "goal.message.received"


def test_post_goal_message_missing_content():
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/messages",
        body={},
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.post_goal_message(event)
    assert resp["statusCode"] == 400


def test_post_goal_message_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_put=RuntimeError("boom")))
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/messages",
        body={"content": "hi"},
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.post_goal_message(event)
    assert resp["statusCode"] == 500


def test_approve_plan_happy_path(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"plan_id": "goal-1", "status": "PlanProposed"}}
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "goal-1"},
    )
    resp = handler.approve_plan(event)

    assert resp["statusCode"] == 200
    assert len(fake_table.update_calls) == 2
    plan_update, goal_update = fake_table.update_calls
    assert plan_update["Key"] == {"pk": "GARDEN#g1", "sk": "PLAN#goal-1"}
    assert plan_update["ExpressionAttributeValues"][":status"] == "Approved"
    assert goal_update["Key"] == {"pk": "GARDEN#g1", "sk": "GOAL#goal-1"}
    assert goal_update["ExpressionAttributeValues"][":status"] == "Approved"

    # Phase 6: a best-effort plan.approved Event is written too.
    assert len(fake_table.put_calls) == 1
    event_item = fake_table.put_calls[0]
    assert event_item["type"] == "plan.approved"
    assert event_item["payload"] == {"goalId": "goal-1", "planId": "goal-1"}


def test_approve_plan_event_write_failure_still_returns_200(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"plan_id": "goal-1", "status": "PlanProposed"}},
        raise_on_put=RuntimeError("boom"),
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "goal-1"},
    )
    resp = handler.approve_plan(event)

    assert resp["statusCode"] == 200


def test_approve_plan_not_found(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "missing"},
    )
    resp = handler.approve_plan(event)
    assert resp["statusCode"] == 404


def test_approve_plan_missing_path_params():
    event = _event("POST", "/gardens/{gardenId}/plans/{planId}/approve", path_params=None)
    resp = handler.approve_plan(event)
    assert resp["statusCode"] == 400


def test_approve_plan_stamps_due_dates_on_pending_tasks(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"plan_id": "goal-1", "status": "PlanProposed"}},
        query_response={
            "Items": [
                {"pk": "GARDEN#g1", "sk": "TASK#goal-1#t1", "status": "pending"},
                {"pk": "GARDEN#g1", "sk": "TASK#goal-1#t2", "status": "done"},
            ]
        },
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "goal-1"},
    )
    resp = handler.approve_plan(event)

    assert resp["statusCode"] == 200
    # 2 status-flip updates (Plan, Goal) + exactly 1 due-date stamp (only the pending task).
    assert len(fake_table.update_calls) == 3
    stamp = fake_table.update_calls[2]
    assert stamp["Key"] == {"pk": "GARDEN#g1", "sk": "TASK#goal-1#t1"}
    assert stamp["ExpressionAttributeValues"][":p"] == "TASK_STATUS#pending"
    due = datetime.fromisoformat(stamp["ExpressionAttributeValues"][":d"])
    assert due > datetime.now(UTC)


def test_approve_plan_due_date_stamping_failure_still_returns_200(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"plan_id": "goal-1", "status": "PlanProposed"}},
        raise_on_query=RuntimeError("boom"),
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "goal-1"},
    )
    resp = handler.approve_plan(event)

    assert resp["statusCode"] == 200
    # The two status-flip updates already committed before the stamping step raised.
    assert len(fake_table.update_calls) == 2


def test_approve_plan_revision_then_reapprove_stamps_only_current_tasks(monkeypatch):
    fake_table = FakeTable(
        get_item_response={"Item": {"plan_id": "goal-1", "status": "PlanProposed"}},
        query_responses=[
            {"Items": [{"pk": "GARDEN#g1", "sk": "TASK#goal-1#old-1", "status": "pending"}]},
            {"Items": [{"pk": "GARDEN#g1", "sk": "TASK#goal-1#new-1", "status": "pending"}]},
        ],
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "goal-1"},
    )
    handler.approve_plan(event)  # first approval: stamps old-1
    handler.approve_plan(event)  # revision + re-approval: stamps new-1 only

    stamped_task_sks = [
        c["Key"]["sk"] for c in fake_table.update_calls if c["Key"]["sk"].startswith("TASK#")
    ]
    assert stamped_task_sks == ["TASK#goal-1#old-1", "TASK#goal-1#new-1"]


# --- post_task_checkin (images-completion: Plant/Goal/Task/Media traceability) -------------


def test_post_task_checkin_task_not_found(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={}))
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "media-1"},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "missing"},
    )
    resp = handler.post_task_checkin(event)
    assert resp["statusCode"] == 404


def test_post_task_checkin_media_not_found(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={"TASK#goal-1#task-1": {"Item": {"task_id": "task-1"}}},
        get_item_response={},  # MEDIA# lookup falls through to this default -> not found
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "missing-media"},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
    )
    resp = handler.post_task_checkin(event)
    assert resp["statusCode"] == 404


def test_post_task_checkin_missing_media_id():
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
    )
    resp = handler.post_task_checkin(event)
    assert resp["statusCode"] == 400


def test_post_task_checkin_missing_path_params():
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "media-1"},
        path_params=None,
    )
    resp = handler.post_task_checkin(event)
    assert resp["statusCode"] == 400


def test_get_goal_detail_resolves_task_media_and_plant_id(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {"Item": {"goal_id": "goal-1", "description": "help"}},
            "MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/photo.jpg"}},
        },
        query_responses=[
            {
                "Items": [
                    {
                        "task_id": "task-1",
                        "goal_id": "goal-1",
                        "title": "Water",
                        "detail": "Deeply",
                        "scope": "plant",
                        "status": "done",
                        "plant_id": "plant-1",
                        "media_id": "media-1",
                    }
                ]
            },
            {"Items": []},
        ],
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "MEDIA_BUCKET_NAME", "tendril-dev-media")

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    task = body["tasks"][0]
    assert task["plantId"] == "plant-1"
    assert task["media"]["mediaId"] == "media-1"
    assert "g1/media-1/photo.jpg" in task["media"]["downloadUrl"]


def test_get_goal_detail_resolves_task_feedback(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {"Item": {"goal_id": "goal-1", "description": "help"}},
        },
        query_responses=[
            {
                "Items": [
                    {
                        "task_id": "task-1",
                        "goal_id": "goal-1",
                        "title": "Water",
                        "detail": "Deeply",
                        "scope": "plant",
                        "status": "done",
                        "feedback": "Looking good, keep it up!",
                    }
                ]
            },
            {"Items": []},
        ],
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    body = json.loads(resp["body"])
    assert body["tasks"][0]["feedback"] == "Looking good, keep it up!"


def test_get_goal_detail_omits_task_feedback_when_absent(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {"Item": {"goal_id": "goal-1", "description": "help"}},
        },
        query_responses=[
            {
                "Items": [
                    {
                        "task_id": "task-1",
                        "goal_id": "goal-1",
                        "title": "Water",
                        "detail": "Deeply",
                        "scope": "plant",
                        "status": "pending",
                    }
                ]
            },
            {"Items": []},
        ],
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    body = json.loads(resp["body"])
    assert "feedback" not in body["tasks"][0]


def test_get_goal_detail_resolves_plan_trace(monkeypatch):
    # "ms" is a Decimal, not a plain int (real boto3 resource-layer regression: DynamoDB's Table
    # deserializes Number attributes as decimal.Decimal, which json.dumps can't serialize on its
    # own — found live, get_goal_detail 500'd the moment a plan actually had a trace).
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {"Item": {"goal_id": "goal-1", "description": "help"}},
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "Leaves green",
                    "status": "PlanProposed",
                    "trace": [
                        {"agent": "agronomy", "says": "Nitrogen is high.", "ms": Decimal(2100)},
                        {
                            "agent": "orchestrator",
                            "says": "Feed change recommended.",
                            "ms": Decimal(900),
                            "is_orchestrator": True,
                        },
                    ],
                }
            },
        },
        query_responses=[{"Items": []}, {"Items": []}],
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    body = json.loads(resp["body"])
    trace = body["plan"]["trace"]
    assert trace[0] == {
        "agent": "agronomy",
        "says": "Nitrogen is high.",
        "ms": 2100,
        "isOrchestrator": False,
    }
    assert trace[1] == {
        "agent": "orchestrator",
        "says": "Feed change recommended.",
        "ms": 900,
        "isOrchestrator": True,
    }


def test_get_goal_detail_omits_plan_trace_when_absent(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "GOAL#goal-1": {"Item": {"goal_id": "goal-1", "description": "help"}},
            "PLAN#goal-1": {
                "Item": {
                    "plan_id": "goal-1",
                    "goal_id": "goal-1",
                    "success_criteria": "Leaves green",
                    "status": "PlanProposed",
                }
            },
        },
        query_responses=[{"Items": []}, {"Items": []}],
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)

    body = json.loads(resp["body"])
    assert "trace" not in body["plan"]


# --- list_tasks / list_activity (Phase 6) --------------------------------------------------


def test_list_tasks_across_multiple_goals(monkeypatch):
    fake_table = FakeTable(
        query_response={
            "Items": [
                {
                    "task_id": "t1",
                    "goal_id": "goal-1",
                    "title": "Water",
                    "detail": "Deeply",
                    "scope": "plant",
                    "status": "pending",
                },
                {
                    "task_id": "t2",
                    "goal_id": "goal-2",
                    "title": "Mulch",
                    "detail": "Around base",
                    "scope": "plant",
                    "status": "done",
                },
            ]
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event("GET", "/gardens/{gardenId}/tasks", path_params={"gardenId": "g1"})
    resp = handler.list_tasks(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert [t["taskId"] for t in body] == ["t1", "t2"]
    assert [t["goalId"] for t in body] == ["goal-1", "goal-2"]
    # A single Query on the shared TASK# prefix, not one per goal.
    assert len(fake_table.query_calls) == 1


def test_list_tasks_empty(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/tasks", path_params={"gardenId": "g1"})
    resp = handler.list_tasks(event)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == []


def test_list_tasks_missing_garden_id():
    event = _event("GET", "/gardens/{gardenId}/tasks", path_params=None)
    resp = handler.list_tasks(event)
    assert resp["statusCode"] == 400


def test_list_tasks_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_query=RuntimeError("boom")))
    event = _event("GET", "/gardens/{gardenId}/tasks", path_params={"gardenId": "g1"})
    resp = handler.list_tasks(event)
    assert resp["statusCode"] == 500


def test_list_activity_happy_path_newest_first(monkeypatch):
    fake_table = FakeTable(
        query_response={
            "Items": [
                {
                    "type": "task.checkin",
                    "payload": {"goalId": "goal-1", "taskId": "t1"},
                    "created_at": "2026-01-02T00:00:00+00:00",
                },
                {
                    "type": "goal.submitted",
                    "payload": {"goalId": "goal-1", "description": "help"},
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            ]
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)

    event = _event("GET", "/gardens/{gardenId}/activity", path_params={"gardenId": "g1"})
    resp = handler.list_activity(event)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert [e["type"] for e in body] == ["task.checkin", "goal.submitted"]
    assert body[0]["goalId"] == "goal-1"
    # Newest-first: DynamoDB does the ordering, not a Python-side sort.
    assert fake_table.query_calls[0]["ScanIndexForward"] is False


def test_list_activity_empty(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/activity", path_params={"gardenId": "g1"})
    resp = handler.list_activity(event)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == []


def test_list_activity_missing_garden_id():
    event = _event("GET", "/gardens/{gardenId}/activity", path_params=None)
    resp = handler.list_activity(event)
    assert resp["statusCode"] == 400


def test_list_activity_dynamo_failure_returns_500(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(raise_on_query=RuntimeError("boom")))
    event = _event("GET", "/gardens/{gardenId}/activity", path_params={"gardenId": "g1"})
    resp = handler.list_activity(event)
    assert resp["statusCode"] == 500


def test_handler_routes_list_tasks(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/tasks", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


def test_handler_routes_list_activity(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/activity", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


# --- contract test: responses conform to openapi.yaml's JSON Schema (WS-03) ----


def test_create_goal_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(handler, "_table", FakeTable())
    monkeypatch.setattr(handler, "_events", FakeEvents())

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_goal(event)
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = spec["components"]["schemas"]["GoalCreateResponse"]
    validate(instance=body, schema=schema)


def test_create_garden_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(handler, "_client", FakeClient())
    event = _event(
        "POST",
        "/gardens",
        body={"name": "G", "geolocation": "Pune"},
        headers={"X-User-Id": "u"},
    )
    resp = handler.create_garden(event)
    assert resp["statusCode"] == 201
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = spec["components"]["schemas"]["GardenCreateResponse"]
    validate(instance=body, schema=schema)


def test_get_garden_weather_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(get_item_response={"Item": {"garden_id": "g1", "geolocation": "Pune"}}),
    )
    monkeypatch.setattr(handler, "_geocode", lambda query: (18.52, 73.86))
    monkeypatch.setattr(
        handler, "_fetch_forecast", lambda lat, lon: {"temperatureC": 29.4, "weatherCode": 1}
    )
    event = _event("GET", "/gardens/{gardenId}/weather", path_params={"gardenId": "g1"})
    resp = handler.get_garden_weather(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = spec["components"]["schemas"]["GardenWeather"]
    validate(instance=body, schema=schema)


def test_list_gardens_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(query_response={"Items": [{"garden_id": "g1", "name": "My Garden"}]}),
    )
    event = _event("GET", "/gardens", headers={"X-User-Id": "user-1"})
    resp = handler.list_gardens(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = {"type": "array", "items": spec["components"]["schemas"]["GardenSummary"]}
    validate(instance=body, schema=schema)


def test_list_plants_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(
            query_response={
                "Items": [
                    {"plant_id": "p1", "species": "Tomato", "variety": "Pusa Ruby", "stage": "new"}
                ]
            }
        ),
    )
    event = _event("GET", "/gardens/{gardenId}/plants", path_params={"gardenId": "g1"})
    resp = handler.list_plants(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = {
        "type": "array",
        "items": spec["components"]["schemas"]["Plant"],
    }
    validate(instance=body, schema=schema)


def test_list_tasks_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(
            query_response={
                "Items": [
                    {
                        "task_id": "t1",
                        "goal_id": "goal-1",
                        "title": "Water",
                        "detail": "Deeply",
                        "scope": "plant",
                        "status": "pending",
                    }
                ]
            }
        ),
    )
    event = _event("GET", "/gardens/{gardenId}/tasks", path_params={"gardenId": "g1"})
    resp = handler.list_tasks(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = {"type": "array", "items": spec["components"]["schemas"]["Task"]}
    validate(instance=body, schema=schema)


def test_list_activity_response_conforms_to_openapi_schema(monkeypatch):
    import yaml
    from jsonschema import validate

    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(
            query_response={
                "Items": [
                    {
                        "type": "goal.submitted",
                        "payload": {"goalId": "goal-1", "description": "help"},
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ]
            }
        ),
    )
    event = _event("GET", "/gardens/{gardenId}/activity", path_params={"gardenId": "g1"})
    resp = handler.list_activity(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    schema = {"type": "array", "items": spec["components"]["schemas"]["ActivityEvent"]}
    validate(instance=body, schema=schema)


def test_get_goal_detail_response_conforms_to_openapi_schema(monkeypatch):
    import jsonschema
    import yaml

    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(
            get_item_responses={
                "GOAL#goal-1": {
                    "Item": {"goal_id": "goal-1", "description": "help", "status": "PlanProposed"}
                },
                "PLAN#goal-1": {
                    "Item": {
                        "plan_id": "goal-1",
                        "goal_id": "goal-1",
                        "success_criteria": "Healthy again",
                        "status": "PlanProposed",
                    }
                },
            },
            query_responses=[{"Items": []}, {"Items": []}],
        ),
    )
    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.get_goal_detail(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    # GoalDetail's schema $refs other component schemas (Goal/Plan/Task/...) — unlike the other
    # contract tests' flat schemas, this needs a resolver rooted at the whole spec document to
    # follow those "#/components/schemas/..." pointers.
    resolver = jsonschema.RefResolver.from_schema(spec)
    validator = jsonschema.Draft7Validator(
        {"$ref": "#/components/schemas/GoalDetail"}, resolver=resolver
    )
    validator.validate(body)


# --- handler() routing ---------------------------------------------------------


def test_handler_routes_post_gardens(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    event = _event(
        "POST", "/gardens", body={"name": "G", "geolocation": "Pune"}, headers={"X-User-Id": "u"}
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 201


def test_handler_routes_get_garden(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(
            get_item_response={
                "Item": {
                    "garden_id": "g1",
                    "name": "G",
                    "geolocation": "Pune",
                    "owner_user_id": "u",
                    "created_at": "now",
                }
            }
        ),
    )
    event = _event("GET", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


def test_handler_routes_post_media(monkeypatch):
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg", "sizeBytes": 2_000_000},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 201


def test_handler_routes_post_plants(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event(
        "POST",
        "/gardens/{gardenId}/plants",
        body={"species": "Tomato"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 201


def test_handler_routes_get_plants(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/plants", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


def test_handler_routes_delete_plant(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event(
        "DELETE",
        "/gardens/{gardenId}/plants/{plantId}",
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1", "plantId": "p1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 204


def test_handler_routes_post_goals(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals",
        body={"description": "help"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 202


def test_handler_routes_get_goals(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": []}))
    event = _event("GET", "/gardens/{gardenId}/goals", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


def test_handler_routes_get_goal_detail(monkeypatch):
    monkeypatch.setattr(
        handler,
        "_table",
        FakeTable(
            get_item_responses={
                "GOAL#goal-1": {"Item": {"goal_id": "goal-1", "description": "help"}}
            },
            query_responses=[{"Items": []}, {"Items": []}],
        ),
    )
    event = _event(
        "GET",
        "/gardens/{gardenId}/goals/{goalId}",
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


def test_handler_routes_post_goal_messages(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/messages",
        body={"content": "hi"},
        path_params={"gardenId": "g1", "goalId": "goal-1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 202


def test_handler_routes_post_plan_approve(monkeypatch):
    monkeypatch.setattr(
        handler, "_table", FakeTable(get_item_response={"Item": {"plan_id": "goal-1"}})
    )
    event = _event(
        "POST",
        "/gardens/{gardenId}/plans/{planId}/approve",
        path_params={"gardenId": "g1", "planId": "goal-1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


def test_handler_unknown_route_returns_404():
    event = _event("DELETE", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 404


# --- Phase A (CP-01/CP-02, ADR-0032): garden context, stake numbers, bulk create ------------

BRAMHANGAON_CONTEXT = {
    "soil": "Heavy black calcareous/saline clay; lime-locked Fe/Mg/N chlorosis",
    "irrigation": "16 mm drip off well water, emitter ~1.5 ft off collar",
    "intercrop": "Veg strip (brinjal, marigold) north edge; chana planned in alleys",
    "layout": "25x25 ft square, 1.5 acre west block; 23 Tall on east fence (border)",
    "stakeRange": {"min": 1, "max": 105},
    "notificationRecipients": [
        {"name": "Owner", "e164": "+919800000001"},
        {"name": "Field worker", "e164": "+919800000002"},
    ],
}


def _validate_against(schema_name: str, instance) -> None:
    import jsonschema
    import yaml

    spec_path = Path(__file__).resolve().parents[1] / "openapi.yaml"
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    resolver = jsonschema.RefResolver.from_schema(spec)
    jsonschema.validate(
        instance=instance, schema=spec["components"]["schemas"][schema_name], resolver=resolver
    )


def _garden_item(**extra):
    item = {
        "pk": "GARDEN#g1",
        "sk": "METADATA",
        "garden_id": "g1",
        "name": "Bramhangaon Farm",
        "geolocation": "Kopargaon, Maharashtra",
        "owner_user_id": "u",
        "created_at": "2026-11-23T00:00:00+00:00",
    }
    item.update(extra)
    return item


def _post(resource, body, path_params, headers=None):
    return _event(
        "POST",
        resource,
        body=body,
        headers={"X-User-Id": "u"} if headers is None else headers,
        path_params=path_params,
    )


def test_create_garden_stores_context_in_snake_case(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    body = {
        "name": "Bramhangaon Farm",
        "geolocation": "Kopargaon, Maharashtra",
        "climateZone": "Semi-arid, Deccan plateau",
        "context": BRAMHANGAON_CONTEXT,
    }
    resp = handler.create_garden(_post("/gardens", body, None))
    assert resp["statusCode"] == 201

    garden = fake_client.calls[0][0]["Put"]["Item"]
    assert garden["climate_zone"]["S"] == "Semi-arid, Deccan plateau"
    ctx = garden["context"]["M"]
    assert ctx["soil"]["S"].startswith("Heavy black")
    assert ctx["stake_range"]["M"]["max"]["N"] == "105"
    assert ctx["notification_recipients"]["L"][1]["M"]["e164"]["S"] == "+919800000002"


def test_create_garden_without_context_is_unchanged(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    resp = handler.create_garden(_post("/gardens", {"name": "G", "geolocation": "Pune"}, None))
    assert resp["statusCode"] == 201
    garden = fake_client.calls[0][0]["Put"]["Item"]
    assert "context" not in garden and "climate_zone" not in garden


@pytest.mark.parametrize(
    ("context", "fragment"),
    [
        ("not-an-object", "must be a JSON object"),
        ({"weather": "hot"}, "unknown field"),
        ({"soil": 5}, "context.soil must be a string"),
        ({"soil": "x" * 1001}, "at most 1000"),
        ({"stakeRange": {"min": 10, "max": 5}}, "stakeRange"),
        ({"stakeRange": {"min": True, "max": 5}}, "stakeRange"),
        ({"notificationRecipients": [{"name": "A", "e164": "9800000001"}]}, "E.164"),
        ({"notificationRecipients": [{"e164": "+919800000001"}]}, "needs a name"),
        ({"notificationRecipients": [{"name": "A", "e164": "+919800000001"}] * 11}, "at most 10"),
    ],
)
def test_create_garden_rejects_invalid_context(monkeypatch, context, fragment):
    monkeypatch.setattr(handler, "_client", FakeClient())
    body = {"name": "G", "geolocation": "Pune", "context": context}
    resp = handler.create_garden(_post("/gardens", body, None))
    assert resp["statusCode"] == 400
    assert fragment in json.loads(resp["body"])["message"]


def test_invalid_recipient_error_does_not_echo_the_number(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    body = {
        "name": "G",
        "geolocation": "Pune",
        "context": {"notificationRecipients": [{"name": "A", "e164": "98765-43210"}]},
    }
    resp = handler.create_garden(_post("/gardens", body, None))
    assert resp["statusCode"] == 400
    assert "98765" not in resp["body"]


def test_get_garden_returns_context_and_climate_zone_conforming_to_schema(monkeypatch):
    stored = {
        "soil": BRAMHANGAON_CONTEXT["soil"],
        "stake_range": {"min": Decimal(1), "max": Decimal(105)},
        "notification_recipients": [{"name": "Owner", "e164": "+919800000001"}],
    }
    item = _garden_item(context=stored, climate_zone="Semi-arid")
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={"Item": item}))
    resp = handler.get_garden(_event("GET", "/gardens/{gardenId}", path_params={"gardenId": "g1"}))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["climateZone"] == "Semi-arid"
    assert body["context"] == {
        "soil": BRAMHANGAON_CONTEXT["soil"],
        "stakeRange": {"min": 1, "max": 105},
        "notificationRecipients": [{"name": "Owner", "e164": "+919800000001"}],
    }
    _validate_against("Garden", body)


def test_update_garden_replaces_context_and_returns_garden(monkeypatch):
    fake_table = FakeTable(get_item_response={"Item": _garden_item()})
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event(
        "PATCH",
        "/gardens/{gardenId}",
        body={"context": {"soil": "clay"}, "climateZone": "Semi-arid"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200

    call = fake_table.update_calls[0]
    assert call["Key"] == {"pk": "GARDEN#g1", "sk": "METADATA"}
    assert call["ConditionExpression"] == "attribute_exists(pk)"
    values = call["ExpressionAttributeValues"]
    assert values[":context"] == {"soil": "clay"}
    assert values[":climate_zone"] == "Semi-arid"
    assert ":updated_at" in values


def test_update_garden_unknown_garden_returns_404(monkeypatch):
    fake_table = FakeTable(raise_on_update=FakeClientError("ConditionalCheckFailedException"))
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event(
        "PATCH",
        "/gardens/{gardenId}",
        body={"vision": "coconuts"},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "nope"},
    )
    assert handler.update_garden(event)["statusCode"] == 404


def test_update_garden_requires_at_least_one_field(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    event = _event(
        "PATCH",
        "/gardens/{gardenId}",
        body={},
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1"},
    )
    assert handler.update_garden(event)["statusCode"] == 400


def test_update_garden_requires_user_id():
    event = _event(
        "PATCH", "/gardens/{gardenId}", body={"vision": "x"}, path_params={"gardenId": "g1"}
    )
    assert handler.update_garden(event)["statusCode"] == 400


PLANTS = "/gardens/{gardenId}/plants"
BULK = "/gardens/{gardenId}/plants/bulk"
G1 = {"gardenId": "g1"}


def test_create_plant_with_stake_writes_lookup_item_transactionally(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_table", FakeTable())  # no garden context -> no range check
    body = {"species": "Coconut", "variety": "Kalpa Samrudhi", "stakeNo": 74, "stage": "young"}
    resp = handler.create_plant(_post(PLANTS, body, G1))
    assert resp["statusCode"] == 201
    result = json.loads(resp["body"])
    assert result["stakeNo"] == 74 and "outOfRange" not in result

    plant_put, stake_put = (i["Put"] for i in fake_client.calls[0])
    assert plant_put["Item"]["stake_no"]["N"] == "74"
    assert plant_put["Item"]["stage"]["S"] == "young"
    assert stake_put["Item"]["sk"]["S"] == "STAKE#00074"
    assert stake_put["Item"]["plant_id"]["S"] == result["plantId"]
    assert stake_put["ConditionExpression"] == "attribute_not_exists(pk)"
    _validate_against("PlantCreateResponse", result)


def test_create_plant_duplicate_stake_returns_409(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient(raise_on_transact=_stake_conflict()))
    monkeypatch.setattr(handler, "_table", FakeTable())
    resp = handler.create_plant(_post(PLANTS, {"species": "Coconut", "stakeNo": 74}, G1))
    assert resp["statusCode"] == 409


def test_create_plant_missing_media_with_stake_is_500_not_409(monkeypatch):
    # Plant, stake lookup, media update: the MEDIA condition (index 2) failed, not the stake.
    err = FakeClientError(
        "TransactionCanceledException", ["None", "None", "ConditionalCheckFailed"]
    )
    monkeypatch.setattr(handler, "_client", FakeClient(raise_on_transact=err))
    monkeypatch.setattr(handler, "_table", FakeTable())
    body = {"species": "Coconut", "stakeNo": 74, "mediaId": "missing"}
    assert handler.create_plant(_post(PLANTS, body, G1))["statusCode"] == 500


def test_create_plant_out_of_range_stake_is_created_but_flagged(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    garden = _garden_item(context={"stake_range": {"min": Decimal(1), "max": Decimal(105)}})
    monkeypatch.setattr(
        handler, "_table", FakeTable(get_item_responses={"METADATA": {"Item": garden}})
    )
    resp = handler.create_plant(_post(PLANTS, {"species": "Coconut", "stakeNo": 106}, G1))
    assert resp["statusCode"] == 201
    assert json.loads(resp["body"])["outOfRange"] is True


@pytest.mark.parametrize("stake", [0, -3, "7", 1.5, True, 100000])
def test_create_plant_rejects_invalid_stake_no(monkeypatch, stake):
    monkeypatch.setattr(handler, "_table", FakeTable())
    resp = handler.create_plant(_post(PLANTS, {"species": "Coconut", "stakeNo": stake}, G1))
    assert resp["statusCode"] == 400


def test_create_plant_rejects_blank_stage(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    resp = handler.create_plant(_post(PLANTS, {"species": "Coconut", "stage": "  "}, G1))
    assert resp["statusCode"] == 400


def test_bulk_create_plants_creates_range_and_skips_existing(monkeypatch):
    # #2 already exists in this garden; #1 and #3 are new.
    fake_client = FakeClient(transact_errors=[None, _stake_conflict(), None])
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(
        handler, "_table", FakeTable(get_item_response={"Item": _garden_item(context={})})
    )
    body = {
        "species": "Coconut",
        "variety": "Kalpa Samrudhi DxT",
        "stage": "young",
        "stakeFrom": 1,
        "stakeTo": 3,
    }
    event = _post(BULK, body, G1)
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 201
    result = json.loads(resp["body"])
    assert [c["stakeNo"] for c in result["created"]] == [1, 3]
    assert result["skipped"] == [2]
    assert len(fake_client.calls) == 2  # the conflicting transaction never committed
    _validate_against("PlantBulkCreateResponse", result)


def test_bulk_create_plants_flags_out_of_range(monkeypatch):
    monkeypatch.setattr(handler, "_client", FakeClient())
    garden = _garden_item(context={"stake_range": {"min": Decimal(1), "max": Decimal(2)}})
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={"Item": garden}))
    body = {"species": "Coconut", "stakeFrom": 2, "stakeTo": 3}
    created = json.loads(handler.bulk_create_plants(_post(BULK, body, G1))["body"])["created"]
    assert "outOfRange" not in created[0]
    assert created[1]["outOfRange"] is True


def test_bulk_create_plants_unknown_garden_returns_404(monkeypatch):
    monkeypatch.setattr(handler, "_table", FakeTable())
    body = {"species": "Coconut", "stakeFrom": 1, "stakeTo": 3}
    resp = handler.bulk_create_plants(_post(BULK, body, {"gardenId": "nope"}))
    assert resp["statusCode"] == 404


@pytest.mark.parametrize(
    "body",
    [
        {"stakeFrom": 1, "stakeTo": 3},  # missing species
        {"species": "Coconut", "stakeFrom": 5, "stakeTo": 1},
        {"species": "Coconut", "stakeFrom": 0, "stakeTo": 3},
        {"species": "Coconut", "stakeFrom": 1, "stakeTo": 201},  # over the 200 cap
        {"species": "Coconut", "stakeFrom": "1", "stakeTo": 3},
    ],
)
def test_bulk_create_plants_rejects_invalid_input(monkeypatch, body):
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={"Item": _garden_item()}))
    assert handler.bulk_create_plants(_post(BULK, body, G1))["statusCode"] == 400


def test_bulk_create_plants_requires_user_id(monkeypatch):
    body = {"species": "Coconut", "stakeFrom": 1, "stakeTo": 3}
    assert handler.bulk_create_plants(_post(BULK, body, G1, headers={}))["statusCode"] == 400


def test_bulk_create_plants_failure_mid_run_returns_500(monkeypatch):
    monkeypatch.setattr(
        handler, "_client", FakeClient(transact_errors=[None, RuntimeError("throttled")])
    )
    monkeypatch.setattr(handler, "_table", FakeTable(get_item_response={"Item": _garden_item()}))
    body = {"species": "Coconut", "stakeFrom": 1, "stakeTo": 3}
    resp = handler.bulk_create_plants(_post(BULK, body, G1))
    assert resp["statusCode"] == 500
    assert "#2" in json.loads(resp["body"])["message"]


def test_list_plants_orders_by_stake_number_unnumbered_last(monkeypatch):
    items = [
        {"plant_id": "pa", "species": "Coconut", "stage": "new"},
        {"plant_id": "p10", "species": "Coconut", "stage": "new", "stake_no": Decimal(10)},
        {"plant_id": "p2", "species": "Coconut", "stage": "new", "stake_no": Decimal(2)},
    ]
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": items}))
    resp = handler.list_plants(_event("GET", PLANTS, path_params=G1))
    body = json.loads(resp["body"])
    assert [p["plantId"] for p in body] == ["p2", "p10", "pa"]
    assert body[0]["stakeNo"] == 2 and "stakeNo" not in body[2]
    _validate_against("Plant", body[0])


def test_list_plants_follows_last_evaluated_key(monkeypatch):
    page1 = {
        "Items": [{"plant_id": "p1", "species": "Coconut", "stage": "new"}],
        "LastEvaluatedKey": {"pk": "GARDEN#g1", "sk": "PLANT#p1"},
    }
    page2 = {"Items": [{"plant_id": "p2", "species": "Coconut", "stage": "new"}]}
    fake_table = FakeTable(query_responses=[page1, page2])
    monkeypatch.setattr(handler, "_table", fake_table)
    resp = handler.list_plants(_event("GET", PLANTS, path_params=G1))
    assert [p["plantId"] for p in json.loads(resp["body"])] == ["p1", "p2"]
    assert fake_table.query_calls[1]["ExclusiveStartKey"] == page1["LastEvaluatedKey"]


def test_batch_get_media_keys_retries_unprocessed_keys(monkeypatch):
    unprocessed = {
        handler.APP_TABLE_NAME: {"Keys": [{"pk": {"S": "GARDEN#g1"}, "sk": {"S": "MEDIA#m2"}}]}
    }
    first = _media_batch_response(("m1", "k1"))
    first["UnprocessedKeys"] = unprocessed
    fake_client = FakeClient(batch_get_responses=[first, _media_batch_response(("m2", "k2"))])
    monkeypatch.setattr(handler, "_client", fake_client)
    assert handler._batch_get_media_keys("g1", ["m1", "m2", "m1"]) == {"m1": "k1", "m2": "k2"}
    assert len(fake_client.batch_get_calls) == 2
    assert fake_client.batch_get_calls[1] == unprocessed


def test_delete_numbered_plant_also_deletes_stake_lookup(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    fake_table = FakeTable(get_item_response={"Item": {"plant_id": "p1", "stake_no": Decimal(74)}})
    monkeypatch.setattr(handler, "_table", fake_table)
    event = _event(
        "DELETE",
        "/gardens/{gardenId}/plants/{plantId}",
        headers={"X-User-Id": "u"},
        path_params={"gardenId": "g1", "plantId": "p1"},
    )
    assert handler.delete_plant(event)["statusCode"] == 204
    assert fake_table.delete_calls == []  # went through the transaction instead
    plant_del, stake_del = (i["Delete"] for i in fake_client.calls[0])
    assert plant_del["Key"]["sk"]["S"] == "PLANT#p1"
    assert stake_del["Key"]["sk"]["S"] == "STAKE#00074"
    assert stake_del["ExpressionAttributeValues"][":pid"]["S"] == "p1"


# --- ADR-0033: upload limit, key layout, processed copies for display -----------------------

MEDIA = "/gardens/{gardenId}/media"


@pytest.mark.parametrize(
    ("size", "fragment"),
    [
        (None, "sizeBytes is required"),
        (0, "sizeBytes is required"),
        ("2000000", "sizeBytes is required"),
        (10 * 1024 * 1024 + 1, "larger than 10 MB"),
    ],
)
def test_create_media_upload_enforces_the_10mb_limit(monkeypatch, size, fragment):
    fake_s3 = FakeS3()
    monkeypatch.setattr(handler, "_s3", fake_s3)
    monkeypatch.setattr(handler, "_table", FakeTable())
    body = {"contentType": "image/jpeg", "fileName": "big.jpg"}
    if size is not None:
        body["sizeBytes"] = size
    resp = handler.create_media_upload(_post(MEDIA, body, G1))
    assert resp["statusCode"] == 400
    assert fragment in json.loads(resp["body"])["message"]
    assert fake_s3.presign_calls == []  # never signed


def test_create_media_upload_accepts_exactly_10mb(monkeypatch):
    monkeypatch.setattr(handler, "_s3", FakeS3())
    monkeypatch.setattr(handler, "_table", FakeTable())
    body = {"contentType": "image/jpeg", "fileName": "ok.jpg", "sizeBytes": 10 * 1024 * 1024}
    assert handler.create_media_upload(_post(MEDIA, body, G1))["statusCode"] == 201


@pytest.mark.parametrize("name", ["../../evil.jpg", "a/b/c.jpg", "dir\\win.jpg"])
def test_create_media_upload_keeps_only_the_base_name(monkeypatch, name):
    fake_s3, fake_table = FakeS3(), FakeTable()
    monkeypatch.setattr(handler, "_s3", fake_s3)
    monkeypatch.setattr(handler, "_table", fake_table)
    body = {"contentType": "image/jpeg", "fileName": name, "sizeBytes": 1000}
    resp = handler.create_media_upload(_post(MEDIA, body, G1))
    media_id = json.loads(resp["body"])["mediaId"]
    key = fake_s3.presign_calls[0]["Params"]["Key"]
    assert key.startswith(f"uploads/g1/{media_id}/")
    assert key.count("/") == 3  # uploads / garden / media / file — the layout ingestion parses
    assert ".." not in key


def test_display_key_prefers_processed_copies_once_ready():
    base = {"s3_key": "uploads/g/m/p.jpg"}
    ready = dict(
        base,
        processing_status="ready",
        analysis_s3_key="derived/g/m/analysis.jpg",
        thumb_s3_key="derived/g/m/thumb.jpg",
    )
    assert handler._display_key(ready) == "derived/g/m/analysis.jpg"
    assert handler._display_key(ready, "thumb") == "derived/g/m/thumb.jpg"
    # Still processing, failed, or uploaded before ingestion existed: the original.
    assert handler._display_key(dict(base, processing_status="pending"), "thumb") == base["s3_key"]
    assert handler._display_key(dict(base, processing_status="failed")) == base["s3_key"]
    assert handler._display_key({"s3_key": "g/m/legacy.jpg"}) == "g/m/legacy.jpg"


def test_list_plants_uses_the_thumbnail_when_processed(monkeypatch):
    items = [{"plant_id": "p1", "species": "Coconut", "stage": "new", "media_id": "m1"}]
    raw = {
        "media_id": {"S": "m1"},
        "s3_key": {"S": "uploads/g1/m1/p.jpg"},
        "processing_status": {"S": "ready"},
        "thumb_s3_key": {"S": "derived/g1/m1/thumb.jpg"},
        "analysis_s3_key": {"S": "derived/g1/m1/analysis.jpg"},
    }
    fake_client = FakeClient(batch_get_responses=[{"Responses": {handler.APP_TABLE_NAME: [raw]}}])
    monkeypatch.setattr(handler, "_table", FakeTable(query_response={"Items": items}))
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_s3", FakeS3())
    body = json.loads(handler.list_plants(_event("GET", PLANTS, path_params=G1))["body"])
    assert "derived/g1/m1/thumb.jpg" in body[0]["photoUrl"]


# --- ADR-0034: chat messages with photos/tasks, check-in proposals, confirm/reject -------------

GOAL_PATH = {"gardenId": "g1", "goalId": "goal-1"}
TASK_ITEM = {
    "task_id": "t1",
    "goal_id": "goal-1",
    "title": "Check drip irrigation",
    "status": "pending",
    "plant_id": "p1",
}
MEDIA_ITEM = {"media_id": "m10", "s3_key": "uploads/g1/m10/p.jpg"}
CHECKIN = "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins"
ACTION_CONFIRM = "/gardens/{gardenId}/goals/{goalId}/actions/{actionId}/confirm"
ACTION_REJECT = "/gardens/{gardenId}/goals/{goalId}/actions/{actionId}/reject"
ACTION_PATH = {**GOAL_PATH, "actionId": "a1"}


def _msg_event(body):
    return _event(
        "POST", "/gardens/{gardenId}/goals/{goalId}/messages", body=body, path_params=GOAL_PATH
    )


def _api_tables(monkeypatch, responses):
    table, events = FakeTable(get_item_responses=responses), FakeEvents()
    monkeypatch.setattr(handler, "_table", table)
    monkeypatch.setattr(handler, "_events", events)
    return table, events


def test_message_with_photo_and_task_is_a_checkin(monkeypatch):
    table, events = _api_tables(
        monkeypatch, {"MEDIA#m10": {"Item": MEDIA_ITEM}, "TASK#goal-1#t1": {"Item": TASK_ITEM}}
    )
    resp = handler.post_goal_message(_msg_event({"mediaIds": ["m10"], "taskId": "t1"}))
    assert resp["statusCode"] == 202
    (msg,) = table.put_calls
    assert msg["media_ids"] == ["m10"] and msg["task_id"] == "t1" and msg["content"] == ""
    assert json.loads(resp["body"]) == {"messageId": msg["message_id"]}
    assert events.put_calls[0][0]["DetailType"] == "goal.message.received"
    # The photo is linked to this goal/message for traceability.
    assert table.update_calls[0]["ExpressionAttributeValues"][":g"] == "goal-1"


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({}, 400),
        ({"content": "   "}, 400),
        ({"mediaIds": "m10"}, 400),
        ({"mediaIds": ["a", "b", "c", "d", "e"]}, 400),
        ({"content": "hi", "taskId": 5}, 400),
        ({"mediaIds": ["missing"]}, 404),
        ({"taskId": "missing"}, 404),
    ],
)
def test_message_validation(monkeypatch, body, status):
    _api_tables(monkeypatch, {"MEDIA#m10": {"Item": MEDIA_ITEM}})
    assert handler.post_goal_message(_msg_event(body))["statusCode"] == status


def test_message_checkin_on_a_done_task_is_409(monkeypatch):
    _api_tables(monkeypatch, {"TASK#goal-1#t1": {"Item": dict(TASK_ITEM, status="done")}})
    assert handler.post_goal_message(_msg_event({"taskId": "t1"}))["statusCode"] == 409


def test_task_checkin_button_now_proposes_instead_of_completing(monkeypatch):
    table, events = _api_tables(
        monkeypatch, {"MEDIA#m10": {"Item": MEDIA_ITEM}, "TASK#goal-1#t1": {"Item": TASK_ITEM}}
    )
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    event = _event(
        "POST", CHECKIN, body={"mediaId": "m10"}, path_params={**GOAL_PATH, "taskId": "t1"}
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 202
    body = json.loads(resp["body"])
    assert body["status"] == "pending_confirmation" and body["taskId"] == "t1"
    (msg,) = table.put_calls
    assert msg["task_id"] == "t1" and msg["media_ids"] == ["m10"]
    assert msg["content"] == "Check-in: Check drip irrigation"
    assert fake_client.calls == []  # the task is NOT marked done here
    assert events.put_calls[0][0]["DetailType"] == "goal.message.received"


def test_task_checkin_on_a_done_task_is_409(monkeypatch):
    _api_tables(
        monkeypatch,
        {
            "MEDIA#m10": {"Item": MEDIA_ITEM},
            "TASK#goal-1#t1": {"Item": dict(TASK_ITEM, status="done")},
        },
    )
    event = _event(
        "POST", CHECKIN, body={"mediaId": "m10"}, path_params={**GOAL_PATH, "taskId": "t1"}
    )
    assert handler.post_task_checkin(event)["statusCode"] == 409


def _action(kind, params, status="proposed"):
    return {
        "Item": {
            "action_id": "a1",
            "goal_id": "goal-1",
            "type": kind,
            "params": params,
            "status": status,
        }
    }


def _confirm(monkeypatch, responses, client=None, resource=ACTION_CONFIRM):
    table, _ = _api_tables(monkeypatch, responses)
    fake_client = client or FakeClient()
    monkeypatch.setattr(handler, "_client", fake_client)
    resp = handler.handler(_event("POST", resource, path_params=ACTION_PATH), None)
    return resp, table, fake_client


def _ops_by_sk(transact_call):
    out = {}
    for item in transact_call:
        ((op, body),) = item.items()
        key = body["Key"]["sk"]["S"] if "Key" in body else body["Item"]["sk"]["S"]
        out[(op, key)] = body
    return out


def test_confirm_complete_task_marks_done_links_photo_and_records_event(monkeypatch):
    params = {"task_id": "t1", "media_id": "m10", "feedback": "Looks good"}
    resp, table, client = _confirm(
        monkeypatch,
        {
            "ACTION#goal-1#a1": _action("complete_task", params),
            "TASK#goal-1#t1": {"Item": TASK_ITEM},
        },
    )
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {
        "actionId": "a1",
        "status": "confirmed",
        "type": "complete_task",
    }
    ops = _ops_by_sk(client.calls[0])
    assert ops[("Update", "ACTION#goal-1#a1")]["ConditionExpression"] == "#s = :proposed"
    task = ops[("Update", "TASK#goal-1#t1")]
    assert "REMOVE gsi1pk, gsi1sk, due_date" in task["UpdateExpression"]
    assert task["ExpressionAttributeValues"][":done"] == {"S": "done"}
    assert task["ExpressionAttributeValues"][":fb"] == {"S": "Looks good"}
    assert task["ConditionExpression"] == "#status = :pending"
    media = ops[("Update", "MEDIA#m10")]
    assert media["ExpressionAttributeValues"][":plant_id"] == {"S": "p1"}
    assert media["ExpressionAttributeValues"][":plant_link_source"] == {"S": "confirmed"}
    assert table.put_calls[0]["type"] == "task.checkin"


def test_confirm_set_plant_moves_the_photo_after_checking_the_plant_exists(monkeypatch):
    resp, _, client = _confirm(
        monkeypatch,
        {"ACTION#goal-1#a1": _action("set_plant", {"media_id": "m10", "plant_id": "p10"})},
    )
    assert resp["statusCode"] == 200
    ops = _ops_by_sk(client.calls[0])
    assert ("ConditionCheck", "PLANT#p10") in ops
    assert ops[("Update", "MEDIA#m10")]["ExpressionAttributeValues"][":plant_id"] == {"S": "p10"}


def test_confirm_set_goal_plant_updates_goal_media_and_unlinked_tasks(monkeypatch):
    table, _ = _api_tables(
        monkeypatch,
        {"ACTION#goal-1#a1": _action("set_goal_plant", {"plant_id": "p10", "media_id": "m10"})},
    )
    table._query_response = {
        "Items": [{"pk": "GARDEN#g1", "sk": "TASK#goal-1#t1", "task_id": "t1"}]
    }
    client = FakeClient()
    monkeypatch.setattr(handler, "_client", client)
    resp = handler.confirm_action(_event("POST", ACTION_CONFIRM, path_params=ACTION_PATH))
    assert resp["statusCode"] == 200
    ops = _ops_by_sk(client.calls[0])
    assert ops[("Update", "GOAL#goal-1")]["ExpressionAttributeValues"][":p"] == {"S": "p10"}
    assert ("Update", "MEDIA#m10") in ops
    assert table.update_calls[0]["ExpressionAttributeValues"][":p"] == "p10"  # task inherits


def test_confirm_create_plant_copies_species_from_the_plantation(monkeypatch):
    table, _ = _api_tables(
        monkeypatch, {"ACTION#goal-1#a1": _action("create_plant", {"stake_no": 59})}
    )
    table._query_response = {
        "Items": [
            {
                "sk": "PLANT#p1",
                "plant_id": "p1",
                "stake_no": 1,
                "species": "Coconut",
                "variety": "Kalpa Samrudhi",
                "stage": "young",
            }
        ]
    }
    client = FakeClient()
    monkeypatch.setattr(handler, "_client", client)
    resp = handler.confirm_action(_event("POST", ACTION_CONFIRM, path_params=ACTION_PATH))
    assert resp["statusCode"] == 200
    plant_put = client.calls[0][0]["Put"]["Item"]
    assert plant_put["species"]["S"] == "Coconut" and plant_put["stake_no"]["N"] == "59"


def test_confirm_is_idempotent_and_refuses_resolved_actions(monkeypatch):
    confirmed = {"ACTION#goal-1#a1": _action("set_plant", {}, status="confirmed")}
    resp, _, client = _confirm(monkeypatch, confirmed)
    assert resp["statusCode"] == 200 and client.calls == []
    superseded = {"ACTION#goal-1#a1": _action("set_plant", {}, status="superseded")}
    assert _confirm(monkeypatch, superseded)[0]["statusCode"] == 409
    assert _confirm(monkeypatch, {})[0]["statusCode"] == 404


def test_confirm_when_the_task_was_already_done_is_409(monkeypatch):
    cancelled = FakeClient(
        raise_on_transact=FakeClientError(
            "TransactionCanceledException", ["None", "ConditionalCheckFailed"]
        )
    )
    resp, _, _ = _confirm(
        monkeypatch,
        {
            "ACTION#goal-1#a1": _action("complete_task", {"task_id": "t1"}),
            "TASK#goal-1#t1": {"Item": TASK_ITEM},
        },
        client=cancelled,
    )
    assert resp["statusCode"] == 409
    assert "no longer applies" in json.loads(resp["body"])["message"]


def test_reject_dismisses_and_is_idempotent(monkeypatch):
    resp, table, client = _confirm(
        monkeypatch, {"ACTION#goal-1#a1": _action("set_plant", {})}, resource=ACTION_REJECT
    )
    assert resp["statusCode"] == 200
    assert table.update_calls[0]["ExpressionAttributeValues"][":rejected"] == "rejected"
    assert client.calls == []

    already = FakeTable(
        get_item_responses={"ACTION#goal-1#a1": _action("set_plant", {}, status="rejected")},
        raise_on_update=FakeClientError("ConditionalCheckFailedException"),
    )
    monkeypatch.setattr(handler, "_table", already)
    resp = handler.reject_action(_event("POST", ACTION_REJECT, path_params=ACTION_PATH))
    assert resp["statusCode"] == 200


def test_goal_detail_returns_actions_and_message_photos_conforming_to_schema(monkeypatch):
    goal = {
        "goal_id": "goal-1",
        "description": "track",
        "type": "general",
        "status": "PlanProposed",
    }
    msg = {
        "sk": "GOALMSG#goal-1#2026-11-23T10:00#u1",
        "message_id": "u1",
        "role": "user",
        "content": "",
        "created_at": "2026-11-23T10:00",
        "media_ids": ["m10"],
        "task_id": "t1",
    }
    reply = {
        "sk": "GOALMSG#goal-1#2026-11-23T10:01#a",
        "message_id": "a",
        "role": "assistant",
        "content": "ok",
        "created_at": "2026-11-23T10:01",
        "action_id": "a1",
    }
    action = {
        "sk": "ACTION#goal-1#a1",
        "action_id": "a1",
        "type": "set_goal_plant",
        "summary": "Is this #71 or #17?",
        "status": "proposed",
        "created_at": "2026-11-23T10:01",
        "params": {
            "plant_id": "p71",
            "candidates": [
                {"stake_no": 71, "plant_id": "p71"},
                {"stake_no": 17, "plant_id": "p17"},
            ],
        },
    }
    ready_media = dict(
        MEDIA_ITEM, processing_status="ready", thumb_s3_key="derived/g1/m10/thumb.jpg"
    )
    table = FakeTable(
        get_item_responses={"GOAL#goal-1": {"Item": goal}, "MEDIA#m10": {"Item": ready_media}},
        query_responses=[{"Items": []}, {"Items": [msg, reply]}, {"Items": [action]}],
    )
    monkeypatch.setattr(handler, "_table", table)
    monkeypatch.setattr(handler, "_s3", FakeS3())
    event = _event("GET", "/gardens/{gardenId}/goals/{goalId}", path_params=GOAL_PATH)
    body = json.loads(handler.get_goal_detail(event)["body"])
    user, assistant = body["messages"]
    assert user["taskId"] == "t1"
    assert "derived/g1/m10/thumb.jpg" in user["media"][0]["downloadUrl"]
    assert assistant["actionId"] == "a1"
    assert body["actions"] == [
        {
            "actionId": "a1",
            "type": "set_goal_plant",
            "summary": "Is this #71 or #17?",
            "status": "proposed",
            "createdAt": "2026-11-23T10:01",
            "candidates": [71, 17],
        }
    ]
    _validate_against("GoalDetail", body)
