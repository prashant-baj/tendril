"""Unit tests for the Weather tool Lambda (AF-03). No live network access — `fetch_forecast`'s
underlying HTTP call is monkeypatched, matching the fake-boto3/fake-HTTP convention already used
across `app/api`, `app/orchestrator`, and `agents/hello_agent`'s test suites.
"""

import json

import handler


def test_handler_returns_forecast_for_valid_coordinates(monkeypatch):
    monkeypatch.setattr(
        handler,
        "fetch_forecast",
        lambda lat, lon: {
            "latitude": lat,
            "longitude": lon,
            "temperatureC": 21.5,
            "precipitationMm": 0.0,
            "weatherCode": 1,
        },
    )
    resp = handler.handler({"queryStringParameters": {"lat": "12.97", "lon": "77.59"}}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["latitude"] == 12.97
    assert body["temperatureC"] == 21.5


def test_handler_rejects_missing_coordinates():
    resp = handler.handler({"queryStringParameters": {"lat": "12.97"}}, None)
    assert resp["statusCode"] == 400
    assert "lon" in json.loads(resp["body"])["error"]


def test_handler_rejects_non_numeric_coordinates():
    resp = handler.handler({"queryStringParameters": {"lat": "north", "lon": "77.59"}}, None)
    assert resp["statusCode"] == 400
    assert "lat" in json.loads(resp["body"])["error"]


def test_handler_rejects_out_of_range_coordinates():
    resp = handler.handler({"queryStringParameters": {"lat": "999", "lon": "77.59"}}, None)
    assert resp["statusCode"] == 400
    assert "lat" in json.loads(resp["body"])["error"]


def test_handler_handles_no_query_params():
    resp = handler.handler({}, None)
    assert resp["statusCode"] == 400


def test_handler_returns_502_on_upstream_failure(monkeypatch):
    import urllib.error

    def boom(lat, lon):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(handler, "fetch_forecast", boom)
    resp = handler.handler({"queryStringParameters": {"lat": "12.97", "lon": "77.59"}}, None)
    assert resp["statusCode"] == 502
    assert "unavailable" in json.loads(resp["body"])["error"]
