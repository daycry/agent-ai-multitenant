"""notification_log_reads — per-user read/unread for the in-app inbox (Plan 10 task_10_16).

The in-app inbox (task_10_16) lists a Tenant Admin's ``notification_logs``
history with a per-user read/unread marker. ``notification_logs`` is
append-only AND tenant-scoped (it has no per-user dimension): a *read*
marker is inherently per-user (two admins of the same tenant have
independent inboxes), so it CANNOT live on the log row without breaking
both invariants.

This migration adds a thin **per-user read-receipt** table:

  - **``notification_log_reads``** — ``(tenant_id, user_id, log_id, read_at)``.
    A row's existence means "this user has read this log". "Unread" is the
    absence of a row. UNIQUE ``(user_id, log_id)`` makes the mark idempotent.
    FK to ``notification_logs`` (CASCADE) so a (hypothetical) log purge takes
    its receipts with it, and FK to ``users`` (CASCADE) so deleting a user
    drops their receipts. ``tenant_id`` NOT NULL + RLS isolates receipts per
    tenant exactly like every other tenant table — a NULL-tenant platform
    send is an operator concern and is not inboxed per user.

RLS copies the canonical NULLIF + cast tenant-isolation shape from
migration 0045 (the notifications substrate) so an unset session matches
zero rows. Fully reversible: ``downgrade`` drops the policy then the table.

Revision ID: 0048_notification_log_reads
Revises: 0047_personal_assistant_enabled
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0048_notification_log_reads"
down_revision: str | Sequence[str] | None = "0047_personal_assistant_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE notification_log_reads ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE notification_log_reads FORCE ROW LEVEL SECURITY",
    "CREATE POLICY notification_log_reads_tenant_isolation ON notification_log_reads FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS notification_log_reads_tenant_isolation ON notification_log_reads",
    "ALTER TABLE notification_log_reads DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "notification_log_reads",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("log_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "read_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["log_id"],
            ["notification_logs.id"],
            name="fk_notification_log_reads_log",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_notification_log_reads_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_log_reads"),
        # One read-receipt per (user, log) — makes the mark idempotent.
        sa.UniqueConstraint(
            "user_id",
            "log_id",
            name="uq_notification_log_reads_user_log",
        ),
    )
    op.create_index(
        "ix_notification_log_reads_tenant_id",
        "notification_log_reads",
        ["tenant_id"],
    )
    # The inbox left-joins receipts by the calling user; index that path.
    op.create_index(
        "ix_notification_log_reads_user_log",
        "notification_log_reads",
        ["user_id", "log_id"],
    )

    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("notification_log_reads")
