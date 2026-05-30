"""Write a per-sync audit row from a sync run's summary (Plan 11 task_11_19).

Every price-catalog sync — manual (the admin-panel endpoints) or scheduled
(the Celery-beat job) — funnels its :class:`SyncSummary` through
:func:`write_sync_audit`, which builds a **compact diff** + the counts and
inserts ONE immutable :class:`PriceSyncAudit` row in the SAME transaction as
the catalog writes. So nothing is ever applied to ``model_prices`` without a
matching audit trail (the guarantee the plan's human test ``human_11_04``
checks: "who, what changed, from where").

The diff is deliberately compact — model keys + the held large-increase
spikes + skipped entries, not the entire feed — so the history table stays
small while still telling a human exactly what moved. The actor is a
free-form string (``"user:<uuid>"`` for a human, ``"scheduler"`` for the
cron); :func:`audit_actor` derives it from an optional user id + the trigger.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.db.price_sync_audit import (
    ACTOR_SCHEDULER,
    ACTOR_SYSTEM,
    PriceSyncAudit,
    SyncTrigger,
    actor_for_user,
)
from api_server.pricing.litellm_sync import SyncSummary

# Cap the per-key lists baked into the diff JSONB so a huge feed never bloats
# the audit row. The headline counts are always exact; the lists are a
# representative sample (newest/first by the summary's deterministic order).
_MAX_KEYS = 200


def audit_actor(trigger: SyncTrigger, actor_user_id: UUID | None) -> str:
    """The canonical ``actor`` string for a sync run.

    A human-run sync (``actor_user_id`` set) is ``"user:<uuid>"``. A scheduled
    run is attributed to the ``"scheduler"``; any other unattributed
    programmatic run falls back to ``"system"``.
    """
    if actor_user_id is not None:
        return actor_for_user(actor_user_id)
    if trigger is SyncTrigger.SCHEDULED:
        return ACTOR_SCHEDULER
    return ACTOR_SYSTEM


def build_diff(summary: SyncSummary) -> dict[str, object]:
    """A compact, JSON-serialisable diff of what a sync changed.

    Carries the discontinued model keys, the held large-increase spikes
    (provider / model / field / old→new / pct), and the skipped feed entries.
    Created / updated counts are headline columns on the row; the diff keeps
    the *detail* a human reads to see what moved. Bounded by ``_MAX_KEYS``.
    """
    return {
        "large_increases": [
            {
                "provider": li.provider,
                "model_id": li.model_id,
                "modality": li.modality,
                "field": li.field,
                "old_price": str(li.old_price),
                "new_price": str(li.new_price),
                "pct_increase": li.pct_increase,
            }
            for li in summary.large_increases[:_MAX_KEYS]
        ],
        "discontinued": [
            {
                "provider": d.provider,
                "model_id": d.model_id,
                "modality": d.modality,
            }
            for d in summary.discontinued_models[:_MAX_KEYS]
        ],
        "skipped": [
            {"model_key": s.model_key, "reason": s.reason} for s in summary.skipped[:_MAX_KEYS]
        ],
    }


async def write_sync_audit(
    session: AsyncSession,
    *,
    summary: SyncSummary,
    trigger: SyncTrigger,
    actor_user_id: UUID | None = None,
    source: str = "litellm",
    feed_url: str | None = None,
    confirmed: bool = False,
) -> UUID:
    """Insert one immutable audit row for a sync run; return its id.

    Must be called inside the SAME open transaction as the catalog writes the
    ``summary`` describes, so the audit and the catalog change commit (or roll
    back) together — there is never a silently-applied change with no audit.
    The row is append-only (the table has no UPDATE/DELETE RLS policy for the
    app role); this helper only ever INSERTs.
    """
    audit_id = uuid7()
    await session.execute(
        insert(PriceSyncAudit).values(
            id=audit_id,
            actor=audit_actor(trigger, actor_user_id),
            actor_user_id=actor_user_id,
            trigger=trigger.value,
            source=source,
            feed_url=feed_url,
            fetched=summary.fetched,
            created=summary.created,
            updated=summary.updated,
            unchanged=summary.unchanged,
            discontinued=summary.discontinued,
            skipped=summary.skipped_count,
            held_large_increases=len(summary.large_increases),
            confirmed=confirmed,
            diff=build_diff(summary),
        )
    )
    return audit_id


__all__ = [
    "audit_actor",
    "build_diff",
    "write_sync_audit",
]
