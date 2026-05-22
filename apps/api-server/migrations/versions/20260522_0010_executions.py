"""executions table — agent loop runs with steps_log JSONB (task_02_11).

Per spec §13: every run of the agent loop against a task is captured as
an `executions` row. The `steps_log` JSONB column holds the append-only
list of step records (one per graph node, model call, tool call and
memory read) produced by `agent_runtime`; the `total_*` / `*_count`
columns are denormalised usage roll-ups.

Same tenant-isolation RLS as the rest of the domain. No builtin-read
policy — executions are never platform-global.

Revision ID: 0010_executions
Revises: 0009_fn_compute_task_ready
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_executions"
down_revision: str | Sequence[str] | None = "0009_fn_compute_task_ready"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default=sa.text("'running'")
        ),
        sa.Column("abort_code", sa.String(length=64), nullable=True),
        sa.Column("output", sa.Text(), nullable=True),
        # steps_log: one dict per step (node / model_call / tool_call / memory_read).
        sa.Column(
            "steps_log",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("iterations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(precision=14, scale=6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("model_call_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
        sa.CheckConstraint("iterations >= 0", name="ck_executions_iterations_non_negative"),
        sa.CheckConstraint("total_tokens >= 0", name="ck_executions_total_tokens_non_negative"),
        sa.CheckConstraint("total_cost_usd >= 0", name="ck_executions_total_cost_non_negative"),
    )
    op.create_index("ix_executions_tenant_id", "executions", ["tenant_id"])
    op.create_index("ix_executions_task_id", "executions", ["task_id"])
    op.create_index("ix_executions_tenant_status", "executions", ["tenant_id", "status"])

    # RLS: tenant isolation, same shape as the rest of the domain.
    op.execute("ALTER TABLE executions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE executions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY executions_tenant_isolation"
        " ON executions FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS executions_tenant_isolation ON executions")
    op.execute("ALTER TABLE executions DISABLE ROW LEVEL SECURITY")
    op.drop_table("executions")
