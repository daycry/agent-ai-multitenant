"""executions.finish_status — structured agent finish (ADR 0087)

The agent now reports its outcome via the `submit_result(status, summary)` tool
on the HTTP providers (claude_sdk finishes in prose → NULL). `finish_status`
('success'|'failed'|'partial') is a HINT surfaced in the Runs UI and given to the
authoritative self-review — distinct from `status` (the execution lifecycle
outcome). Nullable + additive: existing rows keep working with NULL. Reversible.

Revision ID: 0100_execution_finish_status
Revises: 0099_project_plan_slug
Create Date: 2026-06-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0100_execution_finish_status"
down_revision: str | Sequence[str] | None = "0099_project_plan_slug"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("finish_status", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "finish_status")
