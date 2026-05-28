"""Repository for `task_audit_events` (Plan 06.5 task_06_5_03).

Append-only history per task. This module deliberately offers only
two operations:

  * `append_audit_event` — INSERT a new row.
  * `list_history` — SELECT events for a task in chronological order.

There is no UPDATE / DELETE — the append-only invariant is enforced
at this layer. The Python `AuditEvent` dataclass
(`api_server.task_lifecycle.AuditEvent`) maps 1:1 to the table row;
the helper `to_audit_event_dataclass` does the conversion when
serializing for `GET /api/v1/tasks/{task_id}/history` (Plan 06.5
task_06_5_04).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import TaskAuditEvent


async def append_audit_event(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    task_id: UUID,
    kind: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    at: datetime | None = None,
) -> TaskAuditEvent:
    """INSERT a new audit row. `at` defaults to `now()` if not given;
    the dataclass uses unix floats, the DB uses TIMESTAMPTZ — the
    repository converts at the boundary."""
    row = TaskAuditEvent(
        tenant_id=tenant_id,
        task_id=task_id,
        at=at or datetime.now(UTC),
        kind=kind,
        actor=actor,
        payload=payload or {},
    )
    session.add(row)
    await session.flush()
    return row


async def list_history(
    session: AsyncSession,
    task_id: UUID,
    *,
    limit: int | None = None,
) -> list[TaskAuditEvent]:
    """All events for one task, oldest first.

    Caller can pass `limit` if it only needs the last N (the endpoint
    paginates differently — newest first — and reverses on the wire).
    """
    stmt = (
        select(TaskAuditEvent).where(TaskAuditEvent.task_id == task_id).order_by(TaskAuditEvent.at)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


def to_dict(event: TaskAuditEvent) -> dict[str, Any]:
    """JSON-safe projection of one event, ready for the API."""
    return {
        "id": str(event.id),
        "task_id": str(event.task_id),
        "at": event.at.timestamp(),
        "kind": event.kind,
        "actor": event.actor,
        "payload": dict(event.payload or {}),
    }
