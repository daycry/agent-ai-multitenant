"""Per-sync audit trail for the price catalog (Plan 11 task_11_19).

Every refresh of ``model_prices`` from the community LiteLLM price feed —
whether a System Admin clicked "Sincronizar precios" (manual) or the daily
Celery-beat job fired (scheduled) — writes one **immutable** audit row here.
The audit answers, for any past sync, the human questions the plan's human
test (``human_11_04``) asks: **who** ran it, **when**, from **what source**,
**what counts** (created / updated / discontinued / unchanged / skipped),
which **large increases were held** for confirmation, and a **compact diff**
of what actually changed. Nothing is ever applied to the catalog without a
matching row here — the sync paths write the audit in the same transaction as
the catalog writes.

Tenancy decision (mirrors ``model_prices``, task_11_11): **platform-global,
NOT tenant-scoped.** A price sync is a System-Admin platform action, not a
tenant's data, so the table carries **no ``tenant_id``**. The read/write
split is enforced at the DB layer by the same pattern as the catalog
(migration 0051):

  - RLS is ENABLED + FORCED with a single SELECT-only policy ``USING (true)``
    so the catalog's global-read story holds (any authenticated session may
    read the history — the admin screen surfaces it), and
  - there is **no INSERT/UPDATE/DELETE policy at all**, so a NOBYPASSRLS /
    tenant session is denied every write while the BYPASSRLS System-Admin
    session (``get_admin_session``) and the worker's BYPASSRLS role write
    freely. The absence of UPDATE/DELETE policies *also* makes the log
    **append-only / immutable** for the app role — an audit row, once
    written, cannot be edited or erased through the application path
    (mirrors the ``marketplace_audit_entries`` append-only hardening of
    migration 0043, and ``executions`` / the foundations ``audit_log``).

A tenant CANNOT trigger or schedule a sync (the endpoints are
System-Admin-gated, the beat schedule lives in the platform process, and the
enable flag is a platform setting), so a tenant never authors a row here; the
SELECT-only policy merely lets the catalog-read story stay uniform.

``actor`` is a free-form string that names a human (``"user:<uuid>"``) or a
system actor (``"scheduler"`` for the cron, ``"system"`` for an unattributed
programmatic run) — mirroring :class:`MarketplaceAuditEntry.actor` and
``TaskAuditEvent.actor``. When a human ran it, ``actor_user_id`` carries the
acting System Admin's id as a FK (``ON DELETE SET NULL`` — the audit outlives
the user). ``trigger`` records *how* the sync was started.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from api_server.db.base import Base, UUIDPrimaryKeyMixin

# Stable actor strings for non-human (programmatic) syncs. A human-run sync
# uses ``"user:<uuid>"`` (see :func:`actor_for_user`).
ACTOR_SCHEDULER = "scheduler"
ACTOR_SYSTEM = "system"


class SyncTrigger(enum.StrEnum):
    """How a sync run was started (the *who/how*, distinct from the data source).

    - ``manual``:    a System Admin invoked it from the admin panel
                     (``POST /admin/model-prices/sync`` or ``.../sync/apply``).
    - ``scheduled``: the Celery-beat job (``workers.sync_model_prices``) fired
                     on its configured cadence. Attributed to the scheduler,
                     never a user.

    Extend by adding members; never rename existing ones — historical rows
    still reference the old string value.
    """

    MANUAL = "manual"
    SCHEDULED = "scheduled"


def actor_for_user(user_id: UUID) -> str:
    """The canonical ``actor`` string for a human-run sync."""
    return f"user:{user_id}"


class PriceSyncAudit(Base, UUIDPrimaryKeyMixin):
    """One immutable audit record of a price-catalog sync run (task_11_19).

    Platform-global (no ``tenant_id``) and append-only — written in the same
    transaction as the catalog writes it describes, never updated or deleted
    through the app path. We declare the single ``created_at`` explicitly
    (rather than via :class:`TimestampMixin`) because an immutable record has
    no ``updated_at``.
    """

    __tablename__ = "price_sync_audit"
    __table_args__ = (
        # The history screen's primary query: newest-first by time.
        Index("ix_price_sync_audit_created", "created_at"),
        # Filter the history by trigger (manual vs scheduled) over time.
        Index("ix_price_sync_audit_trigger_created", "trigger", "created_at"),
        # FK lookup for the acting System Admin (no unindexed FK).
        Index(
            "ix_price_sync_audit_actor_user",
            "actor_user_id",
            postgresql_where=text("actor_user_id IS NOT NULL"),
        ),
    )

    # --- who -----------------------------------------------------------------
    # Free-form actor: "user:<uuid>" for a human, "scheduler"/"system" for a
    # programmatic run. Mirrors MarketplaceAuditEntry.actor / TaskAuditEvent.
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    # The acting System Admin's id when a human ran it (NULL for the cron, or
    # once the user is deleted — the audit outlives the user).
    actor_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # How the sync was started: manual (admin panel) or scheduled (cron).
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    # Where the data came from — the price source the rows were written with
    # (``litellm`` for the community feed; mirrors PriceSource so the history
    # can record a provider-API sync later without a schema change).
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'litellm'")
    )
    # The feed URL the run read (an internal mirror or the public JSON). NULL
    # for a no-op disabled run that never fetched.
    feed_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # --- counts (the headline summary) ---------------------------------------
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    discontinued: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # How many price rises >10% were HELD for confirmation (not applied) on a
    # run that did not confirm them. 0 when nothing spiked or it was confirmed.
    held_large_increases: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    # True when the run explicitly confirmed large increases (applied spikes).
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # --- the compact diff + held spikes --------------------------------------
    # A small structured record of what changed: the created / updated /
    # discontinued model keys, the held large-increase spikes (provider /
    # model / field / old→new / pct), and the skipped feed entries. JSONB so
    # the shape can evolve migration-free; bounded to a compact summary, not
    # the whole feed.
    diff: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- when ----------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"PriceSyncAudit(id={self.id!r}, actor={self.actor!r}, "
            f"trigger={self.trigger!r}, created={self.created!r}, updated={self.updated!r})"
        )


__all__ = [
    "ACTOR_SCHEDULER",
    "ACTOR_SYSTEM",
    "PriceSyncAudit",
    "SyncTrigger",
    "actor_for_user",
]
