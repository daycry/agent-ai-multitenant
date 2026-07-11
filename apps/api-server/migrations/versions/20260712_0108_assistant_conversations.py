"""Hilos persistentes del asistente de tenants (A1, investigación 2026-07-11).

El asistente era stateless (criterio ``human_10_04`` incumplido: «mantiene
contexto entre mensajes»). Crea ``assistant_conversations`` +
``assistant_turns`` — espejo tenant-scoped (RLS) del patrón córtex.

Reversible: el downgrade tira políticas, RLS e índices y elimina las tablas.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0108_assistant_conversations"
down_revision: str | Sequence[str] | None = "0107_chunks_fts_es_unaccent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("assistant_conversations", "assistant_turns")


def _enable_rls(table_name: str) -> None:
    policy = f"{table_name}_tenant_isolation"
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {policy}"
        f" ON {table_name} FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    op.create_table(
        "assistant_conversations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_assistant_conversations_user",
        "assistant_conversations",
        ["tenant_id", "user_id", "updated_at"],
    )
    op.create_index(
        "ix_assistant_conversations_tenant_id", "assistant_conversations", ["tenant_id"]
    )

    op.create_table(
        "assistant_turns",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tools_called", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("rounds", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_assistant_turns_role"),
    )
    op.create_index(
        "ix_assistant_turns_conversation", "assistant_turns", ["conversation_id", "created_at"]
    )
    op.create_index("ix_assistant_turns_tenant_id", "assistant_turns", ["tenant_id"])

    for table in _TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_assistant_turns_tenant_id", table_name="assistant_turns")
    op.drop_index("ix_assistant_turns_conversation", table_name="assistant_turns")
    op.drop_table("assistant_turns")
    op.drop_index("ix_assistant_conversations_tenant_id", table_name="assistant_conversations")
    op.drop_index("ix_assistant_conversations_user", table_name="assistant_conversations")
    op.drop_table("assistant_conversations")
