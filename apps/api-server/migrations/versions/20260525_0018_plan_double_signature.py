"""plans.first_approved_{by,at} for double-signature approval flow
(Plan 03 task_03_25).

Two new nullable columns on `plans`:

  - ``first_approved_by`` (FK users, ON DELETE SET NULL): the user who
    cast the first of two signatures on a plan whose AI cost estimate
    exceeded the platform-configured double-signature threshold.
  - ``first_approved_at`` (TIMESTAMPTZ): when that first signature
    happened. The existing ``approved_by``/``approved_at`` columns
    keep their meaning ("the signature that flipped the plan to
    approved"); on a double-firma plan they hold the *second* signer.

The new `pending_second_approval` status value fits inside the
existing VARCHAR(32) `status` column (widened by migration 0016).

Revision ID: 0018_plan_double_signature
Revises: 0017_plan_comments
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_plan_double_signature"
down_revision: str | Sequence[str] | None = "0017_plan_comments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column(
            "first_approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "plans",
        sa.Column(
            "first_approved_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("plans", "first_approved_at")
    op.drop_column("plans", "first_approved_by")
