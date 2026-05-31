"""exchange_rates (global FX catalog + global-read RLS) + organizations.display_currency
(Plan 11.1 Fase A, task_11_1_01).

Creates the ``exchange_rates`` table whose ORM shape (columns, indexes,
CHECKs) is defined in ``api_server.db.exchange_rates.ExchangeRate``. The
catalog records, for a currency on a calendar date, how many units of
that currency one USD buys (``rate_vs_usd``). USD is the platform's
canonical cost currency; a tenant's ``display_currency`` is converted on
the fly with the rate of each execution's date.

Tenancy decision (CLAUDE.md principle 1 / 9, mirrors ``model_prices``,
migration 0049): **platform-global, NOT tenant-scoped.** An FX rate is a
property of the market on a date, identical for every tenant — so the
table carries **no ``tenant_id``**. We enforce a read/write split at the
DB layer with a **global-read RLS policy**: RLS ENABLED + FORCED with a
single SELECT-only policy ``USING (true)`` (every authenticated session
reads) and **no write policy at all**, so a NOBYPASSRLS (tenant) session
is denied every INSERT/UPDATE/DELETE while the BYPASSRLS System-Admin /
migrations session (and the daily Celery Beat fetcher, task_11_1_02)
bypasses RLS and writes freely. This is the exact SELECT-only
``global_read`` pattern of ``model_prices_global_read`` (0049) and
``marketplace_listings`` (0041) — a *provable* "reads open to all, writes
System-Admin-only" guarantee that does not rely on application code.

Indexes / constraints (all from the ORM):
  - ``uq_exchange_rates_currency_date`` — UNIQUE ``(currency, as_of_date)``:
    one rate per currency per calendar date (the fetcher upserts).
  - ``ix_exchange_rates_currency_as_of_date`` — the conversion lookup path
    ``(currency, as_of_date)`` (rate ON a date or the most recent prior).
  - ``ck_exchange_rates_rate_positive`` — a rate is strictly positive.
  - ``ck_exchange_rates_currency_not_usd`` — USD is the identity and is
    never stored as a row.

Also adds ``organizations.display_currency`` (NOT NULL, server_default
``'EUR'``) — the per-tenant DISPLAY currency. Cost is always canonical
USD; this is only how a tenant's dashboards show it. The server_default
backfills every existing tenant to EUR without a data migration.

Single head before this migration is ``0061_outlier_alert_rules``; this
is ``0062_exchange_rates``. Fully reversible: ``downgrade`` drops the
policy, disables RLS, drops the table, then drops the column. Proven by
``tests/integration/test_exchange_rates.py`` (up / down to
``0040_sso_email_domains`` / up).

Revision ID: 0062_exchange_rates
Revises: 0061_outlier_alert_rules
Create Date: 2026-05-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0062_exchange_rates"
down_revision: str | Sequence[str] | None = "0061_outlier_alert_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Global-read, no-write RLS — a single SELECT-only policy USING (true) opens
# reads to every authenticated session; the ABSENCE of any write policy means
# a NOBYPASSRLS session is denied every INSERT/UPDATE/DELETE while the
# BYPASSRLS System-Admin / migrations / fetcher session bypasses RLS and
# writes freely. FORCE so the policy applies even to the table owner. Emitted
# as raw SQL one statement at a time (asyncpg refuses multi-statement strings;
# Alembic ops don't model row-level security). Mirrors model_prices (0049).
# ---------------------------------------------------------------------------
_RLS_UP: tuple[str, ...] = (
    "ALTER TABLE exchange_rates ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE exchange_rates FORCE ROW LEVEL SECURITY",
    "CREATE POLICY exchange_rates_global_read ON exchange_rates FOR SELECT USING (true)",
)

_RLS_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS exchange_rates_global_read ON exchange_rates",
    "ALTER TABLE exchange_rates DISABLE ROW LEVEL SECURITY",
)


def upgrade() -> None:
    op.create_table(
        "exchange_rates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # ISO-4217 code, stored uppercase. Never 'USD' (CHECK below).
        sa.Column("currency", sa.String(length=3), nullable=False),
        # Units of `currency` per 1 USD (display = usd * rate_vs_usd).
        sa.Column("rate_vs_usd", sa.Numeric(precision=20, scale=10), nullable=False),
        # The calendar date the rate applies to (source publishing date).
        sa.Column("as_of_date", sa.Date(), nullable=False),
        # Provenance: 'ecb' default | 'manual'. Free-form (FxSource values).
        sa.Column("source", sa.String(length=32), nullable=False, server_default=sa.text("'ecb'")),
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
        sa.PrimaryKeyConstraint("id", name="pk_exchange_rates"),
        # One rate per currency per calendar date.
        sa.UniqueConstraint(
            "currency",
            "as_of_date",
            name="uq_exchange_rates_currency_date",
        ),
        # A rate is strictly positive.
        sa.CheckConstraint("rate_vs_usd > 0", name="ck_exchange_rates_rate_positive"),
        # USD is the identity and must never be stored.
        sa.CheckConstraint("currency <> 'USD'", name="ck_exchange_rates_currency_not_usd"),
    )

    # Conversion lookup path: the rate for a currency ON a date or the most
    # recent PRIOR date — both served by this (currency, as_of_date) index.
    op.create_index(
        "ix_exchange_rates_currency_as_of_date",
        "exchange_rates",
        ["currency", "as_of_date"],
    )

    # RLS last so the table exists.
    for stmt in _RLS_UP:
        op.execute(stmt)

    # Per-tenant DISPLAY currency. NOT NULL with a server_default of 'EUR'
    # so every existing tenant is backfilled without a data migration; cost
    # itself is always canonical USD (this is display-only).
    op.add_column(
        "organizations",
        sa.Column(
            "display_currency",
            sa.String(length=3),
            nullable=False,
            server_default=sa.text("'EUR'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("organizations", "display_currency")
    # RLS first (the policy depends on the table).
    for stmt in _RLS_DOWN:
        op.execute(stmt)
    op.drop_table("exchange_rates")
