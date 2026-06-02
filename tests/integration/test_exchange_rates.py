"""Integration tests for the global ``exchange_rates`` catalog + the FX
conversion service + ``organizations.display_currency`` (Plan 11.1
task_11_1_01).

Brings the schema up to head against the real Postgres and verifies:

  - the ``exchange_rates`` table exists with the columns the ORM
    (``api_server.db.exchange_rates.ExchangeRate``) declares and **without
    a ``tenant_id``** (it is platform-global),
  - RLS is ENABLED with exactly the SELECT-only
    ``exchange_rates_global_read`` policy and no write policy — a tenant
    (NOBYPASSRLS) session reads every row but cannot write
    (``@pytest.mark.cross_tenant``),
  - the conversion service: USD->USD is the identity; a non-USD currency
    uses the rate of the requested DATE; with no row for the date it falls
    back to the most recent PRIOR rate; an unknown/unavailable currency
    raises :class:`UnknownCurrencyError` (or, opt-in, falls back to USD),
  - ``organizations.display_currency`` persists per tenant (default EUR),
  - the migration is reversible: up / down to ``0040_sso_email_domains`` /
    up.

Network is irrelevant here (the fetcher is task_11_1_02); rates are seeded
directly as the BYPASSRLS migrations user, then probed as the NOBYPASSRLS
app_user so the RLS policy is actually exercised.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.exchange_rates import ExchangeRate
from api_server.fx import UnknownCurrencyError, convert_from_usd, select_rate_for_date
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Run the migrations up to head. ``alembic.command.upgrade`` does its
    own ``asyncio.run`` so we keep it in a sync fixture."""
    command.upgrade(alembic_config, "head")


def _app_dsn() -> str:
    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
    )

    return f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


async def _seed_rate(
    dsn: str,
    *,
    currency: str,
    rate: str,
    on: date,
    source: str = "ecb",
) -> UUID:
    """Insert one FX rate row as the BYPASSRLS migrations user."""
    rate_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO exchange_rates"
            " (id, currency, rate_vs_usd, as_of_date, source)"
            " VALUES ($1, $2, $3, $4, $5)",
            rate_id,
            currency,
            Decimal(rate),
            on,
            source,
        )
    finally:
        await conn.close()
    return rate_id


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE exchange_rates RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


# ===========================================================================
# Schema presence
# ===========================================================================
@pytest.mark.asyncio
async def test_table_exists_and_is_global(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'exchange_rates'"
        )
        cols = {r["column_name"]: r for r in rows}
        for col in ("id", "currency", "rate_vs_usd", "as_of_date", "source"):
            assert col in cols, f"column {col!r} missing from exchange_rates"
        # Platform-global: there is NO tenant_id column.
        assert "tenant_id" not in cols
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_unique_currency_date_and_checks(schema_at_head, migrations_pg_dsn: str) -> None:
    await _truncate(migrations_pg_dsn)
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=date(2026, 5, 20))
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Second rate for the same (currency, date) → unique violation.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date)"
                " VALUES ($1, 'EUR', 0.93, $2)",
                uuid4(),
                date(2026, 5, 20),
            )
        # A non-positive rate is rejected.
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date)"
                " VALUES ($1, 'GBP', 0, $2)",
                uuid4(),
                date(2026, 5, 20),
            )
        # USD is the identity and must never be stored.
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date)"
                " VALUES ($1, 'USD', 1.0, $2)",
                uuid4(),
                date(2026, 5, 20),
            )
    finally:
        await conn.close()


# ===========================================================================
# RLS — global-read, System-Admin-only write.
# ===========================================================================
@pytest.mark.asyncio
async def test_rls_enabled_with_global_read_policy(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        flag = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'exchange_rates'"
        )
        assert flag is True, "RLS must be ENABLED on exchange_rates"
        rows = await conn.fetch(
            "SELECT polname, polcmd::text AS polcmd FROM pg_policy"
            " WHERE polrelid = 'exchange_rates'::regclass"
        )
        policies = {r["polname"]: r["polcmd"] for r in rows}
        # Exactly one SELECT-only ('r') global-read policy; no write policy.
        assert policies == {"exchange_rates_global_read": "r"}
    finally:
        await conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_global_read_works_and_tenant_cannot_write(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """A NOBYPASSRLS app_user reads every rate (with no tenant AND with an
    arbitrary tenant set — the catalog is platform-global) but is denied
    every write."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate(migrations_pg_dsn)
    rate_id = await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=date(2026, 5, 20))

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        # No tenant set → visible (global catalog).
        rows = await app_conn.fetch("SELECT id FROM exchange_rates")
        assert [r["id"] for r in rows] == [rate_id]

        # An arbitrary tenant set → still visible (not tenant-scoped).
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(uuid4()))
        rows = await app_conn.fetch("SELECT id FROM exchange_rates")
        assert [r["id"] for r in rows] == [rate_id]

        # INSERT denied (no write policy).
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date)"
                " VALUES ($1, 'GBP', 0.79, $2)",
                uuid4(),
                date(2026, 5, 21),
            )
        # UPDATE / DELETE touch zero rows (the row is invisible to the write).
        await app_conn.execute("UPDATE exchange_rates SET rate_vs_usd = 9 WHERE id = $1", rate_id)
        await app_conn.execute("DELETE FROM exchange_rates WHERE id = $1", rate_id)
    finally:
        await app_conn.close()

    # The BYPASSRLS migrations user confirms the row survived untouched.
    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rate = await mig_conn.fetchval(
            "SELECT rate_vs_usd FROM exchange_rates WHERE id = $1", rate_id
        )
        assert rate is not None, "row must survive the denied tenant DELETE"
        assert Decimal(rate) == Decimal("0.92"), "rate must survive the denied tenant UPDATE"
    finally:
        await mig_conn.close()


# ===========================================================================
# Conversion service — USD identity, day's rate, prior-rate fallback, unknown.
# ===========================================================================
async def _open_app_session(app_database_url: str):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory()


@pytest.mark.asyncio
async def test_usd_to_usd_is_identity(schema_at_head, app_database_url: str) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    engine, session = await _open_app_session(app_database_url)
    try:
        out = await convert_from_usd(session, Decimal("12.345678"), "USD", date(2026, 5, 20))
        # Identity returns the input unchanged (no rounding).
        assert out == Decimal("12.345678")
        # Case-insensitive: lower-case 'usd' is still the identity.
        out2 = await convert_from_usd(session, Decimal("5.00"), "usd", date(2026, 5, 20))
        assert out2 == Decimal("5.00")
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_conversion_uses_the_days_rate(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate(migrations_pg_dsn)
    # Two rates for EUR on two different days; conversion must pick the
    # exact day's rate, not the other.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.90", on=date(2026, 5, 19))
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=date(2026, 5, 20))

    engine, session = await _open_app_session(app_database_url)
    try:
        # 10 USD on the 20th at 0.92 == 9.20 EUR (quantized to cents).
        out = await convert_from_usd(session, Decimal("10"), "EUR", date(2026, 5, 20))
        assert out == Decimal("9.20")
        # On the 19th the rate is 0.90 → 9.00 EUR.
        out19 = await convert_from_usd(session, Decimal("10"), "EUR", date(2026, 5, 19))
        assert out19 == Decimal("9.00")
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_conversion_falls_back_to_most_recent_prior_rate(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """A weekend/holiday date with no published rate uses the most recent
    PRIOR rate, never a later one."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate(migrations_pg_dsn)
    # Rate published Fri the 22nd; none on Sat 23rd / Sun 24th; a later one
    # on Mon 25th must NOT be used for the 23rd.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.91", on=date(2026, 5, 22))
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.95", on=date(2026, 5, 25))

    engine, session = await _open_app_session(app_database_url)
    try:
        # Saturday 23rd → falls back to Friday 22nd's 0.91 → 9.10 EUR.
        out = await convert_from_usd(session, Decimal("10"), "EUR", date(2026, 5, 23))
        assert out == Decimal("9.10")

        # select_rate_for_date returns the prior row, not the later one.
        row = await select_rate_for_date(session, "EUR", date(2026, 5, 23))
        assert isinstance(row, ExchangeRate)
        assert row.as_of_date == date(2026, 5, 22)
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_currency_raises_or_falls_back(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate(migrations_pg_dsn)
    # A EUR rate exists, but only AFTER the requested date — so there is no
    # rate on or before it: unknown/unavailable.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=date(2026, 5, 25))

    engine, session = await _open_app_session(app_database_url)
    try:
        # No rate at all for an unknown code → typed error by default.
        with pytest.raises(UnknownCurrencyError):
            await convert_from_usd(session, Decimal("10"), "ZZZ", date(2026, 5, 20))

        # No rate on/before the date for a known code → typed error too.
        with pytest.raises(UnknownCurrencyError):
            await convert_from_usd(session, Decimal("10"), "EUR", date(2026, 5, 20))

        # Opt-in fallback returns the USD amount unchanged (display -> USD).
        out = await convert_from_usd(
            session, Decimal("10"), "ZZZ", date(2026, 5, 20), fallback_to_usd=True
        )
        assert out == Decimal("10")
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# organizations.display_currency persists per tenant (default EUR).
# ===========================================================================
@pytest.mark.asyncio
async def test_display_currency_persists_per_tenant(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # A tenant created without specifying display_currency defaults EUR.
        tid_default = uuid4()
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org Default', $2)",
            tid_default,
            f"org-default-{tid_default.hex[:8]}",
        )
        cur = await conn.fetchval(
            "SELECT display_currency FROM organizations WHERE id = $1", tid_default
        )
        assert cur == "EUR"

        # A second tenant set to USD persists its own value independently.
        tid_usd = uuid4()
        await conn.execute(
            "INSERT INTO organizations (id, name, slug, display_currency)"
            " VALUES ($1, 'Org USD', $2, 'USD')",
            tid_usd,
            f"org-usd-{tid_usd.hex[:8]}",
        )
        cur_usd = await conn.fetchval(
            "SELECT display_currency FROM organizations WHERE id = $1", tid_usd
        )
        assert cur_usd == "USD"
        # The first tenant is unaffected.
        cur_default_again = await conn.fetchval(
            "SELECT display_currency FROM organizations WHERE id = $1", tid_default
        )
        assert cur_default_again == "EUR"
    finally:
        await conn.close()


# ===========================================================================
# Reversibility — downgrade drops the table + column, upgrade head re-creates.
#
# SYNC (no @pytest.mark.asyncio): alembic.command.* spins its own event loop.
# ===========================================================================
def _table_present(dsn: str) -> bool:
    import asyncio

    async def _go() -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema() AND table_name = 'exchange_rates'"
            )
            return row is not None
        finally:
            await conn.close()

    return asyncio.run(_go())


def _display_currency_column_present(dsn: str) -> bool:
    import asyncio

    async def _go() -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'organizations' AND column_name = 'display_currency'"
            )
            return row is not None
        finally:
            await conn.close()

    return asyncio.run(_go())


def test_downgrade_then_upgrade_recreates(
    schema_at_head, alembic_config, migrations_pg_dsn: str
) -> None:
    # Up at head (fixture) → table + column present.
    assert _table_present(migrations_pg_dsn) is True
    assert _display_currency_column_present(migrations_pg_dsn) is True

    # Downgrade past the whole stack back to the pre-marketplace revision →
    # both gone cleanly.
    command.downgrade(alembic_config, "0040_sso_email_domains")
    assert _table_present(migrations_pg_dsn) is False
    assert _display_currency_column_present(migrations_pg_dsn) is False

    # Re-upgrade → present again (proves the migration is replayable).
    command.upgrade(alembic_config, "head")
    assert _table_present(migrations_pg_dsn) is True
    assert _display_currency_column_present(migrations_pg_dsn) is True
