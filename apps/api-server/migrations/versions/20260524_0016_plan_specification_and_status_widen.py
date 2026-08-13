"""plans.specification JSONB + status widened to VARCHAR(32) — task_03_14/16.

Two related schema changes that arrive together because task_03_14
(persistence of the canonical template) and task_03_16 (full lifecycle
state machine) ship in the same Fase D:

  * add `plans.specification` JSONB to hold the canonical template
    (summary / phases / tasks_specification / estimates / metadata).
  * widen `plans.status` to VARCHAR(32) so it can carry the new
    `pending_human_validation` value (24 chars) the state machine
    needs alongside the historical `draft`, `approved`, ... values.

Reversible: downgrade truncates anything past 16 chars and drops the
new column.

Revision ID: 0016_plan_spec_status_widen
Revises: 0015_custom_chat_modes
Create Date: 2026-05-24
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016_plan_spec_status_widen"
down_revision: str | Sequence[str] | None = "0015_custom_chat_modes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "specification",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.alter_column(
        "plans",
        "status",
        type_=sa.String(length=32),
        existing_type=sa.String(length=16),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Truncate first so the column-type shrink never breaks on a value
    # that no longer fits (mirrors migration 0012's approach for
    # executions.status).
    op.execute("ALTER TABLE plans ALTER COLUMN status TYPE VARCHAR(16) USING substr(status, 1, 16)")
    op.drop_column("plans", "specification")
