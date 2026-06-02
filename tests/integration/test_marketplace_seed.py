"""Integration tests for the official marketplace catalog seed (Plan 09.1
task_09_1_01).

Drives :func:`api_server.marketplace.seed.seed_marketplace_listings` against
the real Postgres + RLS and verifies the Plan 09.1 decisions:

  - after seeding, the catalog has >= 4 VERIFIED, GLOBAL (``tenant_id NULL``)
    listings INCLUDING the flagship Playwright tool;
  - the seed is IDEMPOTENT — re-running it inserts nothing new and never
    duplicates a listing (``created`` drops to 0, the row count is stable);
  - a tenant session SEES every official listing via the global-read RLS
    policy (and only those — no private rows leak in);
  - each seeded listing PARSES with its format parser (SKILL.md / tool.yaml),
    and each SKILL listing's artifact is written to disk for the install
    fetcher.

Mirrors ``test_seed_tools.py``: seed under the BYPASSRLS migrations role, then
probe as the NOBYPASSRLS app_user so the policies are actually exercised.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_VERIFIED = "verified"
_PLAYWRIGHT_NAME = "playwright"


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_audit_entries, marketplace_installations,"
            " marketplace_listings, marketplace_sources CASCADE"
        )
    finally:
        await conn.close()


async def _run_seed(async_dsn: str, artifact_root: str):
    from api_server.marketplace.seed import seed_marketplace_listings
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(async_dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            return await seed_marketplace_listings(session, artifact_root=artifact_root)
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Catalog is non-empty: >= 4 VERIFIED global listings incl. Playwright.
# ---------------------------------------------------------------------------
def test_seed_creates_verified_global_listings_including_playwright(
    alembic_config, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    result = asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn), str(tmp_path)))
    # Playwright + >= 3 skills => at least 4 listings, all created this run.
    assert result.total >= 4
    assert result.created == result.total

    async def _fetch() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch(
                "SELECT name, kind, trust_level, tenant_id, author"
                " FROM marketplace_listings WHERE deleted_at IS NULL"
            )
        finally:
            await conn.close()

    rows = asyncio.run(_fetch())
    # Every official listing is VERIFIED + GLOBAL (tenant_id NULL).
    assert len(rows) >= 4
    for row in rows:
        assert row["trust_level"] == _VERIFIED, f"{row['name']} must be VERIFIED"
        assert row["tenant_id"] is None, f"{row['name']} must be GLOBAL (tenant_id NULL)"

    names = {r["name"] for r in rows}
    assert _PLAYWRIGHT_NAME in names, "the flagship Playwright tool must be seeded"
    # At least three SKILL listings alongside the tool.
    skill_rows = [r for r in rows if r["kind"] == "skill"]
    assert len(skill_rows) >= 3


# ---------------------------------------------------------------------------
# Idempotency: re-seed inserts nothing new and never duplicates.
# ---------------------------------------------------------------------------
def test_seed_is_idempotent(alembic_config, migrations_pg_dsn: str, tmp_path: Path) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    async_dsn = _as_async_dsn(migrations_pg_dsn)
    first = asyncio.run(_run_seed(async_dsn, str(tmp_path)))
    second = asyncio.run(_run_seed(async_dsn, str(tmp_path)))

    # The first run creates everything; the second creates nothing.
    assert first.created == first.total
    assert second.created == 0
    assert second.total == first.total

    async def _count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow(
                "SELECT count(*) FROM marketplace_listings WHERE deleted_at IS NULL"
            )
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    # Row count is stable across re-seeds — no duplicates.
    assert asyncio.run(_count()) == first.total

    # And the (source, tenant_id, name, version) identity is unique per row.
    async def _distinct_identities() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow(
                "SELECT count(DISTINCT (source_id, name, version))"
                " FROM marketplace_listings WHERE deleted_at IS NULL"
            )
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    assert asyncio.run(_distinct_identities()) == first.total


# ---------------------------------------------------------------------------
# A tenant session sees every official listing via the global-read RLS policy.
# ---------------------------------------------------------------------------
def test_seeded_listings_visible_to_tenant_via_rls(
    alembic_config, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    total = asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn), str(tmp_path))).total

    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
        _grant_app_user_existing_tables,
    )

    asyncio.run(_grant_app_user_existing_tables())

    tenant_id = uuid4()
    app_dsn = f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"

    async def _seed_tenant_and_count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)"
                " ON CONFLICT DO NOTHING",
                tenant_id,
                "Tenant Seed",
                f"tenant-seed-{tenant_id.hex[:8]}",
            )
        finally:
            await conn.close()

        conn = await asyncpg.connect(app_dsn)
        try:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant_id))
                return int(
                    await conn.fetchval(
                        "SELECT count(*) FROM marketplace_listings"
                        " WHERE deleted_at IS NULL AND trust_level = 'verified'"
                    )
                )
        finally:
            await conn.close()

    # Every GLOBAL official listing is visible to the tenant (and none are
    # private, so the count equals the seeded total).
    assert asyncio.run(_seed_tenant_and_count()) == total


# ---------------------------------------------------------------------------
# Each seeded listing parses with its format parser + artifact on disk.
# ---------------------------------------------------------------------------
def test_each_seeded_listing_parses_with_its_format_parser(
    alembic_config, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn), str(tmp_path)))

    from api_server.marketplace.playwright import PLAYWRIGHT_TOOL_YAML
    from api_server.marketplace.skill_format import parse_skill_md
    from api_server.marketplace.tool_format import parse_tool_manifest

    # The tool manifest parses (the listing's canonical YAML).
    tool_manifest = parse_tool_manifest(PLAYWRIGHT_TOOL_YAML)
    assert tool_manifest.name == _PLAYWRIGHT_NAME

    async def _fetch_listings() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch(
                "SELECT id, name, kind FROM marketplace_listings WHERE deleted_at IS NULL"
            )
        finally:
            await conn.close()

    rows = asyncio.run(_fetch_listings())
    for row in rows:
        if row["kind"] != "skill":
            continue
        # The SKILL.md artifact was written under <root>/<listing_id>/SKILL.md
        # and parses cleanly through the shared parser.
        artifact = tmp_path / str(row["id"]) / "SKILL.md"
        assert artifact.is_file(), f"missing SKILL.md artifact for {row['name']}"
        manifest = parse_skill_md(artifact.read_text(encoding="utf-8"))
        assert manifest.name == row["name"]
        assert manifest.version  # non-empty, valid semver (parser enforces)
