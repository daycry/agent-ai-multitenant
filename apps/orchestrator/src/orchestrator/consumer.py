"""Redis Streams consumer for the orchestrator.

Reads domain events off `events:tasks` through a consumer group so
multiple orchestrator replicas split the load and every event is
delivered at least once (XREADGROUP + XACK).

task_02_01 scope: consume + parse + ACK + count. The actual
assignment of a task to a worker is a no-op handler here; task_02_03
swaps in the real assignment policies via `set_handler`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from orchestrator.config import Settings
from orchestrator.events import MalformedEventError, TaskEvent, parse_event

_log = structlog.get_logger("orchestrator.consumer")

# A handler reacts to one parsed event. Returns nothing; raising propagates to
# the consumer, which logs + counts a failure, dead-letters the event, then
# still ACKs so a poison message can't wedge the group (Plan 06.14
# task_06_14_05 / workers-orchestrator-4).
EventHandler = Callable[[TaskEvent], Awaitable[None]]

# Events whose handler raised are XADDed here before the caller ACKs, so a
# failed dispatch is observable and replayable instead of silently lost.
# Mirrors the workers' `dlq:executions` stream (task_06_14_04).
DEAD_LETTER_STREAM = "dlq:orchestrator_events"
# Cap the dead-letter stream so an outage that fails thousands of events can't
# grow Redis unbounded; approximate trimming keeps XADD O(1).
_DEAD_LETTER_MAXLEN = 10_000

# prod-06 task_prod06_evento_01: an entry idle longer than this in the Pending
# Entries List is presumed orphaned — its consumer crashed after delivery but
# before XACK, and XREADGROUP('>') never re-delivers it. We reclaim it.
_RECLAIM_MIN_IDLE_MS = 60_000
# Safety cap on XAUTOCLAIM cursor pages per reclaim call (each page is
# `read_count` entries) so a huge PEL can't spin the loop unbounded.
_RECLAIM_MAX_PAGES = 100


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

    async def reclaim_stale_pending(
        self, *, min_idle_ms: int = _RECLAIM_MIN_IDLE_MS
    ) -> ConsumeResult:
        """Reclaim + reprocess PEL entries orphaned by a crashed consumer.

        prod-06 task_prod06_evento_01 (workers-4). ``XREADGROUP('>')`` only yields
        NEW entries, so an entry delivered to a consumer that died before ``XACK``
        sits in the Pending Entries List forever. This ``XAUTOCLAIM``s entries idle
        longer than ``min_idle_ms`` onto THIS consumer and runs them through the
        normal dispatch+ack path (dead-lettering one that still fails). Idempotent;
        bounded per call by ``read_count`` and drained across the cursor. Called at
        startup (orphans from the previous process) and safe to call periodically.
        """
        s = self._settings
        result = ConsumeResult()
        cursor = "0-0"
        for _ in range(_RECLAIM_MAX_PAGES):
            next_cursor, entries, _deleted = await self._redis.xautoclaim(
                name=s.events_stream,
                groupname=s.consumer_group,
                consumername=s.consumer_name,
                min_idle_time=min_idle_ms,
                start_id=cursor,
                count=s.read_count,
            )
            for entry_id, fields in entries:
                if not fields:
                    # Entry was trimmed/deleted from the stream — drop it from the PEL.
                    await self._redis.xack(s.events_stream, s.consumer_group, entry_id)
                    continue
                result.ids.append(entry_id)
                await self._dispatch(entry_id, fields, result)
                await self._redis.xack(s.events_stream, s.consumer_group, entry_id)
            if not entries or next_cursor in ("0-0", b"0-0"):
                break
            cursor = next_cursor
        if result.ids:
            _log.info(
                "orchestrator.pel_reclaimed",
                count=len(result.ids),
                processed=result.processed,
                failed=result.failed,
            )
        return result

    async def _dispatch(self, entry_id: str, fields: dict[str, str], result: ConsumeResult) -> None:
        """Parse + hand one entry to the handler, updating counters.

        Malformed entries are counted and skipped (still ACKed by the
        caller so a poison message can't wedge the group). A handler that
        raises is counted as `failed` and pushed to the dead-letter stream
        BEFORE the caller ACKs, so the failed dispatch is observable and
        replayable rather than silently lost (workers-orchestrator-4).
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
            await self._dead_letter(event, exc)
            return

        self.stats.processed += 1
        result.processed += 1

    async def _dead_letter(self, event: TaskEvent, exc: Exception) -> None:
        """Best-effort: record a failed event on the dead-letter stream.

        Runs before the caller ACKs so the event survives. A dead-letter
        outage only logs a warning — it must never re-raise into the consume
        loop (the event is counted `failed` and ACKed regardless)."""
        try:
            await self._redis.xadd(
                DEAD_LETTER_STREAM,
                {
                    "entry_id": event.stream_id,
                    "type": event.type,
                    "task_id": event.task_id,
                    "tenant_id": event.tenant_id,
                    "error": f"{type(exc).__name__}: {exc}",
                    "failed_at_unix": str(time.time()),
                },
                maxlen=_DEAD_LETTER_MAXLEN,
                approximate=True,
            )
        except Exception as dlq_exc:  # pragma: no cover - DLQ is best-effort
            _log.warning(
                "orchestrator.dead_letter_record_failed",
                entry=event.stream_id,
                task_id=event.task_id,
                error=str(dlq_exc),
            )
