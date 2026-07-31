"""WebSocket endpoints for real-time UI (task_02_20 / task_02_21).

Streams the browser can tail:

  /ws/executions/{execution_id}  — every step event of one agent run.
  /ws/kanban/{project_id}        — task transitions of one project.
  /ws/plans                      — plan status changes of the caller's tenant.
  /ws/conversation/{id}          — one conversation's message/mode events.
  /ws/documents/{id}             — one document's ingestion progress.

Each socket tails a Redis stream and forwards every entry as JSON. The
browser WebSocket API cannot set an Authorization header, so the JWT
travels as a `?token=` query parameter.

Authorization (Plan 06.14 task_06_14_01): a socket is accepted only when
the token (a) decodes, (b) maps to a *live* server-side session in Redis,
and (c) the requested resource exists **within the caller's tenant** under
PostgreSQL RLS. Any failure closes the socket with 1008 (policy violation) —
we never leak whether the resource exists in another tenant. This closes the
cross-tenant real-time leak where any valid JWT could tail any tenant's
streams by guessing a UUID.

CONTINUOUS re-validation (prod-09 task_prod09_13, authz-3). The parenthetical
above used to read "so logout/revocation closes existing sockets", and that was
FALSE: the session lookup ran ONCE, at accept. An already-open socket outlived
logout, SCIM deprovisioning and even its own token's expiry, streaming events for
as long as the tab stayed open — exactly the sockets a revocation is meant to
kill. :func:`_pump` now re-checks the session (and re-verifies the token,
expiry included) every ``ws_session_revalidate_seconds`` (30 s by default) and
closes with 1008 the moment either fails. The guarantee is now true, with a
bounded delay instead of "never".

Los streams POR-RECURSO (execution/conversation/document) se leen desde el
principio (`0`): su backlog ES el estado que el cliente necesita (p. ej. los
steps ya emitidos de un run en curso). El stream del KANBAN es distinto: el
estado inicial lo da el fetch HTTP y el socket solo debe aportar lo NUEVO —
re-reproducir el histórico del stream GLOBAL resucitaba estados viejos por
encima de datos frescos de BD (reset del plan CI4, 2026-07-03) y crecía sin
límite con la vida de la plataforma. Por eso arranca en `now - ventana`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from api_server.auth.cookies import SESSION_COOKIE_NAME
from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_session_store,
    open_tenant_session,
)
from api_server.auth.jwt import InvalidTokenError, decode_jwt
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.conversation import Conversation
from api_server.db.domain import Execution, Project
from api_server.db.knowledge import Document
from api_server.events import (
    EVENTS_STREAM,
    PLANS_STREAM,
    conversation_stream_key,
    document_stream_key,
    execution_stream_key,
)

_log = structlog.get_logger("api_server.ws")

router = APIRouter(tags=["ws"])

# XREAD block window — long enough to be quiet while idle, short enough
# that a closing socket is noticed reasonably soon.
_BLOCK_MS = 10_000
_READ_COUNT = 64

# Solapamiento de re-reproducción del socket de kanban: cubre el hueco entre el
# fetch HTTP del tablero y la conexión del WS (un evento en esa ventana no se
# pierde) sin re-reproducir el histórico completo del stream global.
_KANBAN_REPLAY_WINDOW_MS = 15_000

# Close codes (RFC 6455 1008 = policy violation).
_CLOSE_POLICY = 1008


def _decode(value: object) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


# ---------------------------------------------------------------------------
# Origin gate (ADR 0133, condición 2) — anti Cross-Site WebSocket Hijacking
# ---------------------------------------------------------------------------
def _normalise_origin(value: str) -> str:
    """Lowercase + drop a trailing slash. An env var written
    ``https://panel.example.com/`` is a typo, not a different site, and a
    mismatch there locks the real panel out — the loudest possible failure for
    the least interesting reason."""
    return value.strip().rstrip("/").lower()


def derive_self_origin(ws: Any) -> str | None:
    """The api-server's OWN public origin, as the browser addressed it.

    In production panel and API are the SAME origin behind Caddy (panel on
    ``/``, api on ``/api/*``), so the legitimate ``Origin`` of a socket IS this
    value — deriving it means a correct deployment needs no extra env var.

    The scheme comes from ``X-Forwarded-Proto`` when the request arrived through
    a proxy (the upstream hop is plain http, so trusting the socket scheme would
    derive ``http://`` for an ``https://`` page and reject the real panel), and
    from the socket scheme otherwise. Not forgeable by an attacker: a page on
    evil.com sends ``Origin: https://evil.com`` with OUR ``Host``, so the two
    disagree and the socket is rejected.
    """
    host = ws.headers.get("host")
    if not host:
        return None
    forwarded = ws.headers.get("x-forwarded-proto")
    if forwarded:
        proto = forwarded.split(",")[0].strip().lower()
    else:
        proto = "https" if ws.url.scheme == "wss" else "http"
    if proto not in ("http", "https"):
        return None
    return _normalise_origin(f"{proto}://{host}")


def origin_is_allowed(
    origin: str | None,
    *,
    allowlist: Sequence[str],
    self_origin: str | None,
    require_origin: bool = False,
) -> bool:
    """Whether a handshake claiming ``origin`` may open a socket.

    The WebSocket handshake does NOT honour CORS, so this is the only thing
    standing between a cookie-authenticated socket and any page on the internet
    (the browser attaches the session cookie to a handshake from ANY origin).
    While the credential was a ``?token=`` query param the attack was impossible
    by construction — which is exactly why the check never existed and why
    adding cookies WITHOUT it would leave the system worse than before.

    ``require_origin`` is the subtle half: an absent ``Origin`` is normal for a
    non-browser client (which carries no ambient credential), but a browser
    ALWAYS sends one, so a COOKIE-authenticated socket without it is not a
    browser doing the normal thing — reject.
    """
    if origin is None:
        return not require_origin
    candidate = _normalise_origin(origin)
    if not candidate:
        return False
    allowed = {_normalise_origin(o) for o in allowlist if o}
    if self_origin:
        allowed.add(_normalise_origin(self_origin))
    return candidate in allowed


def _to_event(entry_id: object, fields: dict[Any, Any]) -> dict[str, Any]:
    """Turn a raw stream entry into the JSON event sent to the browser."""
    event: dict[str, Any] = {_decode(k): _decode(v) for k, v in fields.items()}
    event["id"] = _decode(entry_id)
    raw_payload = event.get("payload")
    if isinstance(raw_payload, str) and raw_payload:
        with contextlib.suppress(json.JSONDecodeError):
            event["payload"] = json.loads(raw_payload)
    return event


async def _resolve_principal(
    token: str | None,
    sessions: SessionStore,
    tenant_id_override: str | None = None,
) -> AuthPrincipal | None:
    """Decode the query-param JWT and confirm its session is still live.

    Returns the principal, or None if the token is missing/invalid or the
    server-side session has been revoked (logout). Mirrors the REST
    `get_principal` checks; WebSocket can't use it directly because it
    reads the bearer from a Header dependency.

    ``tenant_id_override`` is the WebSocket mirror of the REST ``X-Tenant-Id``
    header: for a ``is_system_admin`` principal it overrides the JWT's ``tid``
    so a superadmin can tail the streams of the tenant they are ACTING AS (the
    admin-panel tenant picker). The browser WebSocket API can't set headers, so
    this travels as the ``?tenant_id=`` query param instead. Non-admins can't
    escape their JWT scope — the override is ignored for them (same rule as
    ``get_principal``). A malformed override for an admin rejects the socket
    rather than silently acting on the home tenant.
    """
    if not token:
        return None
    try:
        claims = decode_jwt(token)
    except InvalidTokenError:
        return None
    try:
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["sid"])
    except (KeyError, ValueError, TypeError):
        return None
    is_system_admin = bool(claims.get("sys", False))
    # Effective tenant: a System Admin's ?tenant_id override (the WS mirror of
    # the REST X-Tenant-Id header) wins over the JWT tid so a superadmin can
    # tail the streams of the tenant they are ACTING AS. Non-admins can't
    # override — they always fall back to their own JWT tid, so the query param
    # can never be used to escape a tenant's own scope.
    raw_tenant = (
        tenant_id_override if (is_system_admin and tenant_id_override) else claims.get("tid")
    )
    tenant_id: UUID | None = None
    if raw_tenant is not None:
        try:
            tenant_id = UUID(raw_tenant)
        except (ValueError, TypeError):
            return None
    # Revoked session → reject (immediate logout for live sockets too).
    if not await sessions.get(session_id):
        return None
    return AuthPrincipal(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


async def _authenticate_socket(
    ws: WebSocket,
    token: str | None,
    sessions: SessionStore,
    tenant_id_override: str | None = None,
) -> tuple[AuthPrincipal, str] | None:
    """Origin gate + credential resolution for an already-accepted socket.

    Returns ``(principal, credential)`` — the credential is handed back so the
    pump can keep re-validating it (task_prod09_13) whichever channel it came
    from — or ``None`` after closing the socket with 1008.

    SINGLE entry point on purpose: an endpoint that resolved the principal on
    its own would silently skip the ``Origin`` check, and a missing CSWSH gate
    is invisible until someone exploits it. Every ``/ws/*`` handler in this
    module (and in ``cortex_ws`` / ``cortex_voice``) goes through here, which
    ``tests/unit/test_ws_origin_gate_wired.py`` verifies statically.

    Credential precedence mirrors REST (:func:`auth.deps.read_credential`): an
    explicit ``?token=`` wins, the session cookie is the fallback. The cookie is
    what the panel uses since ADR 0133 — same-origin means the browser sends it
    in the handshake, so the JWT no longer travels in a URL that ends up in
    access logs, proxies and Loki.
    """
    cookie_token = ws.cookies.get(SESSION_COOKIE_NAME)
    credential = token or cookie_token
    from_cookie = not token and bool(cookie_token)

    settings = get_settings()
    if not origin_is_allowed(
        ws.headers.get("origin"),
        allowlist=settings.cors_allowed_origins,
        self_origin=derive_self_origin(ws),
        require_origin=from_cookie,
    ):
        _log.warning("api_server.ws_origin_rejected", origin=ws.headers.get("origin"))
        await _reject(ws, "origin not allowed")
        return None

    principal = await _resolve_principal(credential, sessions, tenant_id_override)
    if principal is None or credential is None:
        await _reject(ws, "unauthenticated")
        return None
    return principal, credential


async def _owns_resource(principal: AuthPrincipal, model: type[Any], resource_id: str) -> bool:
    """True if `resource_id` resolves to a row of `model` visible to the
    caller under RLS (i.e. in their tenant). A malformed UUID, a missing
    row, or a row in another tenant all return False — the database
    itself refuses to surface cross-tenant rows for the app_user role.
    """
    try:
        rid = UUID(resource_id)
    except (ValueError, TypeError):
        return False
    async with open_tenant_session(principal) as session:
        row = await session.get(model, rid)
        return row is not None


async def _initial_stream_id(redis: Redis, replay_window_ms: int | None) -> str:
    """Resolve where the pump starts reading the stream.

    ``None`` → ``"0"``: re-reproduce todo el backlog (streams por-recurso cuyo
    histórico ES el estado, p. ej. los steps de una execution). ``N`` → un id
    ``now-N`` según el RELOJ DE REDIS (los ids de stream los genera Redis; usar
    su TIME evita desfases con el del api-server): solo se re-reproduce la
    ventana reciente — el estado inicial viene del fetch HTTP, y el histórico
    antiguo puede contradecir datos más frescos de BD (2026-07-03: el tablero
    resucitaba tareas a «Hecho» tras el reset del plan CI4)."""
    if replay_window_ms is None:
        return "0"
    seconds, microseconds = await redis.time()
    start_ms = max(0, int(seconds) * 1000 + int(microseconds) // 1000 - replay_window_ms)
    return f"{start_ms}-0"


async def _credential_still_valid(
    sessions: SessionStore, principal: AuthPrincipal, token: str | None
) -> bool:
    """Re-run the accept-time authentication checks on an OPEN socket (authz-3).

    Deliberately calls the SAME primitives as :func:`_resolve_principal` — the
    Redis session lookup and :func:`decode_jwt` — rather than re-deriving "is it
    expired?" from a cached claim. A second implementation of the expiry rule
    would be a second thing to get wrong, and ``decode_jwt`` already enforces
    signature + ``exp`` in one call.

    ``token`` is ``None`` only if a future caller resolves the principal some
    other way (e.g. the one-shot ticket of task_prod09_12); the session check
    still applies, which is the leg that logout and SCIM deprovisioning trip.
    """
    if not await sessions.get(principal.session_id):
        return False
    if token is not None:
        try:
            decode_jwt(token)
        except InvalidTokenError:
            return False
    return True


async def _pump(
    ws: WebSocket,
    redis: Redis,
    stream: str,
    *,
    project_filter: str | None,
    sessions: SessionStore,
    principal: AuthPrincipal,
    token: str | None,
    tenant_filter: str | None = None,
    replay_window_ms: int | None = None,
) -> None:
    """Tail `stream` and forward entries until the client disconnects.
    `project_filter`/`tenant_filter`, when set, drop entries whose
    `project_id`/`tenant_id` field does not match — the kanban stream is
    global, so it is scoped to one project AND one tenant.
    `replay_window_ms` decides how much backlog re-plays on connect (see
    :func:`_initial_stream_id`).

    A single `ws.receive()` runs alongside the Redis read so a client
    that closes while the stream is idle is noticed at once — no leaked
    task blocked on `xread`.

    Every ``ws_session_revalidate_seconds`` the loop re-checks the caller's
    credential (:func:`_credential_still_valid`) and closes with 1008 if it has
    been revoked or has expired (task_prod09_13). The check sits at the TOP of the
    iteration, before the blocking read is scheduled, so there is no pending
    future to unwind on the revocation path — and since the read blocks for at
    most ``_BLOCK_MS``, an idle socket is still checked on schedule instead of
    only when an event happens to arrive. ``sessions``/``principal``/``token`` are
    keyword-REQUIRED so a new endpoint cannot mount a pump that never re-checks.
    """
    revalidate_every = float(get_settings().ws_session_revalidate_seconds)
    last_check = time.monotonic()
    last_id = await _initial_stream_id(redis, replay_window_ms)
    reader = asyncio.ensure_future(ws.receive())
    try:
        while True:
            if revalidate_every > 0 and (time.monotonic() - last_check) >= revalidate_every:
                last_check = time.monotonic()
                if not await _credential_still_valid(sessions, principal, token):
                    _log.info(
                        "api_server.ws_credential_revoked",
                        stream=stream,
                        user_id=str(principal.user_id),
                        session_id=str(principal.session_id),
                    )
                    await _reject(ws, "session revoked or expired")
                    return
            xread = asyncio.ensure_future(
                redis.xread({stream: last_id}, count=_READ_COUNT, block=_BLOCK_MS)
            )
            done, _pending = await asyncio.wait(
                {reader, xread}, return_when=asyncio.FIRST_COMPLETED
            )
            if reader in done:
                xread.cancel()
                return  # client disconnected
            entries: Any = xread.result()
            for _stream_name, items in entries or []:
                for entry_id, fields in items:
                    last_id = _decode(entry_id)
                    event = _to_event(entry_id, fields)
                    if project_filter is not None and event.get("project_id") != project_filter:
                        continue
                    if tenant_filter is not None and event.get("tenant_id") != tenant_filter:
                        continue
                    await ws.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # a Redis blip must not leave the socket dangling
        _log.warning("api_server.ws_pump_error", stream=stream, error=str(exc))
        with contextlib.suppress(Exception):
            await ws.close(code=1011)
    finally:
        reader.cancel()


async def _reject(ws: WebSocket, reason: str) -> None:
    with contextlib.suppress(Exception):
        await ws.close(code=_CLOSE_POLICY, reason=reason)


@router.websocket("/ws/executions/{execution_id}")
async def execution_stream(
    ws: WebSocket,
    execution_id: str,
    token: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream one execution's step events — only to a member of its tenant."""
    await ws.accept()
    authenticated = await _authenticate_socket(ws, token, sessions, tenant_id)
    if authenticated is None:
        return
    principal, token = authenticated
    if not await _owns_resource(principal, Execution, execution_id):
        await _reject(ws, "forbidden")
        return
    await _pump(
        ws,
        redis,
        execution_stream_key(execution_id),
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=token,
    )


@router.websocket("/ws/kanban/{project_id}")
async def kanban_stream(
    ws: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream a project's task transitions — only to a member of its tenant.

    The kanban stream is the single global EVENTS_STREAM, so it is scoped
    both by `project_id` and by the caller's `tenant_id` (defence in depth;
    project ids are globally-unique UUIDs already).
    """
    await ws.accept()
    authenticated = await _authenticate_socket(ws, token, sessions, tenant_id)
    if authenticated is None:
        return
    principal, token = authenticated
    if not await _owns_resource(principal, Project, project_id):
        await _reject(ws, "forbidden")
        return
    tenant_filter = str(principal.tenant_id) if principal.tenant_id is not None else None
    await _pump(
        ws,
        redis,
        EVENTS_STREAM,
        project_filter=project_id,
        sessions=sessions,
        principal=principal,
        token=token,
        tenant_filter=tenant_filter,
        # El estado inicial del tablero es el fetch HTTP; el socket solo aporta
        # lo nuevo (+ una ventana corta de solape). Ver _initial_stream_id.
        replay_window_ms=_KANBAN_REPLAY_WINDOW_MS,
    )


@router.websocket("/ws/plans")
async def plans_stream(
    ws: WebSocket,
    token: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Cambios de estado de los planes del tenant (`task_wf_32`).

    De TENANT y no de proyecto: el tablero gerencial lista los planes de todo
    el tenant, así que un socket por proyecto dejaría rancias las tarjetas de
    los demás.

    Eso lo hace el primer socket **sin recurso**: los otros cuatro autorizan
    comprobando que el id pedido existe dentro del tenant del llamante
    (`_owns_resource`), y aquí no hay id que comprobar. La autorización es que
    el principal TENGA tenant — y el filtro por `tenant_id` del pump es lo que
    impide leer los planes de otro. Un superadmin sin tenant elegido no puede
    abrirlo: sin tenant no hay filtro, y sin filtro el socket sería un
    escaparate de toda la plataforma.
    """
    await ws.accept()
    authenticated = await _authenticate_socket(ws, token, sessions, tenant_id)
    if authenticated is None:
        return
    principal, token = authenticated
    if principal.tenant_id is None:
        await _reject(ws, "forbidden")
        return
    await _pump(
        ws,
        redis,
        PLANS_STREAM,
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=token,
        tenant_filter=str(principal.tenant_id),
        # Mismo criterio que el kanban: el estado inicial es el fetch HTTP y el
        # socket solo aporta lo nuevo. Re-reproducir el histórico resucitaría
        # estados viejos por encima de datos frescos de BD.
        replay_window_ms=_KANBAN_REPLAY_WINDOW_MS,
    )


@router.websocket("/ws/conversation/{conversation_id}")
async def conversation_stream(
    ws: WebSocket,
    conversation_id: str,
    token: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream one conversation's message + mode-change events live —
    only to a member of its tenant. The REST endpoint
    POST /conversations/{id}/messages is the sole producer."""
    await ws.accept()
    authenticated = await _authenticate_socket(ws, token, sessions, tenant_id)
    if authenticated is None:
        return
    principal, token = authenticated
    if not await _owns_resource(principal, Conversation, conversation_id):
        await _reject(ws, "forbidden")
        return
    await _pump(
        ws,
        redis,
        conversation_stream_key(conversation_id),
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=token,
    )


@router.websocket("/ws/documents/{document_id}")
async def document_stream(
    ws: WebSocket,
    document_id: str,
    token: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream KB document ingestion progress (Plan 04 task_04_15) —
    only to a member of its tenant. The producer is the Celery ingestion
    task, which publishes ``document.status`` / ``document.progress``
    events to the per-document Redis stream as it walks scan → parse →
    embed → persist."""
    await ws.accept()
    authenticated = await _authenticate_socket(ws, token, sessions, tenant_id)
    if authenticated is None:
        return
    principal, token = authenticated
    if not await _owns_resource(principal, Document, document_id):
        await _reject(ws, "forbidden")
        return
    await _pump(
        ws,
        redis,
        document_stream_key(document_id),
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=token,
    )
