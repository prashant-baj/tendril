"""Weather Tool Lambda (AF-03's reference tool) — a single `GET ?lat=&lon=` operation invoked
over a Lambda Function URL with IAM auth, never API Gateway (architecture.md §4.2: tools are
APIs, not code baked into an agent).

Calls Open-Meteo's free, keyless forecast API (https://open-meteo.com) so this returns a real
forecast, not a stub.

Deviation from ADR-0011's exact `SpecRestApi` mechanism, documented in `openapi.yaml` alongside
this file: this is an internal, IAM-authenticated, server-to-server tool with exactly one
operation, never called by the public frontend — a full API Gateway + OpenAPI-import stack for
that is unwarranted machinery. A Lambda Function URL is the same "real HTTP API" posture
(architecture.md §4.2) at a fraction of the infra; the OpenAPI file is still the contract, just
not a CDK deploy artifact this time.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

# force=True: the standard Lambda Python runtime pre-attaches its own handler to the root
# logger before user code runs, and basicConfig() is a documented no-op once handlers already
# exist — the same silent-logging bug confirmed live in app/orchestrator/orchestrator.py, which
# is the same plain-Lambda base image (ADR-0014). force=True replaces that handler instead of
# being ignored by it.
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), force=True)
logger = logging.getLogger("tendril.tools.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_SECONDS = 8


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _parse_coordinate(raw: str | None, *, low: float, high: float, name: str) -> float:
    if raw is None:
        raise ValueError(f"missing required query parameter '{name}'")
    try:
        value = float(raw)
    except ValueError as e:
        raise ValueError(f"'{name}' must be a number") from e
    if not (low <= value <= high):
        raise ValueError(f"'{name}' must be between {low} and {high}")
    return value


def fetch_forecast(lat: float, lon: float) -> dict[str, Any]:
    url = (
        f"{FORECAST_URL}?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,precipitation,weather_code"
        "&forecast_days=1"
    )
    with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_SECONDS) as resp:  # noqa: S310
        data = json.loads(resp.read())
    current = data.get("current", {})
    return {
        "latitude": lat,
        "longitude": lon,
        "temperatureC": current.get("temperature_2m"),
        "precipitationMm": current.get("precipitation"),
        "weatherCode": current.get("weather_code"),
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    params = event.get("queryStringParameters") or {}
    try:
        lat = _parse_coordinate(params.get("lat"), low=-90, high=90, name="lat")
        lon = _parse_coordinate(params.get("lon"), low=-180, high=180, name="lon")
    except ValueError as e:
        return _response(400, {"error": str(e)})

    try:
        forecast = fetch_forecast(lat, lon)
    except urllib.error.URLError as e:
        logger.exception("weather_fetch_failed lat=%s lon=%s", lat, lon)
        return _response(502, {"error": f"upstream weather service unavailable: {e}"})

    return _response(200, forecast)
