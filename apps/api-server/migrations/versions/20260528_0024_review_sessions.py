"""review_sessions table (Plan 06.5 task_06_5_01).

Persistence for the `ReviewRuntimeManager` (Plan 06 Fase G). The
manager was already in-memory: spawns / suspends / expires were
tracked in a Python dict per tenant. That works for the demo
`plan_runner` but loses state on worker restart — Celery is going to
move the orchestration into worker processes (Plan 06.5 Fase C) and
we need durability.

Each row is one review-runtime session. The Python dataclass
``workers.review_runtime.ReviewSession`` shape maps 1:1, with the
status enum reflecting transitions in the manager:

    running    initial state — container(s) up, URL active
    suspended  idle for > 24h — containers paused but kept (mgr.suspend_idle)
    approved   human verdict approved → terminal
    rejected   human verdict rejected → terminal
    expired    > 48h since created → terminal (mgr.expire_overdue)
    cancelled  manually cancelled by tenant_admin → terminal

The signed URL the human visits derives from a tenant-level HMAC key
(not per-session) — see `review_runtime.sign_review_url` — so we do
NOT store a per-row secret here.

Revision ID: 0024_review_sessions
Revises: 0023_tenant_settings
Create Date: 2026-05-28
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024_review_sessions"
down_revision: str | Sequence[str] | None = "0023_tenant_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        # The serialized ReviewRuntimeSpec — image, env, mounts, etc.
        # Stored as JSONB so the manager can rehydrate the session
        # after a worker restart without going through the DB schema.
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        # The compose project may spawn N containers (postgres-test,
        # redis-test, the review-runtime itself…) — store the docker ids
        # so we can `docker stop` them later without re-discovering.
        sa.Column(
            "container_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column(
            "rerun_requested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_activity_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("suspended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["organizations.id"],
            name="fk_review_sessions_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["plans.id"],
            name="fk_review_sessions_plan",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_sessions"),
        sa.CheckConstraint(
            "status IN ('running', 'suspended', 'approved', 'rejected', 'expired', 'cancelled')",
            name="ck_review_sessions_status",
        ),
        sa.CheckConstraint(
            "verdict IS NULL OR verdict IN ('approved', 'rejected')",
            name="ck_review_sessions_verdict",
        ),
    )

    # Indexes — drive the four hot queries:
    #   1. by tenant (most listings)
    #   2. expire_overdue scan: expires_at < now() AND status='running'
    #   3. suspend_idle scan: last_activity_at < now()-24h AND status='running'
    #   4. sessions of a plan (the /plans/{id}/review endpoint)
    op.create_index(
        "ix_review_sessions_tenant_id",
        "review_sessions",
        ["tenant_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_review_sessions_running_by_expiry",
        "review_sessions",
        ["expires_at"],
        postgresql_where=sa.text("status = 'running' AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_review_sessions_running_by_activity",
        "review_sessions",
        ["last_activity_at"],
        postgresql_where=sa.text("status = 'running' AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_review_sessions_plan_id",
        "review_sessions",
        ["plan_id"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # RLS — same pattern as the rest of the tenant-scoped tables.
    op.execute("ALTER TABLE review_sessions ENABLE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON review_sessions "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true));"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON review_sessions;")
    op.drop_index("ix_review_sessions_plan_id", table_name="review_sessions")
    op.drop_index("ix_review_sessions_running_by_activity", table_name="review_sessions")
    op.drop_index("ix_review_sessions_running_by_expiry", table_name="review_sessions")
    op.drop_index("ix_review_sessions_tenant_id", table_name="review_sessions")
    op.drop_table("review_sessions")
