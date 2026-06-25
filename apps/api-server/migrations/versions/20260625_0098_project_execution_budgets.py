"""per-project run budget override: projects.execution_budgets

prod-06 task_prod06_budget_02 (workers-10): the dispatcher threaded
``budgets: None`` into every ``run_execution``, so a runaway agent loop was
bounded only by the agent-runtime's compiled-in defaults. ``execution_budgets``
is the per-project OVERRIDE envelope (a subset of the runtime ``Budgets`` keys);
the dispatcher merges it over the platform default and clamps to the runtime
ceiling. Nullable + additive — NULL means "no override, use the platform
default". Reversible.

Revision ID: 0098_project_execution_budgets
Revises: 0097_document_enqueued_at
Create Date: 2026-06-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0098_project_execution_budgets"
down_revision: str | Sequence[str] | None = "0097_document_enqueued_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("execution_budgets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "execution_budgets")
