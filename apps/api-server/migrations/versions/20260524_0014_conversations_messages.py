"""conversations + messages tables — Plan 03 task_03_02.

Materialises the ORM declared in `api_server.db.conversation` (task_03_01):

  conversations   one chat session per project; current_mode lives here
                  so a mode switch is one row update.
  messages        one entry per turn; mode captured at send time;
                  attachments JSONB; related_plan_id soft-FK; CHECK on
                  author_kind <-> author_*_id consistency.

The migration also promotes two soft-FKs to real ones now that both
endpoints exist:

  - plans.conversation_id              -> conversations.id  ON DELETE SET NULL
  - conversations.related_plan_id      -> plans.id          ON DELETE SET NULL

Both directions use SET NULL so deleting a plan does not wipe the
chat history that produced it, and deleting a conversation leaves
its plans in place.

RLS shape is identical to the rest of the domain: `tenant_id` matched
against `app.tenant_id` set by the FastAPI middleware on every request.

Revision ID: 0014_conversations_messages
Revises: 0013_task_status_widen
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_conversations_messages"
down_revision: str | Sequence[str] | None = "0013_task_status_widen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_TABLES: tuple[str, ...] = ("conversations", "messages")


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation"
        f" ON {table} FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # conversations
    # -----------------------------------------------------------------------
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column(
            "current_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'planning'"),
        ),
        sa.Column("custom_mode_name", sa.String(length=120), nullable=True),
        # related_plan_id FK is added after the table exists (avoids
        # ordering issues across this migration).
        sa.Column("related_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_by",
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
        sa.CheckConstraint(
            "(current_mode = 'custom' AND custom_mode_name IS NOT NULL)"
            " OR (current_mode <> 'custom' AND custom_mode_name IS NULL)",
            name="ck_conversations_custom_mode_name_consistency",
        ),
    )
    op.create_index("ix_conversations_tenant_id", "conversations", ["tenant_id"])
    op.create_index(
        "ix_conversations_tenant_project",
        "conversations",
        ["tenant_id", "project_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_conversations_related_plan", "conversations", ["related_plan_id"])

    # -----------------------------------------------------------------------
    # messages
    # -----------------------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column(
            "attachments",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Soft-FK; promoted to real FK below.
        sa.Column("related_plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "is_summary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
        sa.CheckConstraint(
            "(author_kind = 'user'   AND author_user_id  IS NOT NULL"
            "                        AND author_agent_id IS NULL)"
            " OR (author_kind = 'agent' AND author_agent_id IS NOT NULL"
            "                            AND author_user_id  IS NULL)"
            " OR (author_kind = 'system' AND author_user_id IS NULL"
            "                             AND author_agent_id IS NULL)",
            name="ck_messages_author_kind_consistency",
        ),
    )
    op.create_index("ix_messages_tenant_id", "messages", ["tenant_id"])
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id", "id"])
    op.create_index(
        "ix_messages_author_agent",
        "messages",
        ["author_agent_id"],
        postgresql_where=sa.text("author_agent_id IS NOT NULL"),
    )

    # -----------------------------------------------------------------------
    # Promote the two soft-FKs to real ones now that both tables exist.
    # -----------------------------------------------------------------------
    op.create_foreign_key(
        "fk_conversations_related_plan_id",
        source_table="conversations",
        referent_table="plans",
        local_cols=["related_plan_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_messages_related_plan_id",
        source_table="messages",
        referent_table="plans",
        local_cols=["related_plan_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_plans_conversation_id",
        source_table="plans",
        referent_table="conversations",
        local_cols=["conversation_id"],
        remote_cols=["id"],
        ondelete="SET NULL",
    )

    # -----------------------------------------------------------------------
    # RLS
    # -----------------------------------------------------------------------
    for table in _RLS_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    # Drop in reverse FK order so downgrade is reversible even with data.
    op.drop_constraint("fk_plans_conversation_id", "plans", type_="foreignkey")
    op.drop_constraint("fk_messages_related_plan_id", "messages", type_="foreignkey")
    op.drop_constraint("fk_conversations_related_plan_id", "conversations", type_="foreignkey")

    for table in reversed(_RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_messages_author_agent", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_index("ix_messages_tenant_id", table_name="messages")
    op.drop_table("messages")

    op.drop_index("ix_conversations_related_plan", table_name="conversations")
    op.drop_index("ix_conversations_tenant_project", table_name="conversations")
    op.drop_index("ix_conversations_tenant_id", table_name="conversations")
    op.drop_table("conversations")
