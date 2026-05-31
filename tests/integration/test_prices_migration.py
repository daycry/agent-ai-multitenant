"""Integration tests for migration 0049 — model_prices + global-read RLS
(Plan 11 Fase C, task_11_11).

Brings the schema up to head against the real Postgres and verifies:

  - the ``model_prices`` table exists with the columns the ORM
    (``api_server.db.model_prices.ModelPrice``) declares, and **without
    a ``tenant_id``** (it is platform-global),
  - the catalog's indexes are present: the partial-unique
    ``uq_model_prices_current`` (one open period per key), the browse
    path ``ix_model_prices_provider_model_modality_from``, and the
    ``updated_by`` FK index,
  - RLS is ENABLED with exactly the SELECT-only ``model_prices_global_read``
    policy and no write policy,
  - **global read works** while a **tenant (NOBYPASSRLS) session cannot
    write**: the BYPASSRLS migrations_user seeds rows, the app_user reads
    them all (with and without a tenant set — the catalog is global) but
    every INSERT/UPDATE/DELETE it attempts is denied
    (``@pytest.mark.cross_tenant``),
  - the migration is reversible: ``downgrade`` to the pre-marketplace
    revision drops the table cleanly and ``upgrade head`` re-creates it
    (up / down / up).

Mirrors ``test_marketplace_migration.py``: seed as the BYPASSRLS
migrations user, then probe as the NOBYPASSRLS app_user so the policy is
actually exercised.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Run the migrations up to head. ``alembic.command.upgrade`` does its
    own ``asyncio.run``, which clashes with ``@pytest.mark.asyncio`` tests
    — so we keep it in a sync fixture and let pytest order things."""
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


async def _seed_price(dsn: str) -> UUID:
    """Insert one current (open-period) price row as the BYPASSRLS
    migrations user and return its id."""
    price_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE model_prices RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, modality, input_price, output_price)"
            " VALUES ($1, 'anthropic', 'claude-sonnet-4-5', 'text', 3.0, 15.0)",
            price_id,
        )
    finally:
        await conn.close()
    return price_id


# ===========================================================================
# Schema presence
# ===========================================================================
@pytest.mark.asyncio
async def test_table_exists(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = current_schema() AND table_name = 'model_prices'"
        )
        assert row is not None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_columns_match_orm(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'model_prices'"
        )
        cols = {r["column_name"]: r for r in rows}
        for col in (
            "id",
            "provider",
            "model_id",
            "modality",
            "input_price",
            "output_price",
            "cached_input_price",
            "unit",
            "currency",
            "context_window",
            "source",
            "effective_from",
            "effective_to",
            "updated_by",
            "created_at",
            "updated_at",
        ):
            assert col in cols, f"column {col!r} missing from model_prices"
        # Platform-global: there is NO tenant_id column.
        assert "tenant_id" not in cols
        # cached_input_price is nullable (prompt caching is optional);
        # effective_to is nullable (NULL == current open period).
        assert cols["cached_input_price"]["is_nullable"] == "YES"
        assert cols["effective_to"]["is_nullable"] == "YES"
        # input_price / output_price are NOT NULL.
        assert cols["input_price"]["is_nullable"] == "NO"
        assert cols["output_price"]["is_nullable"] == "NO"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_indexes_present(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename = 'model_prices'")
        names = {r["indexname"] for r in rows}
        for ix in (
            "uq_model_prices_current",
            "ix_model_prices_provider_model_modality_from",
            "ix_model_prices_updated_by",
        ):
            assert ix in names, f"index {ix!r} missing from model_prices"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_current_price_partial_unique_enforced(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """At most one OPEN period per (provider, model_id, modality); a
    second open row for the same key violates uq_model_prices_current,
    but a CLOSED row for the same key is allowed."""
    await _seed_price(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Second open period for the same key → unique violation.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO model_prices"
                " (id, provider, model_id, modality, input_price, output_price)"
                " VALUES ($1, 'anthropic', 'claude-sonnet-4-5', 'text', 4.0, 16.0)",
                uuid4(),
            )
        # A CLOSED period (effective_to set) for the same key is fine.
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, modality, input_price, output_price,"
            "  effective_from, effective_to)"
            " VALUES ($1, 'anthropic', 'claude-sonnet-4-5', 'text', 2.0, 10.0,"
            "  now() - interval '2 days', now() - interval '1 day')",
            uuid4(),
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_currency_usd_check_enforced(schema_at_head, migrations_pg_dsn: str) -> None:
    """The catalog is USD-only — a non-USD currency is rejected by CHECK."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("TRUNCATE model_prices RESTART IDENTITY CASCADE")
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO model_prices"
                " (id, provider, model_id, input_price, output_price, currency)"
                " VALUES ($1, 'openai', 'gpt-4o', 3.0, 10.0, 'EUR')",
                uuid4(),
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
            "SELECT relrowsecurity FROM pg_class WHERE relname = 'model_prices'"
        )
        assert flag is True, "RLS must be ENABLED on model_prices"
        rows = await conn.fetch(
            # polcmd is the internal "char" type ('r' == SELECT); cast to
            # text so asyncpg yields a str rather than a one-byte bytes.
            "SELECT polname, polcmd::text AS polcmd FROM pg_policy"
            " WHERE polrelid = 'model_prices'::regclass"
        )
        policies = {r["polname"]: r["polcmd"] for r in rows}
        # Exactly one policy: a SELECT-only ('r') global read. No write
        # policy → NOBYPASSRLS writes are denied.
        assert policies == {"model_prices_global_read": "r"}
    finally:
        await conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_global_read_works_for_any_session(schema_at_head, migrations_pg_dsn: str) -> None:
    """A NOBYPASSRLS app_user reads every catalog row — with no tenant set
    AND with an arbitrary tenant set (the catalog is platform-global, not
    tenant-scoped, so reads never depend on app.tenant_id)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    price_id = await _seed_price(migrations_pg_dsn)

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        # No tenant set → still visible (global catalog).
        rows = await app_conn.fetch("SELECT id FROM model_prices")
        assert [r["id"] for r in rows] == [price_id]

        # An arbitrary tenant set → still visible (not tenant-scoped).
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(uuid4()))
        rows = await app_conn.fetch("SELECT id FROM model_prices")
        assert [r["id"] for r in rows] == [price_id]
    finally:
        await app_conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_session_cannot_write(schema_at_head, migrations_pg_dsn: str) -> None:
    """Writes are reserved for the BYPASSRLS System-Admin session. A
    NOBYPASSRLS (tenant) session is denied INSERT, UPDATE and DELETE — the
    table has a SELECT-only policy and no write policy."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    price_id = await _seed_price(migrations_pg_dsn)

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(uuid4()))

        # INSERT denied (no write policy → WITH CHECK fails for everyone
        # but BYPASSRLS roles).
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "INSERT INTO model_prices"
                " (id, provider, model_id, input_price, output_price)"
                " VALUES ($1, 'openai', 'gpt-4o', 3.0, 10.0)",
                uuid4(),
            )

        # UPDATE affects zero rows (the USING clause of a write needs a
        # permissive write policy; none exists) — RLS makes the row
        # invisible to the UPDATE, so nothing changes.
        await app_conn.execute("UPDATE model_prices SET input_price = 999 WHERE id = $1", price_id)
        # DELETE likewise touches nothing.
        await app_conn.execute("DELETE FROM model_prices WHERE id = $1", price_id)
    finally:
        await app_conn.close()

    # The BYPASSRLS migrations user confirms the row is untouched.
    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        price = await mig_conn.fetchval(
            "SELECT input_price FROM model_prices WHERE id = $1", price_id
        )
        assert price is not None, "row must survive the denied tenant DELETE"
        assert float(price) == 3.0, "input_price must survive the denied tenant UPDATE"
    finally:
        await mig_conn.close()


@pytest.mark.asyncio
async def test_admin_session_can_write(schema_at_head, migrations_pg_dsn: str) -> None:
    """The BYPASSRLS System-Admin session writes freely (RLS is bypassed)."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("TRUNCATE model_prices RESTART IDENTITY CASCADE")
        new_id = uuid4()
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, input_price, output_price, cached_input_price)"
            " VALUES ($1, 'anthropic', 'claude-opus-4', 5.0, 25.0, 0.5)",
            new_id,
        )
        await conn.execute("UPDATE model_prices SET output_price = 30.0 WHERE id = $1", new_id)
        out = await conn.fetchval("SELECT output_price FROM model_prices WHERE id = $1", new_id)
        assert float(out) == 30.0
    finally:
        await conn.close()


# ===========================================================================
# Reversibility — downgrade drops the table, upgrade head re-creates.
#
# NOTE: SYNC (no @pytest.mark.asyncio). `alembic.command.*` spins up its
# own event loop via `asyncio.run` inside env.py; calling it from inside a
# running loop raises. So we stay synchronous and drive the asyncpg probe
# through `asyncio.run` in this otherwise loop-free thread.
# ===========================================================================
def _table_present(dsn: str) -> bool:
    import asyncio

    async def _go() -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema() AND table_name = 'model_prices'"
            )
            return row is not None
        finally:
            await conn.close()

    return asyncio.run(_go())


def test_downgrade_drops_table_then_upgrade_recreates(
    schema_at_head, alembic_config, migrations_pg_dsn: str
) -> None:
    # Up at head (fixture) → present.
    assert _table_present(migrations_pg_dsn) is True

    # Downgrade past the whole marketplace + notifications + prices stack
    # back to the pre-marketplace revision → model_prices gone cleanly.
    # Target the revision explicitly (not "-1") so this stays correct as
    # later phases stack more migrations on top of 0049.
    command.downgrade(alembic_config, "0040_sso_email_domains")
    assert _table_present(migrations_pg_dsn) is False

    # Re-upgrade → present again (proves the migration is replayable).
    command.upgrade(alembic_config, "head")
    assert _table_present(migrations_pg_dsn) is True
