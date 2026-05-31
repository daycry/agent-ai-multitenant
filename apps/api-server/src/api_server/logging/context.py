"""Context-local request metadata bound to every log line via structlog.

`bind_request_context(...)` is called by `RequestContextMiddleware`
(registered in `create_app`) at the start of each request. The bound
values are stored in contextvars so async tasks spawned from the
request inherit them, and every downstream log line carries
`request_id` (+ `user_id` / `tenant_id` when resolvable from the JWT).

The middleware also propagates the request id: it honours an inbound
`X-Request-ID` header (so a reverse proxy / upstream service can stitch
a trace together) or mints a fresh UUID, and echoes the value back in
the response `X-Request-ID` header.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api_server.auth.jwt import InvalidTokenError, decode_jwt

# Header used to carry a stable correlation id across services. Named
# constant rather than a scattered literal so a future rename (or a
# second alias) lives in one place.
REQUEST_ID_HEADER = "X-Request-ID"


def bind_request_context(
    *,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    project_id: UUID | None = None,
    request_id: str | None = None,
) -> None:
    """Attach per-request identifiers to every subsequent log line."""
    fields: dict[str, str | None] = {}
    if user_id is not None:
        fields["user_id"] = str(user_id)
    if tenant_id is not None:
        fields["tenant_id"] = str(tenant_id)
    if project_id is not None:
        fields["project_id"] = str(project_id)
    if request_id is not None:
        fields["request_id"] = request_id
    if fields:
        structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    structlog.contextvars.clear_contextvars()


def _resolve_principal_ids(request: Request) -> tuple[UUID | None, UUID | None]:
    """Best-effort extraction of (user_id, tenant_id) from the bearer JWT.

    Pure claim decoding — NO Redis session lookup and NO DB query, so it
    stays cheap and never raises into the request path. The authoritative
    auth check still happens in `get_principal`; this is only to enrich
    log lines. A malformed / unsigned / absent token simply yields
    `(None, None)` and the request id alone is bound.
    """
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None, None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, None
    try:
        claims = decode_jwt(token)
    except InvalidTokenError:
        return None, None
    user_id: UUID | None = None
    tenant_id: UUID | None = None
    sub = claims.get("sub")
    if isinstance(sub, str):
        try:
            user_id = UUID(sub)
        except ValueError:
            user_id = None
    tid = claims.get("tid")
    if isinstance(tid, str):
        try:
            tenant_id = UUID(tid)
        except ValueError:
            tenant_id = None
    return user_id, tenant_id


class RequestContextMiddleware:
    """ASGI middleware that binds per-request log context.

    Generates / propagates a request id, binds it (plus the JWT's
    user_id / tenant_id when present) via `bind_request_context`, echoes
    the id back in the `X-Request-ID` response header, and clears the
    context on exit so contextvars never leak across requests.

    Implemented as a raw ASGI middleware (not
    `BaseHTTPMiddleware`) so the bound contextvars are visible to the
    route handler within the same task — `BaseHTTPMiddleware` runs the
    downstream app in a separate task, which would not inherit the
    contextvars bound here.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid4())
        user_id, tenant_id = _resolve_principal_ids(request)

        bind_request_context(request_id=request_id, user_id=user_id, tenant_id=tenant_id)
        # Also stash the ids on the request state. When a route raises an
        # unhandled exception it propagates OUT through this middleware
        # (clearing the contextvars in `finally`) BEFORE Starlette's
        # outer ServerErrorMiddleware invokes the global exception
        # handler — so the handler can no longer see the contextvars.
        # The handler re-reads these from `request.state` to log under
        # the same correlation id.
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        state["log_user_id"] = user_id
        state["log_tenant_id"] = tenant_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            clear_request_context()


__all__ = [
    "REQUEST_ID_HEADER",
    "RequestContextMiddleware",
    "bind_request_context",
    "clear_request_context",
]
