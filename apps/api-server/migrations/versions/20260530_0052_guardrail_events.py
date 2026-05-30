"""guardrail_events — tenant-scoped, append-only guardrail log + RLS (Plan 11 task_11_20).

Creates the ``guardrail_events`` table whose ORM shape (columns, indexes)
is defined in ``api_server.db.guardrail_event``. One append-only row is
written every time a guardrail *triggers* at any of the four hook points
(``pre_llm`` / ``post_llm`` / ``pre_tool`` / ``post_tool``). It is the
substrate behind the tenant guardrails dashboard (task_11_20) and the
configurable alerts (task_11_21).

Tenancy decision (CLAUDE.md principle 1): **tenant-owned** —
``tenant_id`` NOT NULL + the canonical FOR ALL tenant-isolation RLS
policy (the same NULLIF + ::uuid cast shape copied from migrations
0001 / 0041 / 0045), so a tenant sees / writes ONLY its own events. There
is no platform / NULL-tenant branch: every event is attributed to the
tenant whose work tripped the guardrail. Append-only / immutable: the row
has only ``created_at`` (no ``updated_at`` / ``deleted_at``).

The redacted-detail invariant (NEVER persist the raw secret / PII) lives
in the recorder service (``api_server.guardrails.events``), not the DDL —
the DB just stores the masked summary the service builds.

Fully reversible: ``downgrade`` drops the policy, disables RLS, then drops
the table. Proven by ``tests/integration/test_guardrail_events.py`` and an
up / down to 0040_sso_email_domains / up migration cycle.

Revision ID: 0052_guardrail_events
Revises: 0051_price_sync_audit
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0052_guardrail_events"
down_revision: str | Sequence[str] | None = "0051_price_sync_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Tenant-isolation RLS — canonical FOR ALL policy (copied verbatim from the
# notification_logs / marketplace pattern). The NULLIF(..., '') guard turns
# the empty string an unset GUC returns into NULL before the ::uuid cast, so
# an unset session deterministically matches zero rows (safe default). FORCE
# so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE guardrail_events ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE guardrail_events FORCE ROW LEVEL SECURITY",
    "CREATE POLICY guardrail_events_tenant_isolation ON guardrail_events FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS guardrail_events_tenant_isolation ON guardrail_events",
    "ALTER TABLE guardrail_events DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "guardrail_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # --- what fired ---------------------------------------------------
        sa.Column("guardrail_type", sa.String(length=64), nullable=False),
        sa.Column("hook_point", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=True),
        # --- where it fired (refs, all nullable, no FK so the event
        #     survives the referenced row's deletion) -----------------------
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("execution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_label", sa.String(length=160), nullable=True),
        # --- redacted detail (NEVER the raw secret / PII) -----------------
        sa.Column("detail", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "detail_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # --- when ---------------------------------------------------------
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_guardrail_events"),
    )

    # The plain tenant_id index TenantScopedMixin declares (index=True).
    op.create_index("ix_guardrail_events_tenant_id", "guardrail_events", ["tenant_id"])
    # Dashboard: a tenant's recent events, newest-first.
    op.create_index(
        "ix_guardrail_events_tenant_created",
        "guardrail_events",
        ["tenant_id", "created_at"],
    )
    # Counts-by-type-over-time + the `type` list filter.
    op.create_index(
        "ix_guardrail_events_tenant_type_created",
        "guardrail_events",
        ["tenant_id", "guardrail_type", "created_at"],
    )
    # Counts-by-severity-over-time + the `severity` list filter.
    op.create_index(
        "ix_guardrail_events_tenant_severity_created",
        "guardrail_events",
        ["tenant_id", "severity", "created_at"],
    )

    # RLS last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("guardrail_events")
