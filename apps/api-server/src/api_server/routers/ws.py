"""WebSocket endpoints for real-time UI (task_02_20 / task_02_21).

Streams the browser can tail:

  /ws/executions/{execution_id}  — every step event of one agent run.
  /ws/kanban/{project_id}        — task transitions of one project.
  /ws/conversation/{id}          — one conversation's message/mode events.
  /ws/documents/{id}             — one document's ingestion progress.

Each socket tails a Redis stream and forwards every entry as JSON. The
browser WebSocket API cannot set an Authorization header, so the JWT
travels as a `?token=` query parameter.

Authorization (Plan 06.14 task_06_14_01): a socket is accepted only when
the token (a) decodes, (b) maps to a *live* server-side session in Redis
(so logout/revocation closes existing sockets), and (c) the requested
resource exists **within the caller's tenant** under PostgreSQL RLS. Any
failure closes the socket with 1008 (policy violation) — we never leak
whether the resource exists in another tenant. This closes the
cross-tenant real-time leak where any valid JWT could tail any tenant's
streams by guessing a UUID.

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
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_session_store,
    open_tenant_session,
)
from api_server.auth.jwt import InvalidTokenError, decode_jwt
from api_server.auth.sessions import SessionStore
from api_server.db.conversation import Conversation
from api_server.db.domain import Execution, Project
from api_server.db.knowledge import Document
from api_server.events import (
    EVENTS_STREAM,
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


def _to_event(entry_id: object, fields: dict[Any, Any]) -> dict[str, Any]:
    """Turn a raw stream entry into the JSON event sent to the browser."""
    event: dict[str, Any] = {_decode(k): _decode(v) for k, v in fields.items()}
    event["id"] = _decode(entry_id)
    raw_payload = event.get("payload")
    if isinstance(raw_payload, str) and raw_payload:
        with contextlib.suppress(json.JSONDecodeError):
            event["payload"] = json.loads(raw_payload)
    return event


async def _resolve_principal(token: str | None, sessions: SessionStore) -> AuthPrincipal | None:
    """Decode the query-param JWT and confirm its session is still live.

    Returns the principal, or None if the token is missing/invalid or the
    server-side session has been revoked (logout). Mirrors the REST
    `get_principal` checks; WebSocket can't use it directly because it
    reads the bearer from a Header dependency.
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
    tenant_id: UUID | None = None
    if claims.get("tid") is not None:
        try:
            tenant_id = UUID(claims["tid"])
        except (ValueError, TypeError):
            return None
    # Revoked session → reject (immediate logout for live sockets too).
    if not await sessions.get(session_id):
        return None
    return AuthPrincipal(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=bool(claims.get("sys", False)),
    )


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


async def _pump(
    ws: WebSocket,
    redis: Redis,
    stream: str,
    *,
    project_filter: str | None,
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
    """
    last_id = await _initial_stream_id(redis, replay_window_ms)
    reader = asyncio.ensure_future(ws.receive())
    try:
        while True:
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
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream one execution's step events — only to a member of its tenant."""
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    if not await _owns_resource(principal, Execution, execution_id):
        await _reject(ws, "forbidden")
        return
    await _pump(ws, redis, execution_stream_key(execution_id), project_filter=None)


@router.websocket("/ws/kanban/{project_id}")
async def kanban_stream(
    ws: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream a project's task transitions — only to a member of its tenant.

    The kanban stream is the single global EVENTS_STREAM, so it is scoped
    both by `project_id` and by the caller's `tenant_id` (defence in depth;
    project ids are globally-unique UUIDs already).
    """
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    if not await _owns_resource(principal, Project, project_id):
        await _reject(ws, "forbidden")
        return
    tenant_filter = str(principal.tenant_id) if principal.tenant_id is not None else None
    await _pump(
        ws,
        redis,
        EVENTS_STREAM,
        project_filter=project_id,
        tenant_filter=tenant_filter,
        # El estado inicial del tablero es el fetch HTTP; el socket solo aporta
        # lo nuevo (+ una ventana corta de solape). Ver _initial_stream_id.
        replay_window_ms=_KANBAN_REPLAY_WINDOW_MS,
    )


@router.websocket("/ws/conversation/{conversation_id}")
async def conversation_stream(
    ws: WebSocket,
    conversation_id: str,
    token: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream one conversation's message + mode-change events live —
    only to a member of its tenant. The REST endpoint
    POST /conversations/{id}/messages is the sole producer."""
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    if not await _owns_resource(principal, Conversation, conversation_id):
        await _reject(ws, "forbidden")
        return
    await _pump(ws, redis, conversation_stream_key(conversation_id), project_filter=None)


@router.websocket("/ws/documents/{document_id}")
async def document_stream(
    ws: WebSocket,
    document_id: str,
    token: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream KB document ingestion progress (Plan 04 task_04_15) —
    only to a member of its tenant. The producer is the Celery ingestion
    task, which publishes ``document.status`` / ``document.progress``
    events to the per-document Redis stream as it walks scan → parse →
    embed → persist."""
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    if not await _owns_resource(principal, Document, document_id):
        await _reject(ws, "forbidden")
        return
    await _pump(ws, redis, document_stream_key(document_id), project_filter=None)
