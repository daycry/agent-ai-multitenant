"""Tool deduplication + taxonomy enforcement (Plan 06.18 task_06_18_04, ADR 0049).

Three invariants land here, all DB- and API-enforced:

  1. **No duplicate live names per tenant.** A partial unique index
     ``uq_tools_tenant_name (tenant_id, name) WHERE deleted_at IS NULL``
     forbids two *live* tools of the same tenant sharing a name; a
     soft-deleted name may be reused. ``create_tool``/``update_tool`` map a
     same-tenant or built-in collision to ``409 Conflict`` (built-ins live
     under the platform tenant, so the cross-tenant homonym guard is an
     application check against the canonical catalog, not a DB constraint).

  2. **Names are normalised to slug-case** before persistence, so
     ``"Read File"`` and ``read_file`` collide rather than coexisting.

  3. **Taxonomy is a closed value set.** ``category`` / ``security_level`` /
     ``implementation_type`` carry CHECK constraints derived from the real
     seed (``builtin_tools.py``) plus the documented origin/orchestration
     buckets; a value outside the set is rejected at both the API (422) and
     the database (CHECK) layers.

The ``cross_tenant`` mark gates these in CI (every assertion either crosses a
tenant boundary or guarantees the dedup/taxonomy that the multi-tenant catalog
relies on).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: two tenants (each a tenant_admin) + one platform built-in named
# ``read_file`` so the homonym guard has a real catalog row to collide with.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    builtin_tool = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE skills, tools, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            user_a,
            "alice@a.test",
            "argon2-placeholder",
            user_b,
            "bob@b.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
            uuid4(),
            tenant_b,
            user_b,
            "tenant_admin",
        )
        # Platform built-in named read_file (file category, builtin impl).
        await conn.execute(
            "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
            " security_level, is_builtin) VALUES ($1, $2, 'read_file', 'file', 'builtin',"
            " 'safe', true)",
            builtin_tool,
            _PLATFORM_TENANT_ID,
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "builtin_tool": builtin_tool,
    }


# ---------------------------------------------------------------------------
# Fixtures (same shape as test_skills_tools_endpoints.py)
# ---------------------------------------------------------------------------
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _valid_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "acme_deploy",
        "category": "network",
        "implementation_type": "http_endpoint",
        "security_level": "sandboxed",
        "timeout_seconds": 30,
    }
    base.update(overrides)
    return base


# ===========================================================================
# Homonym of a platform built-in -> 409
# ===========================================================================
@pytest.mark.asyncio
async def test_create_custom_homonym_of_builtin_is_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Exact built-in name.
        resp = await client.post(
            "/tools", json=_valid_payload(name="read_file", category="file"), headers=headers
        )
        assert resp.status_code == 409, resp.text

        # A label that normalises to the same slug must also collide.
        resp2 = await client.post(
            "/tools", json=_valid_payload(name="Read File", category="file"), headers=headers
        )
        assert resp2.status_code == 409, resp2.text


@pytest.mark.asyncio
async def test_update_custom_to_builtin_name_is_409(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/tools", json=_valid_payload(name="acme_deploy"), headers=headers
        )
        assert created.status_code == 201, created.text
        tool_id = created.json()["id"]

        # Renaming onto a platform built-in name collides.
        upd = await client.put(f"/tools/{tool_id}", json={"name": "read_file"}, headers=headers)
        assert upd.status_code == 409, upd.text


# ===========================================================================
# Name normalisation to slug-case
# ===========================================================================
@pytest.mark.asyncio
async def test_create_normalises_name_to_slug(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/tools", json=_valid_payload(name="  My Custom Tool  "), headers=headers
        )
        assert created.status_code == 201, created.text
        assert created.json()["name"] == "my_custom_tool"


@pytest.mark.asyncio
async def test_duplicate_live_name_within_tenant_is_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        first = await client.post(
            "/tools", json=_valid_payload(name="acme_deploy"), headers=headers
        )
        assert first.status_code == 201, first.text
        # Same normalised name -> 409, no second row.
        dup = await client.post("/tools", json=_valid_payload(name="Acme Deploy"), headers=headers)
        assert dup.status_code == 409, dup.text

        listed = await client.get("/tools", headers=headers)
        assert sum(1 for t in listed.json() if t["name"] == "acme_deploy") == 1


# ===========================================================================
# Taxonomy: closed value set
# ===========================================================================
@pytest.mark.asyncio
async def test_invalid_security_level_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tools",
            json=_valid_payload(name="bad_sec", security_level="dangerous"),
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_invalid_category_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tools",
            json=_valid_payload(name="bad_cat", category="totally_made_up"),
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_db_check_rejects_out_of_enum_security_level(
    configured_app, admin_pg_dsn: str
) -> None:
    """Defense in depth: even bypassing the API, the DB CHECK refuses an
    out-of-enum security_level (asyncpg raises CheckViolationError)."""
    conn = await asyncpg.connect(admin_pg_dsn)
    try:
        with pytest.raises(asyncpg.exceptions.CheckViolationError):
            await conn.execute(
                "INSERT INTO tools (id, tenant_id, name, category, implementation_type,"
                " security_level, is_builtin) VALUES ($1, $2, 'x', 'file', 'builtin',"
                " 'nonsense', false)",
                uuid4(),
                _PLATFORM_TENANT_ID,
            )
    finally:
        await conn.close()


# ===========================================================================
# Cross-tenant isolation
# ===========================================================================
@pytest.mark.asyncio
async def test_same_custom_name_allowed_across_tenants_but_isolated(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Both tenants create a custom tool with the SAME name -> no collision.
        a = await client.post(
            "/tools",
            json=_valid_payload(name="shared_name"),
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert a.status_code == 201, a.text
        b = await client.post(
            "/tools",
            json=_valid_payload(name="shared_name"),
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert b.status_code == 201, b.text

        # Tenant B cannot see A's row (different id), and cannot fetch it.
        a_id = a.json()["id"]
        fetch = await client.get(f"/tools/{a_id}", headers={"Authorization": f"Bearer {token_b}"})
        assert fetch.status_code == 404, fetch.text

        listed_b = await client.get("/tools", headers={"Authorization": f"Bearer {token_b}"})
        ids_b = {t["id"] for t in listed_b.json()}
        assert a_id not in ids_b
        # B's own homonym IS visible to B.
        assert b.json()["id"] in ids_b


@pytest.mark.asyncio
async def test_builtin_homonym_guard_applies_to_every_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    """The catalog read-through makes built-ins visible to all tenants;
    the homonym guard must reject ``read_file`` for tenant B too."""
    seeded = await _seed(migrations_pg_dsn)
    token_b = await _mint_token(seeded["user_b"], seeded["tenant_b"])
    headers = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/tools", json=_valid_payload(name="read_file", category="file"), headers=headers
        )
    assert resp.status_code == 409, resp.text


# ===========================================================================
# Migration reversibility (the new revision upgrades/downgrades cleanly)
# ===========================================================================
def test_migration_upgrade_downgrade_upgrade_is_reversible(
    alembic_config: Config, admin_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")

    async def _index_present() -> bool:
        conn = await asyncpg.connect(admin_pg_dsn)
        try:
            row = await conn.fetchval(
                "SELECT 1 FROM pg_indexes WHERE tablename = 'tools'"
                " AND indexname = 'uq_tools_tenant_name'"
            )
            return row is not None
        finally:
            await conn.close()

    assert asyncio.run(_index_present()) is True

    # downgrade one step removes the unique index + CHECKs added by this rev...
    command.downgrade(alembic_config, "-1")
    assert asyncio.run(_index_present()) is False

    # ...and re-upgrade restores it (proves the migration is reversible).
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_index_present()) is True
