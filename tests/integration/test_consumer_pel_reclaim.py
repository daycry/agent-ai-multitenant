"""Integration test — prod-06 task_prod06_evento_01 (PEL reclaim).

An event delivered to a consumer that then crashed before ``XACK`` sits in the
group's Pending Entries List forever — ``XREADGROUP('>')`` only yields NEW
entries. ``StreamConsumer.reclaim_stale_pending`` must ``XAUTOCLAIM`` it onto a
live consumer and run it through the normal dispatch+ack path. Driven against a
real Redis (Streams semantics are server-side).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from orchestrator.config import Settings
from orchestrator.consumer import StreamConsumer
from orchestrator.events import EVENT_TASK_CREATED
from redis.asyncio import Redis

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration


def _settings(stream: str) -> Settings:
    return Settings(
        redis_url=TEST_REDIS_URL,
        events_stream=stream,
        consumer_group="orchestrator",
        consumer_name="live-consumer",
        block_ms=100,
    )


def _event_fields() -> dict[str, str]:
    return {
        "type": EVENT_TASK_CREATED,
        "tenant_id": str(uuid.uuid4()),
        "project_id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": json.dumps({"status": "backlog", "priority": "medium"}),
    }


@pytest_asyncio.fixture()
async def redis_client() -> AsyncIterator[Redis]:
    client: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    yield client
    await client.aclose()


@pytest_asyncio.fixture()
async def stream(redis_client: Redis) -> AsyncIterator[str]:
    name = f"events:tasks:pel-reclaim:{uuid.uuid4().hex}"
    yield name
    await redis_client.delete(name)


@pytest.mark.asyncio
async def test_reclaim_processes_orphaned_pending_entry(redis_client: Redis, stream: str) -> None:
    settings = _settings(stream)
    await redis_client.xgroup_create(stream, settings.consumer_group, id="0", mkstream=True)
    await redis_client.xadd(stream, _event_fields())

    # A now-dead consumer takes delivery but never ACKs → the entry lands in the PEL.
    await redis_client.xreadgroup(
        groupname=settings.consumer_group,
        consumername="dead-consumer",
        streams={stream: ">"},
        count=10,
    )
    pending_before = await redis_client.xpending(stream, settings.consumer_group)
    assert pending_before["pending"] == 1

    # A live consumer reclaims it (min_idle_ms=0 → eligible immediately) + processes it.
    consumer = StreamConsumer(redis_client, settings)
    result = await consumer.reclaim_stale_pending(min_idle_ms=0)

    assert result.processed == 1
    assert consumer.stats.processed == 1
    pending_after = await redis_client.xpending(stream, settings.consumer_group)
    assert pending_after["pending"] == 0
