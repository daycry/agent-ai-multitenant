"""budget_alert_states — per-threshold budget-alert debounce + RLS (Plan 11.1 task_11_1_05).

Creates the ``budget_alert_states`` table whose ORM shape (columns, indexes,
CHECKs) is defined in ``api_server.db.budget_alert_state``. One row records
that the budget consumption evaluator (``api_server.budgets.consumption``)
already fired a given percentage threshold (e.g. 80 / 90 / 100) for a
``(scope, project_id, period_start)`` budget window — so the debounce is "one
alert per threshold per period per scope": a sustained breach (or a later
re-evaluation in the same period) never re-fires a threshold already raised.
A NEW period starts with a clean slate because ``period_start`` is part of the
debounce key.

Tenancy decision (CLAUDE.md principle 1): **tenant-owned** — ``tenant_id`` NOT
NULL + the canonical FOR ALL tenant-isolation RLS policy (the same NULLIF +
``::uuid`` cast shape copied verbatim from migrations 0061_outlier_alert_rules
/ 0053_guardrail_alert_rules), so a tenant's spend can NEVER fire / debounce a
tenant-B alert. There is no platform / NULL-tenant branch.

Single head before this migration is ``0063_organization_budget``; this is
``0064_budget_alert_states``. Fully reversible: ``downgrade`` drops the policy,
disables RLS, then drops the table — restoring 0063 exactly. The plan-wide
reversibility proof target is ``0040_sso_email_domains``.

Revision ID: 0064_budget_alert_states
Revises: 0063_organization_budget
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0064_budget_alert_states"
down_revision: str | Sequence[str] | None = "0063_organization_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# outlier_alert_rules / guardrail_alert_rules pattern). The NULLIF(..., '')
# guard turns the empty string an unset GUC returns into NULL before the ::uuid
# cast, so an unset session deterministically matches zero rows (safe default).
# FORCE so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE budget_alert_states ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE budget_alert_states FORCE ROW LEVEL SECURITY",
    "CREATE POLICY budget_alert_states_tenant_isolation ON budget_alert_states FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS budget_alert_states_tenant_isolation ON budget_alert_states",
    "ALTER TABLE budget_alert_states DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "budget_alert_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # --- the debounce key fields --------------------------------------
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        # --- timestamps ---------------------------------------------------
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
        sa.PrimaryKeyConstraint("id", name="pk_budget_alert_states"),
        # At most one fired-row per debounce tuple — the hard idempotence
        # guarantee on top of the evaluator's check-then-insert.
        sa.UniqueConstraint(
            "tenant_id",
            "scope",
            "project_id",
            "period_start",
            "threshold",
            name="uq_budget_alert_states_debounce",
        ),
        # A threshold is a positive percentage of the budget.
        sa.CheckConstraint(
            "threshold >= 1",
            name="ck_budget_alert_states_threshold_positive",
        ),
        # The project scope carries a project_id; the tenant scope does not.
        sa.CheckConstraint(
            "(scope = 'tenant' AND project_id IS NULL) "
            "OR (scope = 'project' AND project_id IS NOT NULL)",
            name="ck_budget_alert_states_scope_project_pairing",
        ),
    )

    # The plain tenant_id index TenantScopedMixin declares (index=True).
    op.create_index(
        "ix_budget_alert_states_tenant_id",
        "budget_alert_states",
        ["tenant_id"],
    )
    # Evaluator lookup: the fired thresholds for a scope's current period.
    op.create_index(
        "ix_budget_alert_states_lookup",
        "budget_alert_states",
        ["tenant_id", "scope", "project_id", "period_start"],
    )

    # RLS last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("budget_alert_states")
