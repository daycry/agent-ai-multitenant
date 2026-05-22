"""Integration tests for the real-time WebSocket endpoints (task_02_20 /
task_02_21).

Drives the FastAPI app through Starlette's TestClient against a real
Redis (test DB 15): events are published onto a stream, the browser-side
WebSocket tails it, and the auth gate is exercised.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from api_server.auth.deps import get_redis
from api_server.auth.jwt import encode_jwt
from api_server.events import EVENTS_STREAM, execution_stream_key, publish_execution_event
from api_server.main import app
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


def _token() -> str:
    return encode_jwt(user_id=uuid4(), session_id=uuid4())


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


async def _seed_execution(url: str, execution_id: str) -> None:
    redis: Redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.delete(execution_stream_key(execution_id))
        await publish_execution_event(
            redis, execution_id, event_type="step.started", payload={"n": 1}
        )
        await publish_execution_event(
            redis, execution_id, event_type="step.finished", payload={"n": 2}
        )
    finally:
        await redis.aclose()


async def _seed_kanban(url: str, project_a: str, project_b: str) -> None:
    redis: Redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.delete(EVENTS_STREAM)
        for project_id in (project_a, project_b, project_a):
            await redis.xadd(
                EVENTS_STREAM,
                {
                    "type": "task.status_changed",
                    "tenant_id": str(uuid4()),
                    "project_id": project_id,
                    "task_id": str(uuid4()),
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "payload": json.dumps({"old_status": "ready", "new_status": "in_progress"}),
                },
            )
    finally:
        await redis.aclose()


# ---------------------------------------------------------------------------
# /ws/executions/{id}
# ---------------------------------------------------------------------------
def test_execution_ws_streams_step_events(client: TestClient, test_redis_url: str) -> None:
    execution_id = str(uuid4())
    asyncio.run(_seed_execution(test_redis_url, execution_id))

    with client.websocket_connect(f"/ws/executions/{execution_id}?token={_token()}") as ws:
        first = ws.receive_json()
        second = ws.receive_json()

    assert first["type"] == "step.started"
    assert first["payload"] == {"n": 1}
    assert second["type"] == "step.finished"
    assert second["payload"] == {"n": 2}


# ---------------------------------------------------------------------------
# /ws/kanban/{project_id}
# ---------------------------------------------------------------------------
def test_kanban_ws_streams_only_the_requested_project(
    client: TestClient, test_redis_url: str
) -> None:
    project_a, project_b = str(uuid4()), str(uuid4())
    asyncio.run(_seed_kanban(test_redis_url, project_a, project_b))

    with client.websocket_connect(f"/ws/kanban/{project_a}?token={_token()}") as ws:
        first = ws.receive_json()
        second = ws.receive_json()

    # The project_b event in the middle was filtered out.
    assert first["project_id"] == project_a
    assert second["project_id"] == project_a
    assert first["payload"]["new_status"] == "in_progress"


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------
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
