"""organizations budget fields (Plan 11.1 Fase B, task_11_1_04).

Adds the **tenant-level** budget controls to ``organizations`` — the
project-level budget columns already exist on ``projects`` since the
domain-minimum migration (0002), so this migration only fills the
tenant side of the spec §28.7 tenant-vs-project budget interaction:

  - ``tenant_budget_amount``            Numeric(14,2), nullable — the cap
                                        in ``tenant_budget_currency`` (NULL
                                        = no tenant-level budget).
  - ``tenant_budget_currency``          char(3), nullable — the currency the
                                        amount is denominated in. Cost is
                                        always canonical USD; the cap is
                                        converted to USD when evaluated.
  - ``tenant_budget_period``            varchar(16), nullable — one of the
                                        ``BudgetPeriod`` values
                                        (weekly/monthly/quarterly/yearly/
                                        custom).
  - ``tenant_budget_period_start_day``  Integer, nullable — only meaningful
                                        for the ``custom`` period: the day
                                        the cycle starts.
  - ``tenant_budget_period_length_days`` Integer, nullable — only meaningful
                                        for the ``custom`` period: cycle
                                        length in days.

All columns are NULLABLE with no server_default: a tenant has NO budget
until one is explicitly configured (the default state is "unbudgeted"),
so existing rows need no backfill. A non-negative CHECK mirrors the
``projects`` constraint (``ck_projects_budget_non_negative``) so a tenant
budget can never be stored negative.

Single head before this migration is ``0062_exchange_rates`` (Plan 11.1
Fase A); this is ``0063_organization_budget``. Fully reversible:
``downgrade`` drops the four columns + the amount column + the CHECK,
restoring 0062 exactly. Downgrade target for the plan-wide reversibility
proof is ``0040_sso_email_domains``.

Revision ID: 0063_organization_budget
Revises: 0062_exchange_rates
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_organization_budget"
down_revision: str | Sequence[str] | None = "0062_exchange_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("tenant_budget_amount", sa.Numeric(precision=14, scale=2), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("tenant_budget_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("tenant_budget_period", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("tenant_budget_period_start_day", sa.Integer(), nullable=True),
    )
    op.add_column(
        "organizations",
        sa.Column("tenant_budget_period_length_days", sa.Integer(), nullable=True),
    )
    # A configured tenant budget can never be negative (mirrors the
    # projects.budget_amount CHECK). NULL stays valid (no budget).
    op.create_check_constraint(
        "ck_organizations_tenant_budget_non_negative",
        "organizations",
        "tenant_budget_amount IS NULL OR tenant_budget_amount >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_organizations_tenant_budget_non_negative",
        "organizations",
        type_="check",
    )
    op.drop_column("organizations", "tenant_budget_period_length_days")
    op.drop_column("organizations", "tenant_budget_period_start_day")
    op.drop_column("organizations", "tenant_budget_period")
    op.drop_column("organizations", "tenant_budget_currency")
    op.drop_column("organizations", "tenant_budget_amount")
