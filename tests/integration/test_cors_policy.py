"""CORS preflight contract for the api-server (Plan 06.14 task_06_14_14).

Regression for secrets-config-4: the pre-Plan-06.14 app configured the
CORS middleware with `allow_methods=["*"]` and `allow_headers=["*"]` while
also setting `allow_credentials=True`. With credentials enabled the
wildcard is meaningless — the browser rejects a literal `*` — so Starlette
silently reflects whatever the request asked for, which is the opposite of
an allow-list. The middleware is now pinned to the explicit verbs this API
serves and the headers the admin-panel actually sends.

These tests drive the real FastAPI app through `TestClient` (no DB needed:
the CORS layer answers the OPTIONS preflight before any route runs) and
assert the preflight response advertises the explicit method/header lists,
never `*`.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

_ALLOWED_ORIGIN = "http://localhost:3000"
_FOREIGN_ORIGIN = "https://evil.example.com"

# Mirror the contract pinned in api_server.main so a drift in either side
# is caught by the assertions below.
_EXPECTED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}
_EXPECTED_HEADERS = {"authorization", "content-type", "x-tenant-id", "x-request-id"}


@pytest.fixture()
def cors_client(monkeypatch: pytest.MonkeyPatch):
    """Build the app fresh with a known CORS origin allow-list."""
    monkeypatch.setenv("API_SERVER_CORS_ALLOWED_ORIGINS", json.dumps([_ALLOWED_ORIGIN]))
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.config import get_settings

    get_settings.cache_clear()
    try:
        from api_server.main import create_app

        app = create_app()
        with TestClient(app) as client:
            yield client
    finally:
        get_settings.cache_clear()


def _preflight(
    client: TestClient, *, origin: str, method: str = "POST", req_headers: str = "authorization"
):
    return client.options(
        "/me",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": req_headers,
        },
    )


# ===========================================================================
# Happy path — preflight from the allowed origin returns explicit lists.
# ===========================================================================
def test_preflight_advertises_explicit_methods_not_wildcard(cors_client: TestClient) -> None:
    resp = _preflight(cors_client, origin=_ALLOWED_ORIGIN)
    assert resp.status_code == 200, resp.text

    allow_methods = resp.headers["access-control-allow-methods"]
    # Never the reflect-everything wildcard.
    assert allow_methods.strip() != "*"
    returned = {m.strip().upper() for m in allow_methods.split(",")}
    assert returned == _EXPECTED_METHODS


def test_preflight_advertises_explicit_headers_not_wildcard(cors_client: TestClient) -> None:
    # Request all four headers the admin-panel sends; the middleware must
    # echo them back because each is in the explicit allow-list.
    resp = _preflight(
        cors_client,
        origin=_ALLOWED_ORIGIN,
        req_headers="authorization, content-type, x-tenant-id, x-request-id",
    )
    assert resp.status_code == 200, resp.text

    allow_headers = resp.headers["access-control-allow-headers"]
    assert allow_headers.strip() != "*"
    returned = {h.strip().lower() for h in allow_headers.split(",")}
    assert _EXPECTED_HEADERS.issubset(returned)


def test_preflight_echoes_allowed_origin_with_credentials(cors_client: TestClient) -> None:
    resp = _preflight(cors_client, origin=_ALLOWED_ORIGIN)
    assert resp.status_code == 200, resp.text
    # Credentials mode: the origin is echoed verbatim (never `*`), and the
    # allow-credentials flag is present.
    assert resp.headers["access-control-allow-origin"] == _ALLOWED_ORIGIN
    assert resp.headers["access-control-allow-origin"] != "*"
    assert resp.headers["access-control-allow-credentials"] == "true"


# ===========================================================================
# Denial path — a foreign origin is not granted CORS access.
# ===========================================================================
def test_preflight_from_foreign_origin_is_denied(cors_client: TestClient) -> None:
    resp = _preflight(cors_client, origin=_FOREIGN_ORIGIN)
    # Starlette answers the OPTIONS with 400 ("Disallowed CORS origin")
    # and, crucially, never adds an Access-Control-Allow-Origin header that
    # would let the browser use the response.
    assert resp.headers.get("access-control-allow-origin") != _FOREIGN_ORIGIN
    assert "access-control-allow-origin" not in resp.headers


# ===========================================================================
# Edge case — a method outside the allow-list is rejected at preflight.
# ===========================================================================
def test_preflight_disallowed_method_rejected(cors_client: TestClient) -> None:
    # TRACE is not in the pinned method list.
    resp = _preflight(cors_client, origin=_ALLOWED_ORIGIN, method="TRACE")
    assert resp.status_code == 400
    allow_methods = resp.headers.get("access-control-allow-methods", "")
    assert "TRACE" not in {m.strip().upper() for m in allow_methods.split(",") if m.strip()}


def test_preflight_disallowed_header_rejected(cors_client: TestClient) -> None:
    # A header outside the explicit allow-list (X-Custom-Foo) is rejected.
    resp = _preflight(
        cors_client,
        origin=_ALLOWED_ORIGIN,
        req_headers="x-custom-foo",
    )
    assert resp.status_code == 400
    allow_headers = resp.headers.get("access-control-allow-headers", "")
    assert "x-custom-foo" not in allow_headers.lower()
