"""human_agent_config table + RLS (Plan 16 Fase A, task_16_02).

The human-specific configuration of an ``agent_type='human'`` Agent. Plan 16
Decisiones Clave: ``agent_type`` extends the EXISTING Agent entity rather than
introducing a separate one, so the columns that are meaningless for an AI agent
(who the human is, their rate, how to reach them, the acceptance timeout /
escalation target) live in this 1:1 side table instead of widening ``agents``
with a dozen always-NULL-for-AI columns. The ``agent_id`` FK is UNIQUE so the
relationship is strictly one row per human agent.

Columns (from the task block):

  - ``agent_id``                      FK agents (CASCADE) — the human agent; UNIQUE.
  - ``assignment_mode``               TEXT default 'specific_user'. MVP-only:
                                      ``ck_human_agent_config_assignment_mode``
                                      CHECKs ``= 'specific_user'``. The
                                      :class:`AssignmentMode` enum models the
                                      future role_queue/team_pool modes but the
                                      DB rejects them this plan.
  - ``assigned_user_id``              FK users (SET NULL) — the concrete User.
  - ``hourly_rate`` / ``..._currency`` Numeric(10,2) + ISO-4217 (mirrors
                                      organizations.hourly_rate, migration 0019).
  - ``notification_channels``         JSONB list, default '[]'.
  - ``acceptance_timeout_hours``      INT NOT NULL DEFAULT 24 (Decisiones Clave).
  - ``escalation_target_user_id``     FK users (SET NULL).
  - ``expected_response_time_hours``  INT nullable (planning estimate).
  - ``expected_execution_time_hours`` INT nullable (planning estimate).

Tenant-owned: ``tenant_id`` NOT NULL + RLS. The RLS DDL copies the EXACT shape
the ``agents`` table uses (migration 0002): ``ENABLE`` + ``FORCE`` ROW LEVEL
SECURITY, then a ``{table}_tenant_isolation`` FOR ALL policy with
``tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid`` — the
NULLIF guard turns the empty string an unset GUC returns into NULL before the
``::uuid`` cast, so an unset session deterministically matches zero tenant rows
(safe default). ``assigned_user_id`` being intrinsically a tenant concept is why
this table is never global (Plan 16 Decisiones Clave: global templates fork to
the tenant before naming a User).

Single head before this migration is ``0066_agent_type_check``; this is
``0067_human_agent_config``. Fully reversible: ``downgrade`` drops the policy
then the table, restoring 0066 exactly. The plan-wide reversibility proof
target is ``0040_sso_email_domains``.

Revision ID: 0067_human_agent_config
Revises: 0066_agent_type_check
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0067_human_agent_config"
down_revision: str | Sequence[str] | None = "0066_agent_type_check"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# RLS DDL — sent one statement at a time (asyncpg refuses multi-statement
# strings). Mirrors the agents tenant-isolation shape verbatim (migration
# 0002): ENABLE + FORCE + a FOR ALL USING policy with the NULLIF + ::uuid cast.
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE human_agent_config ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE human_agent_config FORCE ROW LEVEL SECURITY",
    "CREATE POLICY human_agent_config_tenant_isolation ON human_agent_config FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS human_agent_config_tenant_isolation ON human_agent_config",
    "ALTER TABLE human_agent_config DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "human_agent_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "assignment_mode",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'specific_user'"),
        ),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("hourly_rate", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column("hourly_rate_currency", sa.String(length=3), nullable=True),
        sa.Column(
            "notification_channels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "acceptance_timeout_hours",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("24"),
        ),
        sa.Column("escalation_target_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expected_response_time_hours", sa.Integer(), nullable=True),
        sa.Column("expected_execution_time_hours", sa.Integer(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_human_agent_config_agent",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_user_id"],
            ["users.id"],
            name="fk_human_agent_config_assigned_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["escalation_target_user_id"],
            ["users.id"],
            name="fk_human_agent_config_escalation_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_human_agent_config"),
        # 1:1 with the human agent — at most one config row per agent.
        sa.UniqueConstraint("agent_id", name="uq_human_agent_config_agent"),
        # MVP: only specific_user is allowed (Plan 16 Decisiones Clave).
        sa.CheckConstraint(
            "assignment_mode = 'specific_user'",
            name="ck_human_agent_config_assignment_mode",
        ),
        sa.CheckConstraint(
            "hourly_rate IS NULL OR hourly_rate >= 0",
            name="ck_human_agent_config_hourly_rate_non_negative",
        ),
        sa.CheckConstraint(
            "acceptance_timeout_hours > 0",
            name="ck_human_agent_config_acceptance_timeout_positive",
        ),
    )
    op.create_index(
        "ix_human_agent_config_tenant_id",
        "human_agent_config",
        ["tenant_id"],
    )
    op.create_index(
        "ix_human_agent_config_assigned_user",
        "human_agent_config",
        ["assigned_user_id"],
        postgresql_where=sa.text("assigned_user_id IS NOT NULL"),
    )

    # RLS — applied last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)

    op.drop_index("ix_human_agent_config_assigned_user", table_name="human_agent_config")
    op.drop_index("ix_human_agent_config_tenant_id", table_name="human_agent_config")
    op.drop_table("human_agent_config")
