"""Contract tests for the Client API key requirement (ADR-0021, change `add-api-key-auth`).

Every operation except the CORS `OPTIONS` preflight must require the `api_key` security scheme
(an `x-api-key` header); `OPTIONS` must stay open (`security: []`) so the browser preflight,
which never carries the key, is not rejected.
"""

from pathlib import Path

import yaml

SPEC_PATH = Path(__file__).resolve().parents[1] / "openapi.yaml"
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def _spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def _operations(spec: dict):
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method in HTTP_METHODS:
                yield path, method, operation


def test_api_key_security_scheme_is_a_header_named_x_api_key():
    scheme = _spec()["components"]["securitySchemes"]["api_key"]
    assert scheme == {"type": "apiKey", "in": "header", "name": "x-api-key"}


def test_spec_has_operations_to_check():
    # Guards the two tests below against passing vacuously.
    methods = {m for _, m, _ in _operations(_spec())}
    assert "options" in methods
    assert len(methods) > 1


def test_every_non_options_operation_requires_the_api_key():
    missing = [
        f"{method.upper()} {path}"
        for path, method, operation in _operations(_spec())
        if method != "options" and operation.get("security") != [{"api_key": []}]
    ]
    assert not missing, f"operations without the api_key requirement: {missing}"


def test_options_operations_stay_open():
    wrong = [
        f"{method.upper()} {path}"
        for path, method, operation in _operations(_spec())
        if method == "options" and operation.get("security") != []
    ]
    assert not wrong, f"OPTIONS operations that are not open (security: []): {wrong}"


def test_no_global_security_requirement_overrides_the_per_operation_ones():
    # A top-level `security` would apply to OPTIONS unless each opts out; keep the contract
    # explicit per operation (ADR-0021 decision 1) so a new operation cannot inherit silently.
    assert "security" not in _spec()


def test_cors_preflight_allows_the_api_key_header():
    spec = _spec()
    for path, method, operation in _operations(spec):
        if method != "options":
            continue
        params = operation["x-amazon-apigateway-integration"]["responses"]["default"][
            "responseParameters"
        ]
        allowed = params["method.response.header.Access-Control-Allow-Headers"].lower()
        assert "x-api-key" in allowed, f"OPTIONS {path} does not allow x-api-key"
