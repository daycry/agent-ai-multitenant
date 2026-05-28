"""agent_knowledge_bases junction (Plan 06.9 task_06_9_01).

KBs were tied to projects only via `kb_projects` (Plan 04 task_04_07).
That couples stack-specific docs to the project ("Python+FastAPI
conventions") AND role-specific docs to the project too ("API REST
design principles") — even though the role doc applies to every
project the agent runs in.

This migration adds the second axis: KBs can also be granted to an
**agent template** (`global_tenant_template` or `project_local`,
NOT `global_builtin` which the system owns). At retrieval time the
resolver unions:

    KBs visibles = KBs del proyecto + KBs del agente + KBs globales

The shape mirrors `kb_projects`:

  - Composite primary key (agent_id, kb_id) — a KB grant is unique
    per (agent, KB) pair; re-granting is a no-op.
  - tenant_id denormalised so RLS isolates the junction without a
    join to the parent rows (same trick as kb_projects).
  - ON DELETE CASCADE on both FKs — if you delete the agent or the
    KB, the grant disappears.
  - granted_by FK to users.id (SET NULL on user delete) for audit.

Revision ID: 0026_agent_knowledge_bases
Revises: 0025_task_audit_events
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0026_agent_knowledge_bases"
down_revision: str | Sequence[str] | None = "0025_task_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_knowledge_bases",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kb_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "granted_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_agent_kbs_agent", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["kb_id"],
            ["knowledge_bases.id"],
            name="fk_agent_kbs_kb",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["users.id"], name="fk_agent_kbs_granted_by", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("agent_id", "kb_id", name="pk_agent_knowledge_bases"),
    )
    op.create_index(
        "ix_agent_kbs_kb_id",
        "agent_knowledge_bases",
        ["kb_id"],
    )
    op.create_index(
        "ix_agent_kbs_tenant_id",
        "agent_knowledge_bases",
        ["tenant_id"],
    )

    # RLS — same policy as kb_projects (tenant_id from denormalised column).
    op.execute("ALTER TABLE agent_knowledge_bases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_knowledge_bases FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_knowledge_bases_tenant_isolation "
        "ON agent_knowledge_bases FOR ALL "
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS agent_knowledge_bases_tenant_isolation ON agent_knowledge_bases"
    )
    op.execute("ALTER TABLE agent_knowledge_bases DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_agent_kbs_tenant_id", table_name="agent_knowledge_bases")
    op.drop_index("ix_agent_kbs_kb_id", table_name="agent_knowledge_bases")
    op.drop_table("agent_knowledge_bases")
