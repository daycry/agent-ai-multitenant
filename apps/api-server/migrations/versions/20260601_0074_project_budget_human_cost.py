"""projects.budget_includes_human_cost (Plan 16 Fase D, task_16_12).

Human cost (``hourly_rate * hours_logged`` from the task_16_03
``human_work_sessions``) is always IMPUTED to the plan + project and SEGMENTED
in the 13.7 dashboard (AI cost vs human cost). Whether it also counts toward a
project's BUDGET (consumption + threshold alerts + auto-pause) is a per-project
choice:

  * ``budget_includes_human_cost`` BOOLEAN NOT NULL DEFAULT false — when false
    (the current behaviour, so every existing project is unchanged) only the
    canonical-USD AI cost (``executions.total_cost_usd``) counts against the
    budget. When true the project's human cost is converted to USD and FOLDED
    into the consumption the threshold/pause evaluator compares against the cap.

The column inherits the existing ``projects`` tenant RLS (no policy change).
Additive + backward-compatible: existing rows get ``false`` (AI-only budget).

Single head before this migration is ``0073_human_task_review_mode``; this is
``0074_project_budget_human_cost`` (kept <= 32 chars to fit
``alembic_version.version_num``). Fully reversible: ``downgrade`` drops the
column, restoring 0073 exactly.

Revision ID: 0074_project_budget_human_cost
Revises: 0073_human_task_review_mode
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0074_project_budget_human_cost"
down_revision: str | Sequence[str] | None = "0073_human_task_review_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # NOT NULL DEFAULT false so existing projects keep the current AI-only
    # budget behaviour (only executions.total_cost_usd counts).
    op.add_column(
        "projects",
        sa.Column(
            "budget_includes_human_cost",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "budget_includes_human_cost")
