"""Integration tests for migration 0041 — marketplace_sources / listings
/ installations / audit_entries + RLS (Plan 09 task_09_02).

Brings the schema up to head against the real Postgres and verifies:

  - the four tables exist with the columns the ORM expects,
  - RLS is ENABLED on the three tenant-owned tables (listings,
    installations, audit_entries) and DISABLED on the tenant-agnostic
    ``marketplace_sources`` registry,
  - a row inserted under tenant A is invisible under tenant B for both
    installations and audit_entries (``@pytest.mark.cross_tenant``),
  - the hybrid listings policy hides another tenant's PRIVATE listing
    while still exposing GLOBAL (tenant_id IS NULL) catalog rows to every
    tenant,
  - the migration is reversible: ``downgrade -1`` drops all four tables
    cleanly and ``upgrade head`` re-creates them.

Mirrors ``test_memory_migration.py`` / ``test_kb_migration.py``: seed as
the BYPASSRLS migrations user, then probe as the NOBYPASSRLS app_user so
the policies are actually exercised.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_MARKETPLACE_TABLES = {
    "marketplace_sources",
    "marketplace_listings",
    "marketplace_installations",
    "marketplace_audit_entries",
}
# RLS is on the tenant-owned tables; the sources registry is tenant-agnostic.
_TENANT_OWNED_TABLES = {
    "marketplace_listings",
    "marketplace_installations",
    "marketplace_audit_entries",
}


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Run the migrations up to head. ``alembic.command.upgrade`` does its
    own ``asyncio.run``, which clashes with ``@pytest.mark.asyncio`` tests
    — so we keep it in a sync fixture and let pytest order things."""
    command.upgrade(alembic_config, "head")


async def _seed_two_tenants(dsn: str) -> tuple[UUID, UUID, UUID, UUID]:
    """Two tenants + one user each. Returns (tenant_a, tenant_b, user_a,
    source_id). A single tenant-agnostic source is created to hang the
    listings off."""
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    source_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources,"
            " projects, agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a-mkt",
            tenant_b,
            "Tenant B",
            "tenant-b-mkt",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-mkt",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@mkt.test",
            "h",
            user_b,
            "bob@mkt.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type) VALUES ($1, $2, 'official')",
            source_id,
            "official-catalog",
        )
    finally:
        await conn.close()
    return tenant_a, tenant_b, user_a, source_id


def _app_dsn() -> str:
    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
    )

    return f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"


# ===========================================================================
# Schema presence
# ===========================================================================
@pytest.mark.asyncio
async def test_all_four_tables_exist(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = current_schema()"
            "   AND table_name IN ('marketplace_sources', 'marketplace_listings',"
            "                      'marketplace_installations', 'marketplace_audit_entries')"
        )
        present = {r["table_name"] for r in rows}
        assert present == _MARKETPLACE_TABLES
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_listings_columns_match_orm(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name, is_nullable FROM information_schema.columns"
            " WHERE table_name = 'marketplace_listings'"
        )
        cols = {r["column_name"]: r for r in rows}
        for col in (
            "id",
            "source_id",
            "tenant_id",
            "kind",
            "name",
            "version",
            "description",
            "author",
            "trust_level",
            "manifest",
            "requested_permissions",
            "signature",
            "created_at",
            "updated_at",
            "deleted_at",
        ):
            assert col in cols, f"column {col!r} missing from marketplace_listings"
        # tenant_id is NULLABLE (hybrid: NULL => global catalog listing).
        assert cols["tenant_id"]["is_nullable"] == "YES"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_installations_tenant_id_not_null(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'marketplace_installations' AND column_name = 'tenant_id'"
        )
        assert row is not None
        assert row["is_nullable"] == "NO"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fk_indexes_present(schema_at_head, migrations_pg_dsn: str) -> None:
    """Every FK column carries a supporting index (no unindexed FKs)."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes"
            " WHERE tablename IN ('marketplace_listings', 'marketplace_installations',"
            "                     'marketplace_audit_entries')"
        )
        names = {r["indexname"] for r in rows}
        for ix in (
            "ix_marketplace_listings_source_id",
            "ix_marketplace_installations_listing_id",
            "ix_marketplace_installations_project_id",
            "ix_marketplace_audit_listing",
            "ix_marketplace_audit_installation",
        ):
            assert ix in names, f"FK index {ix!r} missing"
    finally:
        await conn.close()


# ===========================================================================
# RLS enabled / disabled per tenancy decision
# ===========================================================================
@pytest.mark.asyncio
async def test_rls_enabled_on_tenant_owned_tables(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT relname, relrowsecurity FROM pg_class WHERE relname = ANY($1::text[])",
            list(_MARKETPLACE_TABLES),
        )
        flags = {r["relname"]: r["relrowsecurity"] for r in rows}
        for table in _TENANT_OWNED_TABLES:
            assert flags[table] is True, f"RLS must be ENABLED on {table}"
        # The registry is tenant-agnostic — RLS stays OFF (visibility is
        # resolved in the service layer).
        assert flags["marketplace_sources"] is False
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_listings_policies_exist(schema_at_head, migrations_pg_dsn: str) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT polname FROM pg_policy WHERE polrelid = 'marketplace_listings'::regclass"
        )
        names = {r["polname"] for r in rows}
        assert "marketplace_listings_tenant_isolation" in names
        assert "marketplace_listings_global_read" in names
    finally:
        await conn.close()


# ===========================================================================
# L4 — tenant-wide installs (project_id NULL) dedupe via COALESCE in the index.
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_wide_install_dedup_under_nulls(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """Two LIVE tenant-wide installs (project_id NULL) of the SAME listing must
    collide. PostgreSQL treats NULLs as distinct, so the plain index over
    project_id let them slip past (the dedup depended only on the router's racy
    SELECT-then-insert). The COALESCE(project_id, zero-uuid) index makes them
    collide so the DB is the real barrier. A project-scoped install of the same
    listing still does NOT collide (different COALESCE value)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_a, _, _, source_id = await _seed_two_tenants(migrations_pg_dsn)
    listing = uuid4()
    project = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version)"
            " VALUES ($1, $2, $3, 'tool', 'tool-a', '1.0.0')",
            listing,
            source_id,
            tenant_a,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')",
            project,
            tenant_a,
        )
        # First tenant-wide install (project_id NULL) — OK.
        await conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, version, status)"
            " VALUES ($1, $2, $3, '1.0.0', 'enabled')",
            uuid4(),
            tenant_a,
            listing,
        )
        # Second tenant-wide install of the SAME listing — must violate the index.
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO marketplace_installations"
                " (id, tenant_id, listing_id, version, status)"
                " VALUES ($1, $2, $3, '1.0.0', 'enabled')",
                uuid4(),
                tenant_a,
                listing,
            )
        # A project-scoped install of the same listing is allowed (distinct COALESCE).
        await conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, project_id, version, status)"
            " VALUES ($1, $2, $3, $4, '1.0.0', 'enabled')",
            uuid4(),
            tenant_a,
            listing,
            project,
        )
    finally:
        await conn.close()


# ===========================================================================
# Cross-tenant isolation — the heart of the multi-tenancy guarantee.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_installations_invisible_across_tenants(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """A row inserted under tenant A must be invisible under tenant B."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_a, tenant_b, _, source_id = await _seed_two_tenants(migrations_pg_dsn)

    listing_a = uuid4()
    listing_b = uuid4()
    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Two PRIVATE listings (one per tenant) + one install each.
        await mig_conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version)"
            " VALUES ($1, $2, $3, 'tool', 'tool-a', '1.0.0'),"
            "        ($4, $2, $5, 'tool', 'tool-b', '1.0.0')",
            listing_a,
            source_id,
            tenant_a,
            listing_b,
            tenant_b,
        )
        await mig_conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, version, status)"
            " VALUES ($1, $2, $3, '1.0.0', 'enabled'),"
            "        ($4, $5, $6, '1.0.0', 'enabled')",
            uuid4(),
            tenant_a,
            listing_a,
            uuid4(),
            tenant_b,
            listing_b,
        )
    finally:
        await mig_conn.close()

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        # No tenant set → policy denies → zero rows.
        assert await app_conn.fetch("SELECT id FROM marketplace_installations") == []

        # Tenant A sees only its own install.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        rows = await app_conn.fetch("SELECT listing_id FROM marketplace_installations")
        assert [r["listing_id"] for r in rows] == [listing_a]

        # Tenant B sees only its own install — tenant A's is invisible.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
        rows = await app_conn.fetch("SELECT listing_id FROM marketplace_installations")
        assert [r["listing_id"] for r in rows] == [listing_b]
    finally:
        await app_conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_audit_entries_invisible_across_tenants(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_a, tenant_b, _, _ = await _seed_two_tenants(migrations_pg_dsn)

    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await mig_conn.execute(
            "INSERT INTO marketplace_audit_entries (id, tenant_id, actor, action)"
            " VALUES ($1, $2, 'user:alice', 'install'),"
            "        ($3, $4, 'user:bob', 'install')",
            uuid4(),
            tenant_a,
            uuid4(),
            tenant_b,
        )
    finally:
        await mig_conn.close()

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        assert await app_conn.fetch("SELECT id FROM marketplace_audit_entries") == []

        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        rows = await app_conn.fetch("SELECT actor FROM marketplace_audit_entries")
        assert [r["actor"] for r in rows] == ["user:alice"]

        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
        rows = await app_conn.fetch("SELECT actor FROM marketplace_audit_entries")
        assert [r["actor"] for r in rows] == ["user:bob"]
    finally:
        await app_conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_listings_hybrid_private_isolated_global_shared(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """Private listings are tenant-isolated; global (tenant_id IS NULL)
    catalog listings are visible to every tenant."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_a, tenant_b, _, source_id = await _seed_two_tenants(migrations_pg_dsn)

    global_listing = uuid4()
    private_a = uuid4()
    private_b = uuid4()
    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await mig_conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version)"
            " VALUES ($1, $2, NULL, 'skill', 'public-skill', '1.0.0'),"
            "        ($3, $2, $4, 'skill', 'priv-a', '1.0.0'),"
            "        ($5, $2, $6, 'skill', 'priv-b', '1.0.0')",
            global_listing,
            source_id,
            private_a,
            tenant_a,
            private_b,
            tenant_b,
        )
    finally:
        await mig_conn.close()

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        # No tenant set → only the global catalog row is visible.
        rows = await app_conn.fetch("SELECT name FROM marketplace_listings ORDER BY name")
        assert [r["name"] for r in rows] == ["public-skill"]

        # Tenant A sees the global row + its own private one, never B's.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        rows = await app_conn.fetch("SELECT name FROM marketplace_listings ORDER BY name")
        assert [r["name"] for r in rows] == ["priv-a", "public-skill"]

        # Tenant B sees the global row + its own private one, never A's.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_b))
        rows = await app_conn.fetch("SELECT name FROM marketplace_listings ORDER BY name")
        assert [r["name"] for r in rows] == ["priv-b", "public-skill"]
    finally:
        await app_conn.close()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_cannot_write_other_tenants_installation(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    """The WITH CHECK clause must reject an INSERT carrying another
    tenant's id (RLS write isolation, not just read isolation)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    tenant_a, tenant_b, _, source_id = await _seed_two_tenants(migrations_pg_dsn)

    listing_global = uuid4()
    mig_conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await mig_conn.execute(
            "INSERT INTO marketplace_listings (id, source_id, tenant_id, kind, name, version)"
            " VALUES ($1, $2, NULL, 'tool', 'pub', '1.0.0')",
            listing_global,
            source_id,
        )
    finally:
        await mig_conn.close()

    app_conn = await asyncpg.connect(_app_dsn())
    try:
        # Acting as tenant A, try to install a row stamped with tenant B.
        await app_conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(tenant_a))
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await app_conn.execute(
                "INSERT INTO marketplace_installations"
                " (id, tenant_id, listing_id, version) VALUES ($1, $2, $3, '1.0.0')",
                uuid4(),
                tenant_b,  # someone else's tenant — must be rejected by WITH CHECK
                listing_global,
            )
    finally:
        await app_conn.close()


# ===========================================================================
# Reversibility — downgrade -1 drops the tables, upgrade head re-creates.
#
# NOTE: this test is SYNC (no @pytest.mark.asyncio). `alembic.command.*`
# spins up its own event loop via `asyncio.run` inside env.py; calling it
# from inside a running loop raises "asyncio.run() cannot be called from a
# running event loop". So we stay synchronous and drive the asyncpg probe
# through `asyncio.run` in this otherwise loop-free thread.
# ===========================================================================
def _present_tables(dsn: str) -> set[str]:
    import asyncio

    async def _go() -> set[str]:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = current_schema()"
                "   AND table_name = ANY($1::text[])",
                list(_MARKETPLACE_TABLES),
            )
            return {r["table_name"] for r in rows}
        finally:
            await conn.close()

    return asyncio.run(_go())


def test_downgrade_drops_all_tables_then_upgrade_recreates(
    schema_at_head, alembic_config, migrations_pg_dsn: str
) -> None:
    # Up at head (fixture) → all present.
    assert _present_tables(migrations_pg_dsn) == _MARKETPLACE_TABLES

    # Downgrade past the whole marketplace migration stack (0041 + any later
    # marketplace migrations such as 0042 consent / 0043 append-only) back to
    # the pre-marketplace revision → all four tables gone cleanly. Target the
    # revision explicitly rather than "-1" so this stays correct as later
    # phases stack more migrations on top of 0041.
    command.downgrade(alembic_config, "0040_sso_email_domains")
    assert _present_tables(migrations_pg_dsn) == set()

    # Re-upgrade → all present again (proves the migration is replayable).
    command.upgrade(alembic_config, "head")
    assert _present_tables(migrations_pg_dsn) == _MARKETPLACE_TABLES
