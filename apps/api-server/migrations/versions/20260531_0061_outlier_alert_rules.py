"""outlier_alert_rules — tenant-configurable agent-outlier alert rules + RLS (Plan 14 task_14_13).

Creates the ``outlier_alert_rules`` table whose ORM shape (columns, indexes,
CHECKs) is defined in ``api_server.db.outlier_alert_rule``. One row is a Tenant
Admin's configurable rule: "alert me when an agent's success rate drops below a
FLOOR" or "when an agent's cost / latency is more than K standard deviations
ABOVE the tenant mean", over a trailing ``window_days``. The evaluator
(``api_server.stats.outliers``) aggregates the tenant's ``executions`` per agent
and fires ONE alert per breaching agent per window through the Plan 10 notifier
(debounced by ``last_fired_at``), mirroring the guardrail-alert / drift-alert
pattern.

Tenancy decision (CLAUDE.md principle 1): **tenant-owned** — ``tenant_id`` NOT
NULL + the canonical FOR ALL tenant-isolation RLS policy (the same NULLIF +
``::uuid`` cast shape copied verbatim from migrations 0053_guardrail_alert_rules
/ 0060_eval_drift_state), so a tenant manages / is alerted on ONLY its own rules
and its own agents' statistics. There is no platform / NULL-tenant branch. The
outlier stats themselves are tenant-scoped; cross-tenant comparison is the
separate System-Admin-only task_14_15. Tenant A's agents can NEVER alert
tenant B.

Single head before this migration is ``0060_eval_drift_state``; this is
``0061_outlier_alert_rules``. Fully reversible: ``downgrade`` drops the policy,
disables RLS, then drops the table. Proven by an up / down to
``0040_sso_email_domains`` / up cycle.

Revision ID: 0061_outlier_alert_rules
Revises: 0060_eval_drift_state
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0061_outlier_alert_rules"
down_revision: str | Sequence[str] | None = "0060_eval_drift_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# guardrail_alert_rules / eval_drift_state pattern). The NULLIF(..., '') guard
# turns the empty string an unset GUC returns into NULL before the ::uuid cast,
# so an unset session deterministically matches zero rows (safe default). FORCE
# so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE outlier_alert_rules ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE outlier_alert_rules FORCE ROW LEVEL SECURITY",
    "CREATE POLICY outlier_alert_rules_tenant_isolation ON outlier_alert_rules FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS outlier_alert_rules_tenant_isolation ON outlier_alert_rules",
    "ALTER TABLE outlier_alert_rules DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "outlier_alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # --- human-facing identity ----------------------------------------
        sa.Column("name", sa.String(length=160), nullable=False),
        # --- the configurable trigger -------------------------------------
        sa.Column("metric", sa.String(length=16), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("min_runs", sa.Integer(), nullable=False, server_default=sa.text("5")),
        # --- thresholds (exactly one set, per the metric) -----------------
        sa.Column("success_rate_floor", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("stddev_k", sa.Numeric(precision=5, scale=2), nullable=True),
        # --- lifecycle ----------------------------------------------------
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_fired_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
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
        sa.Column("deleted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outlier_alert_rules"),
        # window / min_runs must be sane (DB contract on top of API 422).
        sa.CheckConstraint(
            "window_days >= 1",
            name="ck_outlier_alert_rules_window_positive",
        ),
        sa.CheckConstraint(
            "min_runs >= 1",
            name="ck_outlier_alert_rules_min_runs_positive",
        ),
        sa.CheckConstraint(
            "success_rate_floor IS NULL "
            "OR (success_rate_floor >= 0 AND success_rate_floor <= 1)",
            name="ck_outlier_alert_rules_floor_unit_range",
        ),
        sa.CheckConstraint(
            "stddev_k IS NULL OR stddev_k >= 0",
            name="ck_outlier_alert_rules_stddev_k_non_negative",
        ),
        # The metric ↔ threshold pairing (see the ORM docstring).
        sa.CheckConstraint(
            "(metric = 'success_rate' AND success_rate_floor IS NOT NULL "
            "AND stddev_k IS NULL) "
            "OR (metric IN ('cost', 'latency') AND stddev_k IS NOT NULL "
            "AND success_rate_floor IS NULL)",
            name="ck_outlier_alert_rules_metric_threshold_pairing",
        ),
    )

    # The plain tenant_id index TenantScopedMixin declares (index=True).
    op.create_index(
        "ix_outlier_alert_rules_tenant_id",
        "outlier_alert_rules",
        ["tenant_id"],
    )
    # Evaluator lookup: a tenant's enabled, live rules.
    op.create_index(
        "ix_outlier_alert_rules_tenant_enabled",
        "outlier_alert_rules",
        ["tenant_id", "enabled"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # RLS last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("outlier_alert_rules")
