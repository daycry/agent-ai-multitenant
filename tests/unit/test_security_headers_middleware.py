"""Baseline security response headers + `/docs` withdrawal (prod-09, api-7).

Two findings in one task:

  * the api-server sent NO hardening headers — no ``nosniff``, no frame policy,
    no ``Referrer-Policy`` (which matters here more than usual: api-server URLs
    carry SIGNED query strings, ``/review/{id}?exp=&sig=`` and ``?token=``, and a
    default ``Referer`` leaks them to every third-party host a page touches);
  * ``/docs`` and ``/openapi.json`` published the COMPLETE internal schema —
    ``/admin/*``, ``/internal/agent/*``, every tenant route — unauthenticated.

The tests cover the invariants AND the two conditionals that are easy to get
wrong (HSTS only over TLS and outside dev; ``SAMEORIGIN`` for the review surface
that legitimately frames its own app preview). They run against the real
middleware and the real ``create_app``; nothing is mocked but the environment.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.middleware.security_headers import SecurityHeadersMiddleware
from api_server.routing_introspection import route_paths
from starlette.requests import Request

pytestmark = pytest.mark.unit


def _request(path: str = "/healthz", *, scheme: str = "http", **headers: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "scheme": scheme,
            "server": ("api", 8000),
            "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        }
    )


def _mw(environment: str = "prod") -> SecurityHeadersMiddleware:
    async def _noop(*_a: Any, **_k: Any) -> None:  # pragma: no cover - never called
        return None

    return SecurityHeadersMiddleware(_noop, environment=environment)


# ---------------------------------------------------------------------------
# The three unconditional headers
# ---------------------------------------------------------------------------
def test_the_baseline_headers_are_always_present() -> None:
    headers = _mw("dev").headers_for(_request())
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "DENY"


def test_referrer_policy_does_not_leak_signed_urls() -> None:
    """``no-referrer``, not ``strict-origin-when-cross-origin``.

    The origin-only policies still send the ORIGIN cross-site, which is harmless,
    but any same-origin navigation would carry the full URL — and these URLs are
    the credential (``?sig=``, ``?token=``). Pinned because "tighten it later"
    never happens and the weaker value looks equally responsible in a diff.
    """
    assert _mw().headers_for(_request("/review/abc"))["Referrer-Policy"] == "no-referrer"


# ---------------------------------------------------------------------------
# Framing: DENY, except the review surface it would break
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "path", ["/healthz", "/admin/backup/schedule", "/api/v1/projects", "/reviewer-ish"]
)
def test_frame_options_deny_everywhere_else(path: str) -> None:
    assert _mw().headers_for(_request(path))["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize(
    "path",
    [
        "/review/2f0c-1111",
        "/review/2f0c-1111/app/",
        "/review/2f0c-1111/app/static/main.js",
    ],
)
def test_review_surface_allows_same_origin_framing(path: str) -> None:
    """``DENY`` blocks SAME-ORIGIN framing too, and the review SPA is built to
    embed the app preview it proxies at ``/review/{id}/app/`` (ADR 0129/0130 —
    ``routers/review.py`` even carries an ``app_configured`` flag so the SPA can
    avoid showing "un iframe roto"). A blanket DENY would ship a header that
    breaks that preview the moment the SPA bundle lands."""
    assert _mw().headers_for(_request(path))["X-Frame-Options"] == "SAMEORIGIN"


# ---------------------------------------------------------------------------
# HSTS: only over TLS, only outside dev
# ---------------------------------------------------------------------------
def test_hsts_over_direct_tls_in_prod() -> None:
    headers = _mw("prod").headers_for(_request(scheme="https"))
    assert headers["Strict-Transport-Security"].startswith("max-age=31536000")
    assert "includeSubDomains" in headers["Strict-Transport-Security"]


def test_hsts_behind_a_tls_terminating_proxy() -> None:
    """prod-01 terminates TLS in front of the api-server, so the request arrives
    as plain HTTP with ``X-Forwarded-Proto: https``. Without this branch HSTS
    would never be emitted in the actual production topology."""
    headers = _mw("prod").headers_for(_request(**{"X-Forwarded-Proto": "https"}))
    assert "Strict-Transport-Security" in headers


def test_hsts_handles_a_multi_hop_forwarded_proto() -> None:
    """A chain of proxies appends: ``https, http``. The left-most hop is the one
    the CLIENT spoke, same convention as ``X-Forwarded-For`` in
    ``auth.deps.get_client_ip``."""
    headers = _mw("prod").headers_for(_request(**{"X-Forwarded-Proto": "https, http"}))
    assert "Strict-Transport-Security" in headers


def test_no_hsts_over_plain_http() -> None:
    """Sending HSTS on a plain-HTTP response is meaningless (browsers ignore it)
    and would be a lie about the deployment."""
    assert "Strict-Transport-Security" not in _mw("prod").headers_for(_request())


def test_no_hsts_in_dev_even_over_tls() -> None:
    """The developer foot-gun this guard exists for: an HSTS header from a local
    HTTPS tunnel pins ``localhost`` to HTTPS in the browser for a YEAR, breaking
    every other local service on the machine."""
    assert "Strict-Transport-Security" not in _mw("dev").headers_for(_request(scheme="https"))


# ---------------------------------------------------------------------------
# Handler-set headers win
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_handler_set_header_is_not_overwritten() -> None:
    """A route that sets its own policy stays in control (a future CSP for the
    review SPA must not be clobbered by the blanket middleware)."""
    sent: list[dict[str, Any]] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-frame-options", b"SAMEORIGIN")],
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    mw = SecurityHeadersMiddleware(app, environment="prod")

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    async def receive() -> dict[str, Any]:  # pragma: no cover - never called
        return {"type": "http.request"}

    await mw(dict(_request("/healthz").scope), receive, send)

    headers = dict(sent[0]["headers"])
    assert headers[b"x-frame-options"] == b"SAMEORIGIN"  # the handler's, kept
    assert headers[b"x-content-type-options"] == b"nosniff"  # ours, added


@pytest.mark.asyncio
async def test_websocket_scopes_pass_through_untouched() -> None:
    """Response headers are meaningless for a WS handshake; the middleware must
    not try to rewrite that scope (and must not crash on it)."""
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(str(scope["type"]))

    async def send(_m: dict[str, Any]) -> None:  # pragma: no cover
        return None

    async def receive() -> dict[str, Any]:  # pragma: no cover
        return {}

    await SecurityHeadersMiddleware(app, environment="prod")(
        {"type": "websocket", "path": "/ws/plans"}, receive, send
    )
    assert seen == ["websocket"]


# ---------------------------------------------------------------------------
# `/docs` + `/openapi.json` withdrawal
# ---------------------------------------------------------------------------
def _settings(**overrides: Any) -> Any:
    from api_server.config import Settings

    base: dict[str, Any] = {
        "environment": "prod",
        "jwt_secret": "d" * 48,
        "internal_token_secret": "e" * 48,
        "review_url_signing_secret": "f" * 48,
        "sso_encryption_key": "g" * 48,
        "notification_encryption_key": "h" * 48,
        "incoming_webhook_encryption_key": "i" * 48,
        "minio_secret_key": "j" * 48,
        "minio_access_key": "prod-access",
        "database_url": "postgresql+asyncpg://app_user:realpw@db/agentic",
        "admin_database_url": "postgresql+asyncpg://migrations_user:realpw@db/agentic",
    }
    base.update(overrides)
    return Settings(**base)


def test_docs_are_published_in_dev() -> None:
    from api_server.config import Settings

    assert Settings(environment="dev").api_docs_published is True


@pytest.mark.parametrize("env", ["staging", "prod"])
def test_docs_are_withdrawn_outside_dev(env: str) -> None:
    assert _settings(environment=env).api_docs_published is False


def test_docs_can_be_forced_on_and_off_explicitly() -> None:
    """Break-glass override in both directions (a staging box used as a demo; a
    dev box that must not publish)."""
    assert _settings(environment="prod", api_docs_enabled=True).api_docs_published is True
    from api_server.config import Settings

    assert Settings(environment="dev", api_docs_enabled=False).api_docs_published is False


def test_the_app_does_not_mount_docs_or_openapi_outside_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end on the real ``create_app``: BOTH routes disappear.

    Withdrawing only the Swagger UI would be theatre — ``/openapi.json`` IS the
    reconnaissance map. Asserting the dev case in the same test keeps it from
    passing because the app failed to build at all.
    """
    import api_server.main as main_mod
    from api_server.config import Settings

    monkeypatch.setattr(main_mod, "get_settings", lambda: _settings(environment="prod"))
    prod_paths = route_paths(main_mod.create_app())
    assert "/docs" not in prod_paths
    assert "/openapi.json" not in prod_paths

    monkeypatch.setattr(main_mod, "get_settings", lambda: Settings(environment="dev"))
    dev_paths = route_paths(main_mod.create_app())
    assert "/docs" in dev_paths
    assert "/openapi.json" in dev_paths


def test_the_public_api_v1_contract_stays_published_in_prod(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The withdrawal must not take the PUBLIC contract with it.

    ``/api/v1/openapi.json`` is a separate, curated document (only public
    endpoints) that third-party integrators read before they have a token. Losing
    it would be a product regression dressed as hardening.
    """
    import api_server.main as main_mod

    monkeypatch.setattr(main_mod, "get_settings", lambda: _settings(environment="prod"))
    paths = route_paths(main_mod.create_app())
    v1_docs = {p for p in paths if p.startswith("/api/v1") and "openapi" in p}
    assert v1_docs, f"the public v1 OpenAPI document disappeared; /api/v1 paths: {paths}"
