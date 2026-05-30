"""executions price snapshot columns — per-call historical billing (Plan 11 task_11_13).

Adds the price-snapshot columns to the existing ``executions`` table (the
platform's "model call" record lives as ``model_call`` step dicts inside
``executions.steps_log``; the per-call snapshot is frozen into each step's
JSONB, and these columns surface the representative roll-up for
dashboards/billing without scanning JSONB).

The snapshot freezes the catalog price that was IN EFFECT when a run's
model calls were recorded — in **canonical USD** — so a later change to
the ``model_prices`` catalog (a System-Admin edit or the daily sync) never
re-prices a historical execution. The columns mirror the LAST priced model
call of the run; the authoritative per-call snapshots live in
``steps_log[*].price_snapshot``.

Columns (all NULLABLE / backfill-safe — pre-task runs and runs with no
priced model call leave them NULL; an UNKNOWN catalog price is recorded as
a NULL cost, never a fabricated 0):

  - ``price_snapshot_at``       TIMESTAMPTZ — when the snapshot was taken.
  - ``price_snapshot_currency`` CHAR(3) — always 'USD' (catalog is USD-only).
  - ``price_input_usd``         NUMERIC(18,10) — input unit price in effect.
  - ``price_output_usd``        NUMERIC(18,10) — output unit price in effect.
  - ``price_cached_input_usd``  NUMERIC(18,10) — cache-read (prompt-caching)
                                price in effect; NULL when the model does
                                not price cache reads separately.
  - ``price_snapshot_cost_usd`` NUMERIC(14,6) — the computed billable cost
                                of the (representative) call, USD.

Tenancy: ``executions`` is tenant-scoped with an existing FORCE RLS
tenant-isolation policy (migration 0010). Adding columns does not touch
RLS — the snapshot columns inherit the executions policy untouched. The
catalog read that fills them (``model_prices``, platform-global global-read
RLS, migration 0049) happens in application code, not here.

Fully reversible: ``downgrade`` drops exactly the six columns. No data
loss for unrelated columns; proven by ``tests/integration/test_price_snapshot.py``.

Revision ID: 0050_execution_price_snapshot
Revises: 0049_model_prices
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0050_execution_price_snapshot"
down_revision: str | Sequence[str] | None = "0049_model_prices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("price_snapshot_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("price_snapshot_currency", sa.String(length=3), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("price_input_usd", sa.Numeric(precision=18, scale=10), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("price_output_usd", sa.Numeric(precision=18, scale=10), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("price_cached_input_usd", sa.Numeric(precision=18, scale=10), nullable=True),
    )
    op.add_column(
        "executions",
        sa.Column("price_snapshot_cost_usd", sa.Numeric(precision=14, scale=6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "price_snapshot_cost_usd")
    op.drop_column("executions", "price_cached_input_usd")
    op.drop_column("executions", "price_output_usd")
    op.drop_column("executions", "price_input_usd")
    op.drop_column("executions", "price_snapshot_currency")
    op.drop_column("executions", "price_snapshot_at")
