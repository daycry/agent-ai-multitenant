"""córtex F2: cortex_affect_snapshots (tenant-less, BYPASSRLS — ADR 0075)

Serie temporal append-only e inmutable del estado del motor afectivo del System
Owner (PAD + mood + drives). Como el resto del córtex es tenant-less (excepción
consciente al Principio 1 — ADR 0074): NO se activa RLS; el aislamiento es por
filtro ``owner_user_id`` explícito en todo SQL. Aditiva y reversible.

Revision ID: 0093_cortex_affect
Revises: 0092_cortex_threads
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0093_cortex_affect"
down_revision: str | Sequence[str] | None = "0092_cortex_threads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cortex_affect_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Emoción viva (capa rápida).
        sa.Column("valence", sa.Float(), nullable=False),
        sa.Column("arousal", sa.Float(), nullable=False),
        sa.Column("dominance", sa.Float(), nullable=False),
        sa.Column("intensity", sa.Float(), nullable=False),
        # Mood (capa lenta, EWMA).
        sa.Column("mood_valence", sa.Float(), nullable=False),
        sa.Column("mood_arousal", sa.Float(), nullable=False),
        sa.Column("mood_dominance", sa.Float(), nullable=False),
        sa.Column("mood_label", sa.String(length=32), nullable=False),
        # Drives homeostáticos.
        sa.Column(
            "drives",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("appraisal_reason", sa.Text(), nullable=True),
        sa.Column("source_turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_turn_id"], ["cortex_turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # "Último snapshot" + /affect/timeseries.
    op.create_index(
        "ix_cortex_affect_snapshots_owner_created",
        "cortex_affect_snapshots",
        ["owner_user_id", sa.text("created_at DESC")],
    )
    # /episodes?emotion= (filtro por etiqueta de mood).
    op.create_index(
        "ix_cortex_affect_snapshots_owner_mood_label",
        "cortex_affect_snapshots",
        ["owner_user_id", "mood_label"],
    )
    # Idempotencia del distilador: un snapshot por turno (parcial: los snapshots
    # de decay/mantenimiento, sin turno, no chocan).
    op.create_index(
        "uq_cortex_affect_snapshot_per_turn",
        "cortex_affect_snapshots",
        ["source_turn_id"],
        unique=True,
        postgresql_where=sa.text("source_turn_id IS NOT NULL"),
    )
    # CONSCIENTE: tenant-less sobre BYPASSRLS (ADR 0074). NO se hace
    # ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` aquí.


def downgrade() -> None:
    op.drop_index("uq_cortex_affect_snapshot_per_turn", table_name="cortex_affect_snapshots")
    op.drop_index(
        "ix_cortex_affect_snapshots_owner_mood_label",
        table_name="cortex_affect_snapshots",
    )
    op.drop_index(
        "ix_cortex_affect_snapshots_owner_created",
        table_name="cortex_affect_snapshots",
    )
    op.drop_table("cortex_affect_snapshots")
