"""custom_chat_modes table — Plan 03 task_03_08.

Materialises `api_server.db.custom_chat_mode.CustomChatMode`. Per-tenant
custom modes that extend the built-in catalog with arbitrary names
(e.g. "design-review"), each carrying its own system prompt and tool
whitelist. Unique by (tenant_id, name) for live rows so the
`Conversation.custom_mode_name` lookup is unambiguous.

Same RLS / FORCE policy shape as the rest of the domain.

Revision ID: 0015_custom_chat_modes
Revises: 0014_conversations_messages
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015_custom_chat_modes"
down_revision: str | Sequence[str] | None = "0014_conversations_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "custom_chat_modes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("label_es", sa.String(length=120), nullable=False),
        sa.Column("label_en", sa.String(length=120), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column(
            "allowed_tools",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "planning_subgraph",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_custom_chat_modes_tenant_name"),
    )
    op.create_index("ix_custom_chat_modes_tenant_id", "custom_chat_modes", ["tenant_id"])
    op.create_index(
        "ix_custom_chat_modes_tenant_name",
        "custom_chat_modes",
        ["tenant_id", "name"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.execute("ALTER TABLE custom_chat_modes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE custom_chat_modes FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY custom_chat_modes_tenant_isolation"
        " ON custom_chat_modes FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS custom_chat_modes_tenant_isolation ON custom_chat_modes")
    op.execute("ALTER TABLE custom_chat_modes DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_custom_chat_modes_tenant_name", table_name="custom_chat_modes")
    op.drop_index("ix_custom_chat_modes_tenant_id", table_name="custom_chat_modes")
    op.drop_table("custom_chat_modes")
