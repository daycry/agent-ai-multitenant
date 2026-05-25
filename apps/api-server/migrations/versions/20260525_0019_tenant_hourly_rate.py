"""organizations.hourly_rate + hourly_rate_currency (Plan 03 task_03_26).

Tenant-level human-cost rate, configured from the admin panel and
consumed by the cost-breakdown endpoint (task_03_24). NULL keeps the
fallback to the platform default (50 EUR).

Revision ID: 0019_tenant_hourly_rate
Revises: 0018_plan_double_signature
Create Date: 2026-05-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_tenant_hourly_rate"
down_revision: str | Sequence[str] | None = "0018_plan_double_signature"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "hourly_rate",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
    )
    op.add_column(
        "organizations",
        sa.Column(
            "hourly_rate_currency",
            sa.String(length=3),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "hourly_rate_currency")
    op.drop_column("organizations", "hourly_rate")
