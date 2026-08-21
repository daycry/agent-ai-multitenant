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
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_VERIFIED = "verified"
_PLAYWRIGHT_NAME = "playwright"

# A well-formed SKILL.md — used only to prove the private-listing write
# endpoints WORK, so the 404 the official catalog returns cannot be mistaken
# for a broken route. Mirrors the fixture in ``test_private_marketplace.py``.
_SKILL_MD = """\
---
name: internal-reporter
description: Generates the weekly internal status report.
version: 1.0.0
dependencies:
  - jinja2
permissions:
  allowed_paths: [/workspace/reports]
  network_policy: none
examples:
  - title: Weekly report
    prompt: "Generate this week's status report"
---

# Internal Reporter

A tenant-internal skill that compiles a weekly status report.
"""


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


# ===========================================================================
# human_09_1_01 — "los oficiales no se pueden tocar desde el tenant"
#
# Estaba implementado (`_load_private_listing` en routers/marketplace.py exige
# `tenant_id == caller`, así que una fila GLOBAL sale por el 404) y sin un solo
# assert. Sin este test se podía relajar ese filtro —dejando que un tenant
# reescribiera o despublicara el catálogo oficial que ven TODOS los tenants—
# y la suite seguía verde.
# ===========================================================================
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_tenant_admin(dsn: str) -> tuple[UUID, UUID]:
    """One tenant + one tenant_admin. Leaves marketplace rows untouched (the
    official catalog must already be seeded when this runs)."""
    tenant_id = uuid4()
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-mktseed')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'admin@mktseed.test', 'h')",
            user_id,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()
    return tenant_id, user_id


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_official_listings_cannot_be_modified_from_a_tenant(
    configured_app, migrations_pg_dsn: str, tmp_path: Path
) -> None:
    """A tenant_admin can neither re-publish nor unpublish an OFFICIAL listing.

    Both write verbs 404 (the row is global, not the caller's private one) and
    — the part that actually matters — the catalog row survives byte-identical:
    same name, same version, still live.
    """
    # ORDEN IMPORTANTE: `_seed_tenant_admin` hace TRUNCATE organizations
    # CASCADE, que arrastraría los listings (FK tenant_id). Primero el tenant,
    # después el catálogo oficial.
    tenant_id, user_id = await _seed_tenant_admin(migrations_pg_dsn)
    await _truncate(migrations_pg_dsn)
    seed_result = await _run_seed(_as_async_dsn(migrations_pg_dsn), str(tmp_path))
    assert seed_result.total >= 4

    token = await _mint(user_id, tenant_id)
    headers = {"Authorization": f"Bearer {token}"}

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        official = await conn.fetchrow(
            "SELECT id, name, version FROM marketplace_listings"
            " WHERE tenant_id IS NULL AND deleted_at IS NULL AND kind = 'skill' LIMIT 1"
        )
    finally:
        await conn.close()
    assert official is not None, "el seed oficial debe haber dejado un listing skill global"
    listing_id = official["id"]

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # El listing oficial SÍ es visible para el tenant (el 404 de abajo es
        # sobre la ESCRITURA, no un "no existe para mí").
        browse = await client.get("/marketplace/listings", headers=headers)
        assert browse.status_code == 200, browse.text
        assert str(listing_id) in {row["id"] for row in browse.json()}

        # Re-publicar el oficial → 404.
        upd = await client.put(
            f"/marketplace/private/listings/{listing_id}",
            json={"manifest": _SKILL_MD, "author": "intruso"},
            headers=headers,
        )
        assert upd.status_code == 404, upd.text
        assert upd.json()["detail"] == "private listing not found"

        # Despublicar el oficial → 404.
        deleted = await client.delete(
            f"/marketplace/private/listings/{listing_id}", headers=headers
        )
        assert deleted.status_code == 404, deleted.text

        # Contrapeso: los MISMOS dos verbos funcionan sobre un listing PROPIO,
        # así que el 404 de arriba es por ser GLOBAL, no por una ruta rota.
        pub = await client.post(
            "/marketplace/private/listings",
            json={"kind": "skill", "manifest": _SKILL_MD},
            headers=headers,
        )
        assert pub.status_code == 201, pub.text
        own_id = pub.json()["id"]
        own_upd = await client.put(
            f"/marketplace/private/listings/{own_id}",
            json={"manifest": _SKILL_MD.replace("version: 1.0.0", "version: 1.1.0")},
            headers=headers,
        )
        assert own_upd.status_code == 200, own_upd.text
        own_del = await client.delete(f"/marketplace/private/listings/{own_id}", headers=headers)
        assert own_del.status_code == 204, own_del.text

    # El listing oficial quedó INTACTO: mismo nombre, misma versión, vivo.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        after = await conn.fetchrow(
            "SELECT name, version, author, tenant_id, deleted_at"
            " FROM marketplace_listings WHERE id = $1",
            listing_id,
        )
    finally:
        await conn.close()
    assert after is not None
    assert after["name"] == official["name"]
    assert after["version"] == official["version"]
    assert after["author"] != "intruso"
    assert after["tenant_id"] is None
    assert after["deleted_at"] is None
