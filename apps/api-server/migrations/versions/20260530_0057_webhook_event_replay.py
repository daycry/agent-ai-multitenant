"""incoming_webhook_events.replayed_from_event_id — replay audit link.

Plan 13 Fase C task_13_12. Adds ONE column to the received-event table:
``replayed_from_event_id`` (a self-FK, NULLABLE). It records that a row is a
REPLAY (operator-initiated re-run of a previously recorded delivery, for
debugging) pointing at the ORIGINAL event it re-ran; NULL for a genuine inbound
delivery. A replay is its OWN audit row — it carries ``delivery_id = NULL`` so
it never collides with the source's partial UNIQUE on ``(config_id,
delivery_id)`` — so the deliveries trail shows both the original event AND each
replay of it, fully audited.

Multi-tenancy (CLAUDE.md principle 1): the column lives on the already
tenant + project scoped ``incoming_webhook_events`` row (the ``tenant_isolation``
RLS policy from 0055 covers it), and the FK targets the SAME table, so a replay
can only ever reference an event in the same tenant. No new RLS is needed.

The self-FK is ``ON DELETE SET NULL`` so dropping a source event keeps its
replay audit rows (the replay still happened). Indexed so "replays of event X"
is cheap.

Single head before this migration is ``0056_webhook_action_mappings``; this is
``0057_webhook_event_replay``. Fully reversible: ``downgrade`` drops the index
then the column. Proven by an up / down to ``0040_sso_email_domains`` / up cycle.

Revision ID: 0057_webhook_event_replay
Revises: 0056_webhook_action_mappings
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0057_webhook_event_replay"
down_revision: str | Sequence[str] | None = "0056_webhook_action_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "incoming_webhook_events",
        sa.Column("replayed_from_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_incoming_webhook_events_replayed_from",
        "incoming_webhook_events",
        "incoming_webhook_events",
        ["replayed_from_event_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # "Replays of event X" lookup (audit) — only the rows that ARE replays.
    op.create_index(
        "ix_incoming_webhook_events_replayed_from",
        "incoming_webhook_events",
        ["replayed_from_event_id"],
        postgresql_where=sa.text("replayed_from_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_incoming_webhook_events_replayed_from", table_name="incoming_webhook_events")
    op.drop_constraint(
        "fk_incoming_webhook_events_replayed_from",
        "incoming_webhook_events",
        type_="foreignkey",
    )
    op.drop_column("incoming_webhook_events", "replayed_from_event_id")
