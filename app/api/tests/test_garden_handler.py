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
from decimal import Decimal
from pathlib import Path

import garden_handler as handler


class FakeClient:
    def __init__(self, raise_on_transact: Exception | None = None):
        self.raise_on_transact = raise_on_transact
        self.calls: list[list[dict]] = []

    def transact_write_items(self, TransactItems):
        if self.raise_on_transact:
            raise self.raise_on_transact
        self.calls.append(TransactItems)


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
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg"},
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
    assert presign["Params"]["Key"] == f"g1/{body['mediaId']}/tomato.jpg"

    # the Media record was written immediately (data-architecture.md §4)
    assert len(fake_table.put_calls) == 1
    media_item = fake_table.put_calls[0]
    assert media_item["pk"] == "GARDEN#g1"
    assert media_item["sk"] == f"MEDIA#{body['mediaId']}"
    assert media_item["s3_key"] == f"g1/{body['mediaId']}/tomato.jpg"
    assert media_item["content_type"] == "image/jpeg"


def test_create_media_upload_missing_user_id_header(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg"},
        headers={},
        path_params={"gardenId": "g1"},
    )
    resp = handler.create_media_upload(event)
    assert resp["statusCode"] == 400


def test_create_media_upload_missing_garden_id(monkeypatch):
    event = _event(
        "POST",
        "/gardens/{gardenId}/media",
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg"},
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
        body={"contentType": "image/jpeg"},
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
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg"},
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
        get_item_responses={"MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/leaf.jpg"}}},
    )
    monkeypatch.setattr(handler, "_table", fake_table)
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

    assert len(fake_table.put_calls) == 1
    goal_item = fake_table.put_calls[0]
    assert goal_item["pk"] == "GARDEN#g1"
    assert goal_item["sk"] == f"GOAL#{body['goalId']}"
    assert goal_item["description"] == "leaves turning yellow"
    assert goal_item["status"] == "Intake"
    assert "media_ids" not in goal_item

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
            {"Items": [{"task_id": "t1", "title": "Water", "detail": "Deeply", "scope": "plant"}]},
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


# --- post_task_checkin (images-completion: Plant/Goal/Task/Media traceability) -------------


def test_post_task_checkin_happy_path_no_plant(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "TASK#goal-1#task-1": {"Item": {"task_id": "task-1", "goal_id": "goal-1"}},
            "MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/photo.jpg"}},
        }
    )
    fake_client = FakeClient()
    fake_events = FakeEvents()
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_events", fake_events)

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "media-1"},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
    )
    resp = handler.post_task_checkin(event)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"taskId": "task-1", "status": "done"}

    assert len(fake_events.put_calls) == 1
    published = fake_events.put_calls[0][0]
    assert published["DetailType"] == "task.checkin.received"
    assert json.loads(published["Detail"]) == {
        "gardenId": "g1",
        "goalId": "goal-1",
        "taskId": "task-1",
    }

    assert len(fake_client.calls) == 1
    items = fake_client.calls[0]
    task_update = next(
        i["Update"] for i in items if i["Update"]["Key"]["sk"]["S"] == "TASK#goal-1#task-1"
    )
    media_update = next(
        i["Update"] for i in items if i["Update"]["Key"]["sk"]["S"] == "MEDIA#media-1"
    )
    assert task_update["ExpressionAttributeValues"][":status"]["S"] == "done"
    assert task_update["ExpressionAttributeValues"][":mid"]["S"] == "media-1"
    assert media_update["ExpressionAttributeValues"][":tid"]["S"] == "task-1"
    assert media_update["ExpressionAttributeValues"][":gid"]["S"] == "goal-1"
    assert ":pid" not in media_update["ExpressionAttributeValues"]


def test_post_task_checkin_propagates_plant_id_to_media(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "TASK#goal-1#task-1": {
                "Item": {"task_id": "task-1", "goal_id": "goal-1", "plant_id": "plant-1"}
            },
            "MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/photo.jpg"}},
        }
    )
    fake_client = FakeClient()
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_client", fake_client)
    monkeypatch.setattr(handler, "_events", FakeEvents())

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "media-1"},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
    )
    resp = handler.post_task_checkin(event)

    assert resp["statusCode"] == 200
    media_update = next(
        i["Update"]
        for i in fake_client.calls[0]
        if i["Update"]["Key"]["sk"]["S"] == "MEDIA#media-1"
    )
    assert media_update["ExpressionAttributeValues"][":pid"]["S"] == "plant-1"


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


def test_post_task_checkin_event_publish_failure_still_returns_200(monkeypatch):
    # Deliberately NOT folded into the transaction's try/except (PA-05): the check-in's core
    # contract (done + photo linked) already committed by the time the event publish runs, so
    # losing just the bonus agent-feedback event shouldn't turn a successful check-in into a 500.
    fake_table = FakeTable(
        get_item_responses={
            "TASK#goal-1#task-1": {"Item": {"task_id": "task-1", "goal_id": "goal-1"}},
            "MEDIA#media-1": {"Item": {"s3_key": "g1/media-1/photo.jpg"}},
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_client", FakeClient())
    monkeypatch.setattr(handler, "_events", FakeEvents(raise_on_put=RuntimeError("boom")))

    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "media-1"},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
    )
    resp = handler.post_task_checkin(event)

    assert resp["statusCode"] == 200
    assert json.loads(resp["body"]) == {"taskId": "task-1", "status": "done"}


def test_handler_routes_post_task_checkin(monkeypatch):
    fake_table = FakeTable(
        get_item_responses={
            "TASK#goal-1#task-1": {"Item": {"task_id": "task-1"}},
            "MEDIA#media-1": {"Item": {"s3_key": "k"}},
        }
    )
    monkeypatch.setattr(handler, "_table", fake_table)
    monkeypatch.setattr(handler, "_client", FakeClient())
    monkeypatch.setattr(handler, "_events", FakeEvents())
    event = _event(
        "POST",
        "/gardens/{gardenId}/goals/{goalId}/tasks/{taskId}/checkins",
        body={"mediaId": "media-1"},
        path_params={"gardenId": "g1", "goalId": "goal-1", "taskId": "task-1"},
    )
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 200


# --- get_goal_detail: task media/plantId resolution (images completion) --------------------


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
        body={"contentType": "image/jpeg", "fileName": "tomato.jpg"},
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
