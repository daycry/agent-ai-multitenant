"""Unit tests for RequestContextMiddleware (Plan 06.14 task_06_14_10).

No I/O: the middleware is driven directly over a stub ASGI app with a
hand-built HTTP scope. Covers the bound contextvars (request_id +
user_id/tenant_id decoded from the JWT), the X-Request-ID response
header (minted vs inbound), the non-http passthrough, and the cleanup of
contextvars on both the happy and the exception path.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
import structlog
from api_server.logging.context import REQUEST_ID_HEADER, RequestContextMiddleware

pytestmark = pytest.mark.unit


def _bound() -> dict:
    """The structlog contextvars currently bound in this task."""
    return structlog.contextvars.get_contextvars()


def _make_scope(headers: list[tuple[bytes, bytes]] | None = None, kind: str = "http") -> dict:
    return {
        "type": kind,
        "method": "GET",
        "path": "/x",
        "headers": headers or [],
        "state": {},
    }


async def _drive(
    middleware: RequestContextMiddleware,
    scope: dict,
) -> tuple[list[dict], dict]:
    """Run the middleware once; return (sent messages, contextvars seen
    by the inner app while it was executing)."""
    seen: dict = {}
    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    async def inner_app(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        # Capture what the route handler would see.
        seen.update(_bound())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware.app = inner_app  # type: ignore[assignment]
    await middleware(scope, receive, send)
    return sent, seen


@pytest.fixture(autouse=True)
def _clear_ctx():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _start_headers(sent: list[dict]) -> dict[bytes, bytes]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return dict(start["headers"])


def test_mints_request_id_and_binds_it() -> None:
    mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
    sent, seen = asyncio.run(_drive(mw, _make_scope()))

    # The inner app saw a request_id bound.
    assert "request_id" in seen
    UUID(seen["request_id"])  # minted -> valid UUID
    # And it was echoed in the response header.
    echoed = _start_headers(sent)[REQUEST_ID_HEADER.encode("latin-1")].decode("latin-1")
    assert echoed == seen["request_id"]


def test_inbound_request_id_is_honoured() -> None:
    incoming = "edge-correlation-id-7"
    # ASGI servers lowercase raw header names; match that contract.
    scope = _make_scope(
        headers=[(REQUEST_ID_HEADER.lower().encode("latin-1"), incoming.encode("latin-1"))]
    )
    mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
    sent, seen = asyncio.run(_drive(mw, scope))

    assert seen["request_id"] == incoming
    echoed = _start_headers(sent)[REQUEST_ID_HEADER.encode("latin-1")].decode("latin-1")
    assert echoed == incoming


def test_binds_user_and_tenant_from_jwt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret-unit")
    from api_server.auth.jwt import encode_jwt
    from api_server.config import get_settings

    get_settings.cache_clear()
    try:
        user_id = uuid4()
        tenant_id = uuid4()
        token = encode_jwt(user_id=user_id, session_id=uuid4(), tenant_id=tenant_id)
        scope = _make_scope(headers=[(b"authorization", f"Bearer {token}".encode("latin-1"))])
        mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
        _sent, seen = asyncio.run(_drive(mw, scope))

        assert seen["user_id"] == str(user_id)
        assert seen["tenant_id"] == str(tenant_id)
        assert "request_id" in seen
    finally:
        get_settings.cache_clear()


def test_malformed_token_binds_only_request_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret-unit")
    from api_server.config import get_settings

    get_settings.cache_clear()
    try:
        scope = _make_scope(headers=[(b"authorization", b"Bearer not-a-jwt")])
        mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
        _sent, seen = asyncio.run(_drive(mw, scope))

        # No crash; user/tenant stay unbound, request_id still present.
        assert "request_id" in seen
        assert "user_id" not in seen
        assert "tenant_id" not in seen
    finally:
        get_settings.cache_clear()


def test_context_cleared_after_request() -> None:
    mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
    asyncio.run(_drive(mw, _make_scope()))
    # After the middleware returns, nothing leaks into the outer task.
    assert _bound() == {}


def test_context_cleared_even_on_exception() -> None:
    async def boom(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("kaboom")

    mw = RequestContextMiddleware(app=boom)

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:  # pragma: no cover - never called
        pass

    async def run() -> None:
        with pytest.raises(RuntimeError, match="kaboom"):
            await mw(_make_scope(), receive, send)

    asyncio.run(run())
    # contextvars must be cleared by the middleware's finally block.
    assert _bound() == {}


def test_non_http_scope_passes_through() -> None:
    received: dict = {}

    async def inner(scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        received["called"] = True

    mw = RequestContextMiddleware(app=inner)

    async def receive() -> dict:  # pragma: no cover
        return {}

    async def send(message: dict) -> None:  # pragma: no cover
        pass

    asyncio.run(mw(_make_scope(kind="websocket"), receive, send))
    assert received["called"] is True
    # No context bound for a non-http scope.
    assert _bound() == {}


def test_stashes_correlation_ids_on_scope_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exception handler reads these off request.state, so the
    middleware must populate scope['state']."""
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret-unit")
    from api_server.auth.jwt import encode_jwt
    from api_server.config import get_settings

    get_settings.cache_clear()
    try:
        user_id = uuid4()
        tenant_id = uuid4()
        token = encode_jwt(user_id=user_id, session_id=uuid4(), tenant_id=tenant_id)
        scope = _make_scope(headers=[(b"authorization", f"Bearer {token}".encode("latin-1"))])
        mw = RequestContextMiddleware(app=None)  # type: ignore[arg-type]
        asyncio.run(_drive(mw, scope))

        state = scope["state"]
        assert state["request_id"]
        assert state["log_user_id"] == user_id
        assert state["log_tenant_id"] == tenant_id
    finally:
        get_settings.cache_clear()
