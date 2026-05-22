"""Domain event publisher — producer side of the `events:tasks` bus.

The orchestrator (`apps/orchestrator`) consumes this stream to drive
task assignment. The contract (stream name, fields, event types) is
documented in ADR 0011; the consumer-side mirror lives in
`orchestrator.events`.

Publishing is best-effort: a Redis blip must never fail the DB write
that triggered the event. Callers wrap nothing — `publish_task_event`
swallows and logs its own errors.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import structlog
from redis.asyncio import Redis

from api_server.db.domain import Task

_log = structlog.get_logger("api_server.events")

# Stream every task domain event lands on. Single global stream;
# consumers fan out by `tenant_id` if they need to.
EVENTS_STREAM = "events:tasks"

EVENT_TASK_CREATED = "task.created"
EVENT_TASK_STATUS_CHANGED = "task.status_changed"

# Cap the stream so a long-lived dev Redis doesn't grow unbounded.
# Approximate trimming (`~`) lets Redis trim in efficient batches.
_MAXLEN = 10_000


async def _publish(redis: Redis, fields: dict[str, str]) -> None:
    try:
        # redis-py types xadd's `fields` with a wide key/value union;
        # `dict` is invariant so a plain dict[str, str] won't match the
        # annotation even though it's a valid argument at runtime.
        await redis.xadd(
            EVENTS_STREAM,
            fields,  # type: ignore[arg-type]
            maxlen=_MAXLEN,
            approximate=True,
        )
    except Exception as exc:  # event bus is best-effort, never fail the caller
        _log.warning("api_server.event_publish_failed", error=str(exc))


async def publish_task_created(redis: Redis, task: Task) -> None:
    """Emit `task.created` after a task row is inserted."""
    await _publish(
        redis,
        {
            "type": EVENT_TASK_CREATED,
            "tenant_id": str(task.tenant_id),
            "project_id": str(task.project_id),
            "task_id": str(task.id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps({"status": task.status, "priority": task.priority}),
        },
    )


async def publish_task_status_changed(
    redis: Redis, task: Task, *, old_status: str, new_status: str
) -> None:
    """Emit `task.status_changed` when a PUT moves a task's status."""
    payload: dict[str, Any] = {"old_status": old_status, "new_status": new_status}
    await _publish(
        redis,
        {
            "type": EVENT_TASK_STATUS_CHANGED,
            "tenant_id": str(task.tenant_id),
            "project_id": str(task.project_id),
            "task_id": str(task.id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "payload": json.dumps(payload),
        },
    )
