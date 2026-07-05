"""plans.pr_url / pr_branch / pr_error — persist the auto-PR result (P6)

The ``open_plan_pr`` worker task opened the PR best-effort and only logged its
URL; ``plans`` had no column for it, so the API/UI could never show a plan's PR
(audit 2026-07-03, P6). These nullable columns let the task write the result
(or the failure reason) back to the plan. Reversible.

Revision ID: 0102_plan_pr_url
Revises: 0101_tasks_status_check
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0102_plan_pr_url"
down_revision: str | Sequence[str] | None = "0101_tasks_status_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("plans", sa.Column("pr_url", sa.Text(), nullable=True))
    op.add_column("plans", sa.Column("pr_branch", sa.String(length=255), nullable=True))
    op.add_column("plans", sa.Column("pr_error", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("plans", "pr_error")
    op.drop_column("plans", "pr_branch")
    op.drop_column("plans", "pr_url")
