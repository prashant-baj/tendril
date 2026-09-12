"""Unit tests for the garden Client API handler (OB-01).

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
    def __init__(self, get_item_response=None, raise_on_get=None):
        self._get_item_response = get_item_response or {}
        self.raise_on_get = raise_on_get

    def get_item(self, Key):
        if self.raise_on_get:
            raise self.raise_on_get
        return self._get_item_response


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


def test_handler_unknown_route_returns_404():
    event = _event("DELETE", "/gardens/{gardenId}", path_params={"gardenId": "g1"})
    resp = handler.handler(event, None)
    assert resp["statusCode"] == 404
