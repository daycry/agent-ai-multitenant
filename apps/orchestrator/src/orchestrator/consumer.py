"""Redis Streams consumer for the orchestrator.

Reads domain events off `events:tasks` through a consumer group so
multiple orchestrator replicas split the load and every event is
delivered at least once (XREADGROUP + XACK).

task_02_01 scope: consume + parse + ACK + count. The actual
assignment of a task to a worker is a no-op handler here; task_02_03
swaps in the real assignment policies via `set_handler`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from orchestrator.config import Settings
from orchestrator.events import MalformedEventError, TaskEvent, parse_event

_log = structlog.get_logger("orchestrator.consumer")

# A handler reacts to one parsed event. Returns nothing; raising
# propagates to the consumer, which logs + counts a failure but still
# ACKs (task_02_01 keeps it simple — no dead-letter yet).
EventHandler = Callable[[TaskEvent], Awaitable[None]]


async def _noop_handler(event: TaskEvent) -> None:
    """Default handler until task_02_03 wires assignment policies."""
    _log.info(
        "orchestrator.event_observed",
        type=event.type,
        task_id=event.task_id,
        tenant_id=event.tenant_id,
    )


@dataclass
class ConsumerStats:
    """Running counters surfaced by GET /orchestrator/stats."""

    processed: int = 0
    malformed: int = 0
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "malformed": self.malformed,
            "failed": self.failed,
        }


@dataclass
class ConsumeResult:
    """Outcome of a single `consume_once()` batch."""

    processed: int = 0
    malformed: int = 0
    failed: int = 0
    ids: list[str] = field(default_factory=list)


class StreamConsumer:
    """Owns the consumer-group lifecycle and the read loop."""

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        handler: EventHandler | None = None,
    ) -> None:
        self._redis = redis
        self._settings = settings
        self._handler: EventHandler = handler or _noop_handler
        self.stats = ConsumerStats()

    def set_handler(self, handler: EventHandler) -> None:
        """Swap the event handler (task_02_03 injects assignment)."""
        self._handler = handler

    async def ensure_group(self) -> None:
        """Create the consumer group if it doesn't exist yet.

        `MKSTREAM` creates the stream too, so the orchestrator can boot
        before the api-server has published anything. Re-running is
        safe: a pre-existing group raises BUSYGROUP, which we swallow.
        """
        try:
            await self._redis.xgroup_create(
                name=self._settings.events_stream,
                groupname=self._settings.consumer_group,
                id="0",
                mkstream=True,
            )
            _log.info(
                "orchestrator.group_created",
                stream=self._settings.events_stream,
                group=self._settings.consumer_group,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
            _log.debug("orchestrator.group_exists", group=self._settings.consumer_group)

    async def consume_once(self) -> ConsumeResult:
        """Pull one batch of new events, dispatch each, ACK them all.

        Returns the per-batch counts. A batch with no new events
        returns an all-zero result (XREADGROUP unblocked on timeout).
        """
        s = self._settings
        response = await self._redis.xreadgroup(
            groupname=s.consumer_group,
            consumername=s.consumer_name,
            streams={s.events_stream: ">"},
            count=s.read_count,
            block=s.block_ms,
        )
        result = ConsumeResult()
        if not response:
            return result

        # response: [(stream_name, [(entry_id, {field: value}), ...])]
        for _stream, entries in response:
            for entry_id, fields in entries:
                result.ids.append(entry_id)
                await self._dispatch(entry_id, fields, result)
                await self._redis.xack(s.events_stream, s.consumer_group, entry_id)

        return result

    async def _dispatch(self, entry_id: str, fields: dict[str, str], result: ConsumeResult) -> None:
        """Parse + hand one entry to the handler, updating counters.

        Malformed entries are counted and skipped (still ACKed by the
        caller so a poison message can't wedge the group). A handler
        that raises is counted as `failed` but also ACKed — Plan 02
        keeps the path simple; a dead-letter stream is a later refinement.
        """
        try:
            event = parse_event(entry_id, fields)
        except MalformedEventError as exc:
            self.stats.malformed += 1
            result.malformed += 1
            _log.warning("orchestrator.event_malformed", entry=entry_id, error=str(exc))
            return

        try:
            await self._handler(event)
        except Exception as exc:  # handler errors must not kill the loop
            self.stats.failed += 1
            result.failed += 1
            _log.error(
                "orchestrator.handler_failed",
                entry=entry_id,
                type=event.type,
                task_id=event.task_id,
                error=str(exc),
            )
            return

        self.stats.processed += 1
        result.processed += 1
