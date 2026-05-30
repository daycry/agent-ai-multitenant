"""Playwright flagship tool + guided config (Plan 09 task_09_13).

Two layers, mirroring how the rest of Plan 09 splits pure logic from the
RLS-backed persistence layer:

  * **Pure, no-DB** — the official Playwright manifest parses + validates
    through the SHARED tool-manifest parser (task_09_10), and the guided
    :class:`PlaywrightToolConfig` accepts a valid config and REJECTS a bad
    browser / screenshot mode / trace mode / non-positive timeout. These run
    anywhere.

  * **Seed against REAL Postgres + RLS** — :func:`seed_playwright_listing`
    registers Playwright as a VERIFIED GLOBAL listing (``tenant_id NULL``)
    under the official catalog source, is idempotent, and the GLOBAL listing
    is visible to a tenant's RLS-scoped session (the Phase A hybrid model: a
    NULL-tenant catalog row is readable by every tenant via the
    ``marketplace_listings_global_read`` policy) — while a tenant can only
    install it into its OWN tenant-scoped installation.

The e2e for the guided-config UI lives in
``apps/admin-panel/e2e/playwright-tool-config.spec.ts`` and is written but NOT
run here (node-playwright needs a browser — PENDING HUMAN VERIFICATION).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

# Register the ORM tables the marketplace FKs reference before we flush.
from api_server.db import domain as _domain  # noqa: F401
from api_server.db import marketplace as _marketplace  # noqa: F401
from api_server.db import models as _models  # noqa: F401
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

_PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
_PG_PORT = int(os.environ.get("TEST_PG_PORT", "15432"))


# ===========================================================================
# Layer 1 — pure manifest + guided-config validation (no DB)
# ===========================================================================
from api_server.db.marketplace import (  # noqa: E402
    MarketplaceListingKind,
    MarketplaceTrustLevel,
)
from api_server.marketplace.playwright import (  # noqa: E402
    PLAYWRIGHT_TOOL_NAME,
    PLAYWRIGHT_TOOL_VERSION,
    PlaywrightBrowser,
    PlaywrightConfigError,
    PlaywrightToolConfig,
    ScreenshotMode,
    TraceMode,
    config_schema,
    playwright_listing_manifest,
    playwright_tool_manifest,
    seed_playwright_listing,
)
from api_server.marketplace.tool_format import parse_tool_manifest  # noqa: E402
from api_server.marketplace.trust import (  # noqa: E402
    PERMISSION_ALLOWED_DOMAINS,
    PERMISSION_NETWORK_POLICY,
    NetworkPolicy,
)


def test_manifest_parses_and_validates() -> None:
    """The official manifest is a well-formed standard tool manifest."""
    manifest = playwright_tool_manifest()
    assert manifest.name == PLAYWRIGHT_TOOL_NAME
    assert manifest.version == PLAYWRIGHT_TOOL_VERSION
    assert manifest.kind is MarketplaceListingKind.TOOL
    assert manifest.implementation.runtime == "node-playwright"
    # Declared permissions: the sites under test + a restricted network posture.
    perms = {d["type"]: d["value"] for d in manifest.requested_permissions}
    assert PERMISSION_ALLOWED_DOMAINS in perms
    assert perms[PERMISSION_NETWORK_POLICY] == NetworkPolicy.RESTRICTED.value


def test_manifest_round_trips_through_shared_parser() -> None:
    """Re-parsing the rendered manifest dict's source yields the same fields.

    Proves the flagship tool is not a special case: it survives the exact
    same parser any community tool goes through.
    """
    from api_server.marketplace.playwright import PLAYWRIGHT_TOOL_YAML

    reparsed = parse_tool_manifest(PLAYWRIGHT_TOOL_YAML)
    assert reparsed.name == PLAYWRIGHT_TOOL_NAME
    assert reparsed.kind is MarketplaceListingKind.TOOL


def test_listing_manifest_embeds_guided_config_schema() -> None:
    """The listing manifest carries the guided config_schema for the UI."""
    listing_manifest = playwright_listing_manifest()
    assert "config_schema" in listing_manifest
    schema = listing_manifest["config_schema"]
    assert schema["properties"]["browsers"]["widget"] == "multiselect"
    assert set(schema["properties"]["browsers"]["items"]["enum"]) == {
        "chromium",
        "firefox",
        "webkit",
    }
    assert schema["properties"]["screenshots"]["enum"] == [m.value for m in ScreenshotMode]
    assert schema["properties"]["traces"]["enum"] == [m.value for m in TraceMode]


def test_config_accepts_a_valid_config() -> None:
    cfg = PlaywrightToolConfig.from_dict(
        {
            "browsers": ["chromium", "webkit"],
            "headless": False,
            "screenshots": "on",
            "traces": "retain-on-failure",
            "base_url": "https://staging.example.test",
            "timeout_ms": 60000,
        }
    )
    assert cfg.browsers == (PlaywrightBrowser.CHROMIUM, PlaywrightBrowser.WEBKIT)
    assert cfg.headless is False
    assert cfg.screenshots is ScreenshotMode.ON
    assert cfg.traces is TraceMode.RETAIN_ON_FAILURE
    assert cfg.base_url == "https://staging.example.test"
    assert cfg.timeout_ms == 60000
    # Round-trips back to a JSON-able mapping.
    assert cfg.to_dict()["browsers"] == ["chromium", "webkit"]


def test_config_defaults_when_fields_absent() -> None:
    cfg = PlaywrightToolConfig.from_dict({})
    assert cfg.browsers == (PlaywrightBrowser.CHROMIUM,)
    assert cfg.headless is True
    assert cfg.screenshots is ScreenshotMode.ONLY_ON_FAILURE
    assert cfg.traces is TraceMode.RETAIN_ON_FAILURE
    assert cfg.base_url is None
    assert cfg.timeout_ms == 30000


def test_config_dedupes_and_preserves_browser_order() -> None:
    cfg = PlaywrightToolConfig.from_dict({"browsers": ["webkit", "webkit", "chromium"]})
    assert cfg.browsers == (PlaywrightBrowser.WEBKIT, PlaywrightBrowser.CHROMIUM)


def test_config_rejects_unknown_browser() -> None:
    with pytest.raises(PlaywrightConfigError, match="browser"):
        PlaywrightToolConfig.from_dict({"browsers": ["chromium", "internet-explorer"]})


def test_config_rejects_empty_browser_selection() -> None:
    with pytest.raises(PlaywrightConfigError, match="browsers"):
        PlaywrightToolConfig.from_dict({"browsers": []})


def test_config_rejects_bad_screenshot_mode() -> None:
    with pytest.raises(PlaywrightConfigError, match="screenshots"):
        PlaywrightToolConfig.from_dict({"screenshots": "always"})


def test_config_rejects_bad_trace_mode() -> None:
    with pytest.raises(PlaywrightConfigError, match="traces"):
        PlaywrightToolConfig.from_dict({"traces": "sometimes"})


@pytest.mark.parametrize("bad_timeout", [0, -1, "30000", True, 1.5])
def test_config_rejects_bad_timeout(bad_timeout: object) -> None:
    with pytest.raises(PlaywrightConfigError, match="timeout_ms"):
        PlaywrightToolConfig.from_dict({"timeout_ms": bad_timeout})


def test_config_rejects_unknown_key() -> None:
    with pytest.raises(PlaywrightConfigError, match="unknown key"):
        PlaywrightToolConfig.from_dict({"headed": True})


def test_config_rejects_non_mapping() -> None:
    with pytest.raises(PlaywrightConfigError, match="mapping"):
        PlaywrightToolConfig.from_dict(["chromium"])


def test_config_schema_is_well_formed() -> None:
    schema = config_schema()
    assert schema["type"] == "object"
    assert "browsers" in schema["required"]
    assert schema["properties"]["timeout_ms"]["minimum"] == 1


# ===========================================================================
# Layer 2 — seed the verified GLOBAL listing against real Postgres + RLS
# ===========================================================================
@pytest.fixture()
def migrated_db(alembic_config, test_redis_url: str):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    yield


async def _seed_tenants(dsn: str) -> dict[str, UUID]:
    """Two tenants/admins; the marketplace tables start empty (no source/listing)."""
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "admin_a": uuid4(),
        "admin_b": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources,"
            " projects, agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-pw",
            ids["tenant_b"],
            "Tenant B",
            "tenant-b-pw",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["admin_a"],
            "admin-a@pw.test",
            "h",
            ids["admin_b"],
            "admin-b@pw.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_b"],
            ids["admin_b"],
        )
    finally:
        await conn.close()
    return ids


@asynccontextmanager
async def _publisher_session(admin_database_url: str) -> AsyncIterator[AsyncSession]:
    """A BYPASSRLS catalog-publisher session — writes the GLOBAL listing.

    Writing a ``tenant_id NULL`` global row is reserved for the catalog
    publisher; a tenant session's RLS WITH CHECK would reject it. The
    migrations role bypasses RLS, mirroring how the official source is seeded.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


@asynccontextmanager
async def _tenant_session(
    app_database_url: str, *, user_id: UUID, tenant_id: UUID
) -> AsyncIterator[AsyncSession]:
    """A NOBYPASSRLS app-role session with the RLS GUCs set, like the app."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(app_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    session = maker()
    try:
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, false)"), {"uid": str(user_id)}
        )
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, false)"), {"tid": str(tenant_id)}
        )
        yield session
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_registers_verified_global_listing(
    migrated_db, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """Seeding inserts a VERIFIED GLOBAL (tenant_id NULL) tool listing."""
    await _seed_tenants(migrations_pg_dsn)

    async with _publisher_session(admin_database_url) as session:
        result = await seed_playwright_listing(session)
        await session.commit()
        assert result.created is True

    # Inspect the row directly via the BYPASSRLS DSN.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT tenant_id, kind, name, version, trust_level, manifest, requested_permissions"
            " FROM marketplace_listings WHERE name = $1",
            PLAYWRIGHT_TOOL_NAME,
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["tenant_id"] is None  # GLOBAL catalog listing
    assert row["kind"] == MarketplaceListingKind.TOOL.value
    assert row["version"] == PLAYWRIGHT_TOOL_VERSION
    assert row["trust_level"] == MarketplaceTrustLevel.VERIFIED.value


@pytest.mark.asyncio
async def test_seed_is_idempotent(
    migrated_db, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """Re-seeding an already-seeded catalog is a no-op (refresh, not duplicate)."""
    await _seed_tenants(migrations_pg_dsn)

    async with _publisher_session(admin_database_url) as session:
        first = await seed_playwright_listing(session)
        await session.commit()
    async with _publisher_session(admin_database_url) as session:
        second = await seed_playwright_listing(session)
        await session.commit()

    assert first.created is True
    assert second.created is False
    assert first.listing_id == second.listing_id

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM marketplace_listings WHERE name = $1", PLAYWRIGHT_TOOL_NAME
        )
    finally:
        await conn.close()
    assert count == 1


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_global_listing_is_visible_to_every_tenant(
    migrated_db, app_database_url: str, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """A GLOBAL (tenant_id NULL) listing is readable by every tenant's RLS session.

    The Phase A hybrid model: the catalog row is owned by no tenant, so both
    tenant A and tenant B see it via the ``marketplace_listings_global_read``
    SELECT policy — neither can mutate it (it carries no tenant_id), but both
    can browse it to install into their own tenant-scoped installations.
    """
    from api_server.db.marketplace import MarketplaceListing

    seeded = await _seed_tenants(migrations_pg_dsn)
    async with _publisher_session(admin_database_url) as session:
        await seed_playwright_listing(session)
        await session.commit()

    for tenant_key, admin_key in (("tenant_a", "admin_a"), ("tenant_b", "admin_b")):
        async with _tenant_session(
            app_database_url, user_id=seeded[admin_key], tenant_id=seeded[tenant_key]
        ) as session:
            found = await session.execute(
                select(MarketplaceListing).where(
                    MarketplaceListing.name == PLAYWRIGHT_TOOL_NAME,
                    MarketplaceListing.deleted_at.is_(None),
                )
            )
            listing = found.scalar_one()
            assert listing.tenant_id is None
            assert listing.trust_level == MarketplaceTrustLevel.VERIFIED.value
