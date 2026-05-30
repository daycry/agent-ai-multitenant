"""Pydantic schema for the price-sync audit history (Plan 11 task_11_19).

``GET /admin/model-prices/sync/audit`` lists the immutable per-sync audit
rows (newest first) that feed the "Modelos & Precios" screen history: who ran
each sync, when, from what source/trigger, the counts, the held spikes, and a
compact diff. The audit is platform-level (System-Admin-surfaced); these
schemas only shape the read.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api_server.db.price_sync_audit import PriceSyncAudit

_BASE_CONFIG = ConfigDict(populate_by_name=True)


class PriceSyncAuditResponse(BaseModel):
    """One immutable record of a price-catalog sync run.

    ``actor`` names the human (``"user:<uuid>"``) or system actor
    (``"scheduler"`` / ``"system"``); ``trigger`` is ``manual`` or
    ``scheduled``. The counts mirror the run's :class:`SyncSummary`;
    ``held_large_increases`` counts the >10% rises deferred for confirmation;
    ``diff`` is the compact what-changed detail (held spikes, discontinued
    model keys, skipped feed entries).
    """

    model_config = _BASE_CONFIG

    id: UUID
    actor: str
    actor_user_id: UUID | None
    trigger: str
    source: str
    feed_url: str | None
    fetched: int
    created: int
    updated: int
    unchanged: int
    discontinued: int
    skipped: int
    held_large_increases: int
    confirmed: bool
    diff: dict[str, Any]
    created_at: datetime


def to_audit_response(row: PriceSyncAudit) -> PriceSyncAuditResponse:
    """Map a :class:`PriceSyncAudit` ORM row to its response model."""
    return PriceSyncAuditResponse(
        id=row.id,
        actor=row.actor,
        actor_user_id=row.actor_user_id,
        trigger=row.trigger,
        source=row.source,
        feed_url=row.feed_url,
        fetched=row.fetched,
        created=row.created,
        updated=row.updated,
        unchanged=row.unchanged,
        discontinued=row.discontinued,
        skipped=row.skipped,
        held_large_increases=row.held_large_increases,
        confirmed=row.confirmed,
        diff=row.diff,
        created_at=row.created_at,
    )


__all__ = ["PriceSyncAuditResponse", "to_audit_response"]
