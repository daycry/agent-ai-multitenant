"""córtex F1: cortex_conversations + cortex_turns (tenant-less, BYPASSRLS)

Primeras tablas tenant-less del córtex (ADR 0074, F1). Excepción consciente al
Principio 1 (RLS): el córtex del System Owner es singleton, su historial NO se
scopea por ``tenant_id`` + RLS, sino por un filtro ``owner_user_id`` explícito
en todo SQL (defensa en profundidad, sin RLS de respaldo). ``tenant_id`` es un
discriminante físico para la memoria del owner (Decisión D1), NO un eje de
autorización. Aditiva y reversible.

Revision ID: 0092_cortex_threads
Revises: 0091_system_owner_f0
Create Date: 2026-06-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0092_cortex_threads"
down_revision: str | Sequence[str] | None = "0091_system_owner_f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cortex_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("model_id", sa.String(length=128), nullable=True),
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
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # "Owner's live threads, most-recent first". Partial: soft-deleted drop out.
    op.create_index(
        "ix_cortex_conversations_owner",
        "cortex_conversations",
        ["owner_user_id", sa.text("updated_at DESC")],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "cortex_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column(
            "tools_called",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("rounds", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reasoning_effort", sa.String(length=16), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
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
        sa.CheckConstraint("role IN ('user', 'cortex')", name="ck_cortex_turns_role"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["cortex_conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cortex_turns_conversation",
        "cortex_turns",
        ["conversation_id", "created_at"],
    )
    op.create_index("ix_cortex_turns_owner", "cortex_turns", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_cortex_turns_owner", table_name="cortex_turns")
    op.drop_index("ix_cortex_turns_conversation", table_name="cortex_turns")
    op.drop_table("cortex_turns")
    op.drop_index("ix_cortex_conversations_owner", table_name="cortex_conversations")
    op.drop_table("cortex_conversations")
