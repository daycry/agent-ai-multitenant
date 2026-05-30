"""guardrail_alert_rules — tenant-scoped configurable alert rules + RLS (Plan 11 task_11_21).

Creates the ``guardrail_alert_rules`` table whose ORM shape (columns,
indexes, CHECKs) is defined in ``api_server.db.guardrail_alert_rule``. One
row is a Tenant Admin's configurable rule: "alert me when guardrail
violations cross THRESHOLD within WINDOW_SECONDS" (optionally scoped to a
``guardrail_type`` and/or a ``min_severity``). The evaluator counts
matching ``guardrail_events`` (task_11_20) and fires ONE alert per rule per
window through the Plan 10 notifier (debounced by ``last_fired_at``).

Tenancy decision (CLAUDE.md principle 1): **tenant-owned** —
``tenant_id`` NOT NULL + the canonical FOR ALL tenant-isolation RLS policy
(the same NULLIF + ::uuid cast shape copied from migrations 0001 / 0045 /
0052), so a tenant manages / is alerted on ONLY its own rules. There is no
platform / NULL-tenant branch. Tenant A's violations can NEVER alert
tenant B.

Single head before this migration is ``0052_guardrail_events``; this is
``0053_guardrail_alert_rules``. Fully reversible: ``downgrade`` drops the
policy, disables RLS, then drops the table. Proven by an up / down to
0040_sso_email_domains / up cycle.

Revision ID: 0053_guardrail_alert_rules
Revises: 0052_guardrail_events
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053_guardrail_alert_rules"
down_revision: str | Sequence[str] | None = "0052_guardrail_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# notification_preferences / guardrail_events pattern). The NULLIF(..., '')
# guard turns the empty string an unset GUC returns into NULL before the
# ::uuid cast, so an unset session deterministically matches zero rows (safe
# default). FORCE so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE guardrail_alert_rules ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE guardrail_alert_rules FORCE ROW LEVEL SECURITY",
    "CREATE POLICY guardrail_alert_rules_tenant_isolation ON guardrail_alert_rules FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS guardrail_alert_rules_tenant_isolation ON guardrail_alert_rules",
    "ALTER TABLE guardrail_alert_rules DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "guardrail_alert_rules",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # --- human-facing identity ----------------------------------------
        sa.Column("name", sa.String(length=160), nullable=False),
        # --- the configurable trigger -------------------------------------
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        # --- optional scoping ---------------------------------------------
        sa.Column("guardrail_type", sa.String(length=64), nullable=True),
        sa.Column("min_severity", sa.String(length=16), nullable=True),
        # --- lifecycle ----------------------------------------------------
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_alert_rules"),
        # threshold / window must be sane (DB contract on top of API 422).
        sa.CheckConstraint(
            "threshold >= 1",
            name="ck_guardrail_alert_rules_threshold_positive",
        ),
        sa.CheckConstraint(
            "window_seconds >= 1",
            name="ck_guardrail_alert_rules_window_positive",
        ),
    )

    # The plain tenant_id index TenantScopedMixin declares (index=True).
    op.create_index(
        "ix_guardrail_alert_rules_tenant_id",
        "guardrail_alert_rules",
        ["tenant_id"],
    )
    # Evaluator lookup: a tenant's enabled, live rules.
    op.create_index(
        "ix_guardrail_alert_rules_tenant_enabled",
        "guardrail_alert_rules",
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
    op.drop_table("guardrail_alert_rules")
