"""eval_drift_state — the Plan 14 task_14_10 drift-alert debounce table + RLS.

The drift detector (``api_server.evals.drift``) watches a tenant's trailing
eval/shadow-run pass rates for a SUSTAINED decline (not a one-off dip). When
drift is declared it fires ONE alert through the Plan 10 notifier to the
tenant's Tenant Admins and stamps ``last_alerted_at`` here so a still-declining
stream does not spam — the same debounce shape as
``guardrail_alert_rules.last_fired_at`` (Plan 11). This single tenant-owned
table mirrors :class:`api_server.db.evals.EvalDriftState` 1:1:

  * one row per ``(tenant_id, dataset_id)`` (the benchmark stream being watched);
    the UNIQUE index on the pair is the natural key + the lookup index.
  * ``dataset_id`` → ``eval_datasets.id`` ``ON DELETE CASCADE`` (the state is
    meaningless once its dataset is gone).
  * ``last_alerted_at`` — NULL until the first alert; the debounce anchor.

Tenancy decision (CLAUDE.md principle 1): tenant-owned — ``tenant_id`` NOT NULL
+ the canonical FOR ALL tenant-isolation RLS policy (the NULLIF + ``::uuid``
cast shape copied verbatim from 0058_eval_tables / 0059_eval_shadow_records), so
a tenant's drift state is visible only to that tenant.

Single head before this migration is ``0059_eval_shadow_records``; this is
``0060_eval_drift_state``. Fully reversible: ``downgrade`` drops the policy,
disables RLS, then drops the table. Proven by an up / down to
``0040_sso_email_domains`` / up cycle.

Revision ID: 0060_eval_drift_state
Revises: 0059_eval_shadow_records
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0060_eval_drift_state"
down_revision: str | Sequence[str] | None = "0059_eval_shadow_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# 0058_eval_tables / 0059_eval_shadow_records pattern). NULLIF(..., '') turns an
# unset GUC's empty string into NULL before the ::uuid cast, so an unset session
# matches zero rows (safe default). FORCE so the policy applies to the owner too.
# ---------------------------------------------------------------------------
def _rls_up(table: str) -> tuple[str, ...]:
    return (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    )


def _rls_down(table: str) -> tuple[str, ...]:
    return (
        f"DROP POLICY IF EXISTS tenant_isolation ON {table}",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    )


def _ts_col(name: str, *, nullable: bool, default_now: bool = False) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        postgresql.TIMESTAMP(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()") if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "eval_drift_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        _ts_col("last_alerted_at", nullable=True),
        _ts_col("created_at", nullable=False, default_now=True),
        _ts_col("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name="pk_eval_drift_state"),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["eval_datasets.id"],
            name="fk_eval_drift_state_dataset",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_eval_drift_state_tenant_id", "eval_drift_state", ["tenant_id"])
    op.create_index(
        "uq_eval_drift_state_tenant_dataset",
        "eval_drift_state",
        ["tenant_id", "dataset_id"],
        unique=True,
    )

    for stmt in _rls_up("eval_drift_state"):
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _rls_down("eval_drift_state"):
        op.execute(stmt)

    op.drop_index("uq_eval_drift_state_tenant_dataset", table_name="eval_drift_state")
    op.drop_index("ix_eval_drift_state_tenant_id", table_name="eval_drift_state")
    op.drop_table("eval_drift_state")
