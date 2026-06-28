"""Unit tests — consumer transient-failure NACK path (C3 F05).

A handler that raises :class:`TransientHandlerError` (a DB blip on a plan-close
or review trigger) must be kept PENDING for a later reclaim — NOT dead-lettered
and NOT ACKed, which would drop the trigger forever. A normal handler exception
still dead-letters + ACKs (poison message can't wedge the group); the happy path
ACKs and never touches the DLQ. Driven with an in-memory fake Redis — no broker.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from orchestrator.config import Settings
from orchestrator.consumer import DEAD_LETTER_STREAM, StreamConsumer, TransientHandlerError
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Minimal async Redis stand-in for the consume loop: yields one batch of
    pre-seeded entries, then records every XACK / XADD so a test can assert what
    was acknowledged and dead-lettered."""

    def __init__(self, entries: list[tuple[str, dict[str, str]]]) -> None:
        self._entries = entries
        self.acked: list[str] = []
        self.added: dict[str, list[dict[str, str]]] = {}

    async def xreadgroup(self, *, streams: dict[str, str], **_: object) -> object:
        if not self._entries:
            return []
        batch, self._entries = self._entries, []
        stream = next(iter(streams))
        return [(stream, batch)]

    async def xack(self, _stream: str, _group: str, entry_id: str) -> None:
        self.acked.append(entry_id)

    async def xadd(self, stream: str, fields: dict[str, str], **_: object) -> None:
        self.added.setdefault(stream, []).append(fields)


def _fields(**overrides: str) -> dict[str, str]:
    base = {
        "type": EVENT_TASK_STATUS_CHANGED,
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "project_id": "22222222-2222-2222-2222-222222222222",
        "task_id": "33333333-3333-3333-3333-333333333333",
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": '{"old_status": "in_progress", "new_status": "done"}',
    }
    base.update(overrides)
    return base


def _consumer(redis: _FakeRedis, handler: object) -> StreamConsumer:
    settings = Settings(events_stream="s", consumer_group="g", consumer_name="c")
    return StreamConsumer(redis, settings, handler=handler)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_transient_handler_error_is_not_acked_or_dead_lettered() -> None:
    """A TransientHandlerError keeps the entry PENDING (no XACK) and OFF the DLQ
    so a later reclaim retries the trigger instead of losing it."""

    async def transient(_event: TaskEvent) -> None:
        raise TransientHandlerError("db blip")

    redis = _FakeRedis([("5-0", _fields())])
    consumer = _consumer(redis, transient)

    result = await consumer.consume_once()

    assert result.failed == 1
    assert consumer.stats.failed == 1
    # NOT acknowledged → stays in the PEL for reclaim.
    assert redis.acked == []
    # NOT dead-lettered — the trigger must be retried, not recorded-and-dropped.
    assert DEAD_LETTER_STREAM not in redis.added


@pytest.mark.asyncio
async def test_normal_handler_error_dead_letters_and_acks() -> None:
    """A non-transient handler error keeps the old contract: dead-letter + ACK
    (a poison message must not wedge the group)."""

    async def boom(_event: TaskEvent) -> None:
        raise RuntimeError("poison")

    redis = _FakeRedis([("6-0", _fields())])
    consumer = _consumer(redis, boom)

    result = await consumer.consume_once()

    assert result.failed == 1
    assert redis.acked == ["6-0"]
    assert len(redis.added.get(DEAD_LETTER_STREAM, [])) == 1


@pytest.mark.asyncio
async def test_successful_handler_acks_and_skips_dlq() -> None:
    """The happy path ACKs and never records to the DLQ."""
    seen: list[TaskEvent] = []

    async def ok(event: TaskEvent) -> None:
        seen.append(event)

    redis = _FakeRedis([("7-0", _fields())])
    consumer = _consumer(redis, ok)

    result = await consumer.consume_once()

    assert result.processed == 1
    assert redis.acked == ["7-0"]
    assert DEAD_LETTER_STREAM not in redis.added
    assert len(seen) == 1
