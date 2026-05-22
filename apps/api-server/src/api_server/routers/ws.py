"""WebSocket endpoints for real-time UI (task_02_20 / task_02_21).

Two streams the browser can tail:

  /ws/executions/{execution_id}  — every step event of one agent run.
  /ws/kanban/{project_id}        — task transitions of one project.

Both tail a Redis stream and forward each entry as JSON. The browser
WebSocket API cannot set an Authorization header, so the JWT travels as
a `?token=` query parameter; an invalid or missing token closes the
socket with 1008 (policy violation).

Each socket reads its stream from the beginning (`0`), so a client that
connects mid-run still gets the backlog and then the live tail.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import structlog
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from api_server.auth.deps import get_redis
from api_server.auth.jwt import InvalidTokenError, decode_jwt
from api_server.events import EVENTS_STREAM, execution_stream_key

_log = structlog.get_logger("api_server.ws")

router = APIRouter(tags=["ws"])

# XREAD block window — long enough to be quiet while idle, short enough
# that a closing socket is noticed reasonably soon.
_BLOCK_MS = 10_000
_READ_COUNT = 64


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


async def _authenticate(ws: WebSocket, token: str | None) -> bool:
    """Validate the query-param JWT; close the socket and return False
    if it is missing or invalid."""
    if not token:
        await ws.close(code=1008, reason="missing token")
        return False
    try:
        decode_jwt(token)
    except InvalidTokenError:
        await ws.close(code=1008, reason="invalid token")
        return False
    return True


async def _pump(
    ws: WebSocket,
    redis: Redis,
    stream: str,
    *,
    project_filter: str | None,
) -> None:
    """Tail `stream` from the start and forward entries until the client
    disconnects. When `project_filter` is set, only entries for that
    project are forwarded (the kanban stream is global).

    A single `ws.receive()` runs alongside the Redis read so a client
    that closes while the stream is idle is noticed at once — no leaked
    task blocked on `xread`.
    """
    last_id = "0"
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
                    await ws.send_json(event)
    except WebSocketDisconnect:
        return
    except Exception as exc:  # a Redis blip must not leave the socket dangling
        _log.warning("api_server.ws_pump_error", stream=stream, error=str(exc))
        with contextlib.suppress(Exception):
            await ws.close(code=1011)
    finally:
        reader.cancel()


@router.websocket("/ws/executions/{execution_id}")
async def execution_stream(
    ws: WebSocket,
    execution_id: str,
    token: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
) -> None:
    """Stream one execution's step events as they happen."""
    await ws.accept()
    if not await _authenticate(ws, token):
        return
    await _pump(ws, redis, execution_stream_key(execution_id), project_filter=None)


@router.websocket("/ws/kanban/{project_id}")
async def kanban_stream(
    ws: WebSocket,
    project_id: str,
    token: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
) -> None:
    """Stream a project's task transitions as they happen."""
    await ws.accept()
    if not await _authenticate(ws, token):
        return
    await _pump(ws, redis, EVENTS_STREAM, project_filter=project_id)
