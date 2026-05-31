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
