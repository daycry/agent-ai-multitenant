"""córtex F4: cortex_curiosity_pursuits (tenant-less, BYPASSRLS — ADR 0074/0078)

Auditoría + idempotencia de la curiosidad autónoma del córtex: cada persecución de
un tema deja una fila con su ciclo de vida (``selected → searching → digested`` |
``skipped`` | ``failed``), el coste/búsquedas consumidas y la memoria ``learning``
generada. Habilita el panel "lo que está aprendiendo", la idempotencia (la memoria
referencia el ``pursuit_id``) y el dedup por tema reciente.

Como el resto del córtex es **tenant-less** (excepción consciente al Principio 1 —
ADR 0074): NO se activa RLS; el aislamiento es por filtro ``owner_user_id`` explícito
en TODO SQL. Aditiva y reversible.

Revision ID: 0095_cortex_curiosity_pursuits
Revises: 0094_cortex_identity
Create Date: 2026-06-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0095_cortex_curiosity_pursuits"
down_revision: str | Sequence[str] | None = "0094_cortex_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cortex_curiosity_pursuits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column(
            "source_entities",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            server_default=sa.text("'selected'"),
            nullable=False,
        ),
        # El "por qué ahora": snapshot de los drives al disparar.
        sa.Column("drive_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # FK lógica a memory_entries.id (la memoria semantic/learning generada). No
        # FK física dura para no acoplar al ciclo de vida de la memoria del owner.
        sa.Column("learning_memory_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=12, scale=6),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "search_count",
            sa.Numeric(precision=10, scale=0),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("surfaced_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
        # El estado siempre pertenece al ciclo de vida conocido.
        sa.CheckConstraint(
            "status IN ('selected', 'searching', 'digested', 'skipped', 'failed')",
            name="ck_cortex_pursuits_status",
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    # La cola "pendiente de abrir" + el conteo diario por estado.
    op.create_index(
        "ix_cortex_pursuits_owner_status",
        "cortex_curiosity_pursuits",
        ["owner_user_id", "status"],
    )
    # Dedup por tema reciente (no re-investigar lo mismo en N días).
    op.create_index(
        "ix_cortex_pursuits_owner_topic_created",
        "cortex_curiosity_pursuits",
        ["owner_user_id", "topic", sa.text("created_at DESC")],
    )
    # CONSCIENTE: tenant-less sobre BYPASSRLS (ADR 0074). NO se hace
    # ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY`` aquí.


def downgrade() -> None:
    op.drop_index(
        "ix_cortex_pursuits_owner_topic_created",
        table_name="cortex_curiosity_pursuits",
    )
    op.drop_index(
        "ix_cortex_pursuits_owner_status",
        table_name="cortex_curiosity_pursuits",
    )
    op.drop_table("cortex_curiosity_pursuits")
