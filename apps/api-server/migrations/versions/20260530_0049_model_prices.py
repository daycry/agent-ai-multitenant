"""model_prices — platform-global LLM price catalog + global-read RLS (Plan 11 task_11_11).

Creates the ``model_prices`` table whose ORM shape (columns, enums,
indexes, constraints) was defined in task_11_10
(``api_server.db.model_prices``). The catalog records what a given LLM
model costs per token, in **canonical USD**, with effective dating so a
model can have successive priced periods (historical correctness).

Tenancy decision (ADR 0028 + CLAUDE.md principle 1 / 9):
**platform-global, NOT tenant-scoped.** A price is a property of the
*provider's* pricing, identical for every tenant, so the table carries
**no ``tenant_id``**. Writes are reserved for the **System Admin** (the
BYPASSRLS ``get_admin_session``); reads are open to any authenticated
caller.

We enforce that split at the DB layer with a **global-read RLS policy**
(rather than leaving the table un-secured and trusting only the endpoint
RBAC). RLS is ENABLED + FORCED with a single SELECT-only policy
``USING (true)`` — every authenticated session may read every row — and
**no INSERT/UPDATE/DELETE policy at all**. Under RLS, a write needs a
permissive policy whose ``WITH CHECK`` passes; with no write policy a
NOBYPASSRLS session (``app_user`` / a tenant session) is denied every
write, while the BYPASSRLS System-Admin session bypasses RLS entirely
and writes freely. This mirrors the SELECT-only ``global_read`` pattern
used for ``marketplace_listings`` (migration 0041) and the
``agents_global_builtin_read`` policy (migration 0004), and gives a
*provable* "reads open to all, writes System-Admin-only" guarantee that
does not depend solely on application code.

Indexes (all from the ORM):
  - ``uq_model_prices_current`` — partial UNIQUE on
    ``(provider, model_id, modality)`` WHERE ``effective_to IS NULL``:
    at most one open (current) priced period per key, so "the current
    price" is well-defined without scanning dates.
  - ``ix_model_prices_provider_model_modality_from`` — the browse /
    current-price lookup path ``(provider, model_id, modality,
    effective_from)``.
  - ``uq_model_prices_period_start`` — a closed period must start at a
    distinct instant for a key.

CHECK constraints: non-negative prices, a valid (open or forward)
interval, a positive context window, and ``currency = 'USD'`` (the
catalog is USD-only; the CHECK rejects a stray non-USD write at the DB).

Fully reversible: ``downgrade`` drops the policy, disables RLS, then
drops the table. Proven by ``tests/integration/test_prices_migration.py``
(up / down to 0040_sso_email_domains / up).

Revision ID: 0049_model_prices
Revises: 0048_notification_log_reads
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0049_model_prices"
down_revision: str | Sequence[str] | None = "0048_notification_log_reads"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# RLS DDL emitted as raw SQL — Alembic ops don't model row-level security.
# Statements are sent one at a time (asyncpg refuses multi-statement
# strings).
#
# Global-read, no-write: a single SELECT-only policy USING (true) opens
# reads to every authenticated session; the ABSENCE of any write policy
# means a NOBYPASSRLS session is denied every INSERT/UPDATE/DELETE while
# the BYPASSRLS System-Admin session (get_admin_session) bypasses RLS and
# writes freely. FORCE so the policy applies even to the table owner.
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE model_prices ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE model_prices FORCE ROW LEVEL SECURITY",
    "CREATE POLICY model_prices_global_read ON model_prices FOR SELECT USING (true)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS model_prices_global_read ON model_prices",
    "ALTER TABLE model_prices DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "model_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # --- catalog key ---------------------------------------------------
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=255), nullable=False),
        sa.Column(
            "modality",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'text'"),
        ),
        # --- prices (canonical USD, per `unit` tokens) ---------------------
        sa.Column("input_price", sa.Numeric(precision=18, scale=10), nullable=False),
        sa.Column("output_price", sa.Numeric(precision=18, scale=10), nullable=False),
        # Prompt-caching cache-read price. NULL == model prices cache reads
        # together with input (helper falls back to ~10% of input_price).
        sa.Column("cached_input_price", sa.Numeric(precision=18, scale=10), nullable=True),
        sa.Column(
            "unit",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'per_1m_tokens'"),
        ),
        # Constant "USD" — the catalog is USD-only (CHECK below enforces it).
        sa.Column(
            "currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'USD'"),
        ),
        sa.Column("context_window", sa.Integer(), nullable=True),
        # --- provenance + effective dating ---------------------------------
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column(
            "effective_from",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # NULL == the current (open) priced period.
        sa.Column("effective_to", postgresql.TIMESTAMP(timezone=True), nullable=True),
        # The System Admin who last wrote this row; NULL once deleted (the
        # price outlives the user). No tenant_id: platform-global catalog.
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["updated_by"],
            ["users.id"],
            name="fk_model_prices_updated_by",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_prices"),
        # A closed period must start at a distinct instant for a key; the
        # open-period uniqueness is the partial index below.
        sa.UniqueConstraint(
            "provider",
            "model_id",
            "modality",
            "effective_from",
            name="uq_model_prices_period_start",
        ),
        # An open period (NULL) or a real forward interval.
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_model_prices_period_valid",
        ),
        sa.CheckConstraint("input_price >= 0", name="ck_model_prices_input_non_negative"),
        sa.CheckConstraint("output_price >= 0", name="ck_model_prices_output_non_negative"),
        sa.CheckConstraint(
            "cached_input_price IS NULL OR cached_input_price >= 0",
            name="ck_model_prices_cached_input_non_negative",
        ),
        sa.CheckConstraint(
            "context_window IS NULL OR context_window > 0",
            name="ck_model_prices_context_window_positive",
        ),
        # Defence in depth: the catalog is USD-only.
        sa.CheckConstraint("currency = 'USD'", name="ck_model_prices_currency_usd"),
    )

    # The current price for a key is the single row whose period is open.
    # Enforce uniqueness on the open period only so historical (closed)
    # rows can pile up freely.
    op.create_index(
        "uq_model_prices_current",
        "model_prices",
        ["provider", "model_id", "modality"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    # Browse / current-price lookup path: all priced periods for a model.
    op.create_index(
        "ix_model_prices_provider_model_modality_from",
        "model_prices",
        ["provider", "model_id", "modality", "effective_from"],
    )
    # FK index for the updated_by FK (no unindexed FKs).
    op.create_index(
        "ix_model_prices_updated_by",
        "model_prices",
        ["updated_by"],
        postgresql_where=sa.text("updated_by IS NOT NULL"),
    )

    # RLS last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)


def downgrade() -> None:
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("model_prices")
