"""Integration tests for the orchestrator service (task_02_01).

Exercises the Redis Streams consumer end-to-end against a real Redis
(test DB 15, same as the rest of the integration suite):

  - a well-formed event is parsed, dispatched and ACKed;
  - a malformed entry is counted + ACKed (no poison-message wedge);
  - a custom handler receives the parsed TaskEvent;
  - the api-server publisher's wire format round-trips into the
    consumer;
  - the FastAPI app exposes /healthz and /orchestrator/stats.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from orchestrator.app import create_app
from orchestrator.config import Settings
from orchestrator.consumer import StreamConsumer
from orchestrator.events import EVENT_TASK_CREATED, TaskEvent
from redis.asyncio import Redis

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"


def _settings(stream: str) -> Settings:
    return Settings(
        redis_url=TEST_REDIS_URL,
        events_stream=stream,
        consumer_group="orchestrator",
        consumer_name="test-consumer",
        block_ms=100,
    )


def _event_fields(*, type_: str = EVENT_TASK_CREATED, **overrides: str) -> dict[str, str]:
    """A well-formed stream entry; override individual fields per test."""
    fields = {
        "type": type_,
        "tenant_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": json.dumps({"status": "backlog", "priority": "medium"}),
    }
    fields.update(overrides)
    return fields


@pytest_asyncio.fixture()
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture()
async def stream(redis_client: Redis) -> AsyncIterator[str]:
    """A unique stream name per test, dropped on teardown."""
    name = f"test:events:{uuid.uuid4().hex[:12]}"
    yield name
    await redis_client.delete(name)


@pytest.mark.asyncio
async def test_consume_well_formed_event_is_processed_and_acked(
    redis_client: Redis, stream: str
) -> None:
    settings = _settings(stream)
    consumer = StreamConsumer(redis_client, settings)
    await consumer.ensure_group()

    await redis_client.xadd(stream, _event_fields())

    result = await consumer.consume_once()

    assert result.processed == 1
    assert result.malformed == 0
    assert result.failed == 0
    assert consumer.stats.processed == 1

    # The entry was ACKed — nothing left pending for the group.
    pending = await redis_client.xpending(stream, settings.consumer_group)
    assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_malformed_event_is_counted_and_acked(redis_client: Redis, stream: str) -> None:
    settings = _settings(stream)
    consumer = StreamConsumer(redis_client, settings)
    await consumer.ensure_group()

    # Missing tenant_id / project_id / task_id.
    await redis_client.xadd(stream, {"type": EVENT_TASK_CREATED, "payload": "{}"})

    result = await consumer.consume_once()

    assert result.processed == 0
    assert result.malformed == 1
    assert consumer.stats.malformed == 1

    # A poison message must not wedge the group — it's ACKed too.
    pending = await redis_client.xpending(stream, settings.consumer_group)
    assert pending["pending"] == 0


@pytest.mark.asyncio
async def test_empty_stream_yields_zero_result(redis_client: Redis, stream: str) -> None:
    consumer = StreamConsumer(redis_client, _settings(stream))
    await consumer.ensure_group()

    result = await consumer.consume_once()

    assert (result.processed, result.malformed, result.failed) == (0, 0, 0)


@pytest.mark.asyncio
async def test_custom_handler_receives_parsed_event(redis_client: Redis, stream: str) -> None:
    seen: list[TaskEvent] = []

    async def handler(event: TaskEvent) -> None:
        seen.append(event)

    consumer = StreamConsumer(redis_client, _settings(stream), handler=handler)
    await consumer.ensure_group()

    fields = _event_fields()
    await redis_client.xadd(stream, fields)
    await consumer.consume_once()

    assert len(seen) == 1
    assert seen[0].type == fields["type"]
    assert seen[0].task_id == fields["task_id"]
    assert seen[0].tenant_id == fields["tenant_id"]
    assert seen[0].payload == {"status": "backlog", "priority": "medium"}


@pytest.mark.asyncio
async def test_handler_failure_is_counted_but_loop_survives(
    redis_client: Redis, stream: str
) -> None:
    async def boom(_event: TaskEvent) -> None:
        raise RuntimeError("handler exploded")

    consumer = StreamConsumer(redis_client, _settings(stream), handler=boom)
    await consumer.ensure_group()

    await redis_client.xadd(stream, _event_fields())
    result = await consumer.consume_once()

    assert result.failed == 1
    assert result.processed == 0
    assert consumer.stats.failed == 1


@pytest.mark.asyncio
async def test_api_server_publisher_roundtrips_into_consumer(
    redis_client: Redis, stream: str
) -> None:
    """The producer-side helper in api_server.events must emit fields
    the orchestrator's parser accepts."""
    from api_server.db.domain import Task
    from api_server.events import publish_task_created

    seen: list[TaskEvent] = []

    async def handler(event: TaskEvent) -> None:
        seen.append(event)

    consumer = StreamConsumer(redis_client, _settings(stream), handler=handler)
    await consumer.ensure_group()

    # An in-memory Task — no DB row needed, the publisher only reads
    # attributes off the object.
    task = Task(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        title="round-trip",
        status="backlog",
        priority="high",
    )
    # publish_task_created hard-codes the "events:tasks" stream, so
    # monkeypatch the module constant onto the test stream.
    import api_server.events as events_mod

    original = events_mod.EVENTS_STREAM
    events_mod.EVENTS_STREAM = stream
    try:
        await publish_task_created(redis_client, task)
    finally:
        events_mod.EVENTS_STREAM = original

    await consumer.consume_once()

    assert len(seen) == 1
    assert seen[0].task_id == str(task.id)
    assert seen[0].type == EVENT_TASK_CREATED
    assert seen[0].payload == {"status": "backlog", "priority": "high"}


@pytest.mark.asyncio
async def test_app_exposes_healthz_and_stats(stream: str) -> None:
    app = create_app(_settings(stream))
    # httpx's ASGITransport does not fire lifespan events, so drive the
    # app's lifespan context explicitly — that starts the consume loop
    # and lets /orchestrator/stats report loop_running=true.
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        health = await client.get("/healthz")
        stats = await client.get("/orchestrator/stats")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    assert stats.status_code == 200
    body = stats.json()
    assert body["stream"] == stream
    assert body["consumer_group"] == "orchestrator"
    assert body["loop_running"] is True
    assert body["events"] == {"processed": 0, "malformed": 0, "failed": 0}
