"""price_sync_audit — append-only per-sync audit trail + global-read RLS (Plan 11 task_11_19).

Creates the ``price_sync_audit`` table whose ORM shape (columns, indexes)
is defined in ``api_server.db.price_sync_audit``. Every refresh of the
``model_prices`` catalog from the community LiteLLM price feed — manual
(``POST /admin/model-prices/sync`` / ``.../sync/apply``) or scheduled (the
Celery-beat job) — writes one immutable row here: who, when, source, the
counts (created / updated / discontinued / unchanged / skipped), the held
large-increase spikes, and a compact diff.

Tenancy decision (mirrors ``model_prices``, migration 0049): **platform-global,
NOT tenant-scoped** — a price sync is a System-Admin platform action, so the
table carries **no ``tenant_id``**. The read/write split is enforced at the DB
layer:

  - RLS is ENABLED + FORCED with a single SELECT-only policy ``USING (true)``:
    any authenticated session may read the history (the admin screen surfaces
    it) — same global-read story as the catalog itself; and
  - there is **no INSERT/UPDATE/DELETE policy at all**. Under FORCE ROW LEVEL
    SECURITY a command with no matching permissive policy affects zero rows
    for a NOBYPASSRLS role, so a tenant session is denied every write while
    the BYPASSRLS System-Admin / worker session writes freely. The absence of
    UPDATE / DELETE policies *also* makes the log **append-only / immutable**
    for the app role (mirrors ``marketplace_audit_entries`` append-only
    hardening of migration 0043).

Fully reversible: ``downgrade`` drops the policy, disables RLS, then drops the
table. Proven by ``tests/integration/test_sync_audit.py`` and the
up / down to 0040_sso_email_domains / up migration cycle.

Revision ID: 0051_price_sync_audit
Revises: 0050_execution_price_snapshot
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0051_price_sync_audit"
down_revision: str | Sequence[str] | None = "0050_execution_price_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Global-read, no-write RLS — identical pattern to model_prices (0049). A
# single SELECT-only policy USING (true) opens reads to every authenticated
# session; the ABSENCE of any write policy denies a NOBYPASSRLS / tenant
# session every INSERT/UPDATE/DELETE (and makes the log append-only) while
# the BYPASSRLS System-Admin / worker session bypasses RLS and writes freely.
# FORCE so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE price_sync_audit ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE price_sync_audit FORCE ROW LEVEL SECURITY",
    "CREATE POLICY price_sync_audit_global_read ON price_sync_audit FOR SELECT USING (true)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS price_sync_audit_global_read ON price_sync_audit",
    "ALTER TABLE price_sync_audit DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "price_sync_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # --- who ----------------------------------------------------------
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column(
            "source", sa.String(length=16), nullable=False, server_default=sa.text("'litellm'")
        ),
        sa.Column("feed_url", sa.String(length=1024), nullable=True),
        # --- counts -------------------------------------------------------
        sa.Column("fetched", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("updated", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("unchanged", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("discontinued", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "held_large_increases", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # --- compact diff + held spikes -----------------------------------
        sa.Column(
            "diff",
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
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_price_sync_audit_actor_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_price_sync_audit"),
        # Non-negative counts — defence in depth against a bad write.
        sa.CheckConstraint("fetched >= 0", name="ck_price_sync_audit_fetched_non_negative"),
        sa.CheckConstraint("created >= 0", name="ck_price_sync_audit_created_non_negative"),
        sa.CheckConstraint("updated >= 0", name="ck_price_sync_audit_updated_non_negative"),
        sa.CheckConstraint("unchanged >= 0", name="ck_price_sync_audit_unchanged_non_negative"),
        sa.CheckConstraint(
            "discontinued >= 0", name="ck_price_sync_audit_discontinued_non_negative"
        ),
        sa.CheckConstraint("skipped >= 0", name="ck_price_sync_audit_skipped_non_negative"),
        sa.CheckConstraint(
            "held_large_increases >= 0",
            name="ck_price_sync_audit_held_non_negative",
        ),
    )

    op.create_index("ix_price_sync_audit_created", "price_sync_audit", ["created_at"])
    op.create_index(
        "ix_price_sync_audit_trigger_created",
        "price_sync_audit",
        ["trigger", "created_at"],
    )
    op.create_index(
        "ix_price_sync_audit_actor_user",
        "price_sync_audit",
        ["actor_user_id"],
        postgresql_where=sa.text("actor_user_id IS NOT NULL"),
    )

    # RLS last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("price_sync_audit")
