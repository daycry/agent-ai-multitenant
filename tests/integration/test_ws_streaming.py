"""Auth-gate smoke for the real-time WebSocket endpoints (task_02_20 /
task_02_21).

Streaming + cross-tenant authorization coverage moved to
`test_ws_tenant_isolation.py` (Plan 06.14 task_06_14_01), which seeds a
real tenant + resources. Here we keep only the lightweight token-gate
checks that need neither DB nor a seeded session: a missing or malformed
token closes the socket with 1008 before any resource lookup.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from api_server.auth.deps import get_redis
from api_server.main import app
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest.fixture()
def client(test_redis_url: str) -> Iterator[TestClient]:
    """A TestClient whose WebSocket endpoints read the test Redis DB."""
    app.dependency_overrides[get_redis] = lambda: Redis.from_url(
        test_redis_url, decode_responses=True
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_ws_rejects_a_missing_token(client: TestClient) -> None:
    with (
        client.websocket_connect(f"/ws/executions/{uuid4()}") as ws,
        pytest.raises(WebSocketDisconnect) as exc_info,
    ):
        ws.receive_json()
    assert exc_info.value.code == 1008


def test_ws_rejects_an_invalid_token(client: TestClient) -> None:
    with (
        client.websocket_connect(f"/ws/kanban/{uuid4()}?token=not-a-real-jwt") as ws,
        pytest.raises(WebSocketDisconnect) as exc_info,
    ):
        ws.receive_json()
    assert exc_info.value.code == 1008


class _FakeTimeRedis:
    """Solo implementa TIME — lo único que _initial_stream_id necesita."""

    def __init__(self, seconds: int, microseconds: int) -> None:
        self._now = (seconds, microseconds)

    async def time(self) -> tuple[int, int]:
        return self._now


@pytest.mark.asyncio
async def test_initial_stream_id_without_window_replays_from_zero() -> None:
    """Streams por-recurso (execution/conversation/document): el backlog ES el
    estado — se sigue leyendo desde «0»."""
    from api_server.routers.ws import _initial_stream_id

    assert await _initial_stream_id(_FakeTimeRedis(1, 0), None) == "0"  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_initial_stream_id_with_window_skips_stale_history() -> None:
    """Kanban (2026-07-03): re-reproducir el histórico completo del stream
    global resucitaba estados viejos («Hecho») por encima del fetch fresco tras
    el reset del plan CI4. Con ventana, el pump arranca en now-N según el reloj
    DE REDIS (quien genera los ids del stream)."""
    from api_server.routers.ws import _initial_stream_id

    redis = _FakeTimeRedis(1_783_065_000, 500_000)
    start = await _initial_stream_id(redis, 15_000)  # type: ignore[arg-type]
    assert start == f"{1_783_065_000 * 1000 + 500 - 15_000}-0"


def test_kanban_stream_uses_a_bounded_replay_window() -> None:
    """Pin del cableado: el socket del kanban NO re-reproduce desde «0»."""
    from api_server.routers import ws as ws_module

    assert ws_module._KANBAN_REPLAY_WINDOW_MS is not None
    assert 0 < ws_module._KANBAN_REPLAY_WINDOW_MS <= 60_000
