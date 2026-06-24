"""córtex F3: cortex_identity (singleton) + cortex_identity_history (BYPASSRLS — ADR 0074/0077)

Identidad evolutiva del System Owner: un ``identity_state`` JSONB **singleton por
owner** (nombre autoelegido, valores, rasgos Big-Five, narrativa, modelo del owner,
baseline PAD) + un **versionado append-only** con ``diff`` + ``reason`` (auditoría
de cada reescritura).

Como el resto del córtex es **tenant-less** (excepción consciente al Principio 1 —
ADR 0074): NO se activa RLS; el aislamiento es por filtro ``owner_user_id`` explícito
en TODO SQL. La identidad **nunca se borra**, solo se versiona (ADR 0077: protección
de ``kind ∈ {identity, owner_model}``). Aditiva y reversible.

Revision ID: 0094_cortex_identity
Revises: 0093_cortex_affect
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0094_cortex_identity"
down_revision: str | Sequence[str] | None = "0093_cortex_affect"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- cortex_identity (singleton por owner) ---
    op.create_table(
        "cortex_identity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "identity_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        # onboarding | reflection | owner_override (quién escribió el estado actual).
        sa.Column("updated_by", sa.Text(), server_default=sa.text("'onboarding'"), nullable=False),
        # NULL ⇒ onboarding pendiente (se rellena al confirmar el onboarding co-diseñado).
        sa.Column("onboarded_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Invariante singleton: un identity por owner.
    op.create_index(
        "uq_cortex_identity_owner",
        "cortex_identity",
        ["owner_user_id"],
        unique=True,
    )

    # --- cortex_identity_history (versionado append-only) ---
    op.create_table(
        "cortex_identity_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # La versión que esta fila CAPTURA.
        sa.Column("version", sa.Integer(), nullable=False),
        # Snapshot completo del identity_state en esa versión.
        sa.Column(
            "identity_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        # {campo: {before, after}} — auditoría del cambio.
        sa.Column(
            "diff",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        # Resumen 1-línea del cambio (p. ej. el ciclo de reflexión que lo produjo).
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Timeline de versiones del owner (más reciente primero).
    op.create_index(
        "ix_cortex_identity_history_owner_version",
        "cortex_identity_history",
        ["owner_user_id", sa.text("version DESC")],
    )
    # Una sola fila por (owner, version) — versionado monotónico sin duplicados.
    op.create_index(
        "uq_cortex_identity_history_owner_version",
        "cortex_identity_history",
        ["owner_user_id", "version"],
        unique=True,
    )
    # CONSCIENTE: tenant-less sobre BYPASSRLS (ADR 0074). NO se hace
    # ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` aquí.


def downgrade() -> None:
    op.drop_index(
        "uq_cortex_identity_history_owner_version",
        table_name="cortex_identity_history",
    )
    op.drop_index(
        "ix_cortex_identity_history_owner_version",
        table_name="cortex_identity_history",
    )
    op.drop_table("cortex_identity_history")
    op.drop_index("uq_cortex_identity_owner", table_name="cortex_identity")
    op.drop_table("cortex_identity")
