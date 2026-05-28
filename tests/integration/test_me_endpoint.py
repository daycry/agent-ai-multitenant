"""Tests for the enriched `/me` endpoint (Plan 06.8 task_06_8_04).

`/me` returns the current user's profile + all memberships across
tenants. The UI consumes it to know which buttons to show.

Cases covered:

  - tenant_admin in tenant A → email/role/memberships visible, only
    tenant A in memberships, role=tenant_admin, active_tenant_id set.
  - tenant_user in tenants A and B → both memberships listed; the
    active one is determined by the JWT `tid` claim.
  - system_admin → is_system_admin=true even with no memberships.
  - no tid (fresh login) → active_tenant_id is null but the user's
    memberships are still returned so the tenant-picker can show
    them.
  - unauthenticated → 401.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: TWO tenants, a user that's member of both, plus a single-tenant
# admin user and a system_admin "stranger".
# ---------------------------------------------------------------------------
async def _seed_db(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    admin_user = uuid4()
    multi_user = uuid4()
    sysadmin_user = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Acme Corp",
            "acme",
            tenant_b,
            "Beta Industries",
            "beta",
        )
        await conn.execute(
            "INSERT INTO users (id, email, full_name, password_hash, is_system_admin)"
            " VALUES"
            " ($1, 'admin@acme.test', 'Alice Admin', 'hash', false),"
            " ($2, 'multi@acme.test', 'Mary Multi', 'hash', false),"
            " ($3, 'root@example.com', NULL, 'hash', true)",
            admin_user,
            multi_user,
            sysadmin_user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_admin'),"
            " ($7, $8, $9, 'tenant_user')",
            # admin_user is tenant_admin in tenant_a only
            uuid4(),
            tenant_a,
            admin_user,
            # multi_user is admin in tenant_a, user in tenant_b
            uuid4(),
            tenant_a,
            multi_user,
            uuid4(),
            tenant_b,
            multi_user,
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_user": admin_user,
        "multi_user": multi_user,
        "sysadmin_user": sysadmin_user,
    }


# ---------------------------------------------------------------------------
# Fixture
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


async def _mint(user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


# ---------------------------------------------------------------------------
# Single-tenant admin
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_returns_profile_for_single_tenant_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(seed["admin_user"])
    assert body["email"] == "admin@acme.test"
    assert body["full_name"] == "Alice Admin"
    assert body["is_system_admin"] is False
    assert body["active_tenant_id"] == str(seed["tenant_a"])
    assert len(body["memberships"]) == 1
    m = body["memberships"][0]
    assert m["tenant_id"] == str(seed["tenant_a"])
    assert m["tenant_name"] == "Acme Corp"
    assert m["role"] == "tenant_admin"
    assert m["is_active"] is True


# ---------------------------------------------------------------------------
# Multi-tenant user — both memberships visible
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_returns_all_memberships_across_tenants(
    configured_app, migrations_pg_dsn: str
) -> None:
    """RLS would hide tenant B's row from a user with active tenant A.
    `/me` uses BYPASSRLS so the tenant-picker can see all memberships."""
    seed = await _seed_db(migrations_pg_dsn)
    # Mint with active = tenant_a; should still see tenant_b membership.
    token = await _mint(seed["multi_user"], seed["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_tenant_id"] == str(seed["tenant_a"])
    assert len(body["memberships"]) == 2
    by_tenant = {m["tenant_id"]: m for m in body["memberships"]}
    assert by_tenant[str(seed["tenant_a"])]["role"] == "tenant_admin"
    assert by_tenant[str(seed["tenant_a"])]["tenant_name"] == "Acme Corp"
    assert by_tenant[str(seed["tenant_b"])]["role"] == "tenant_user"
    assert by_tenant[str(seed["tenant_b"])]["tenant_name"] == "Beta Industries"


# ---------------------------------------------------------------------------
# System admin — is_system_admin=true even with no memberships
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_marks_system_admin_with_no_memberships(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["sysadmin_user"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_system_admin"] is True
    assert body["active_tenant_id"] is None
    assert body["memberships"] == []


# ---------------------------------------------------------------------------
# No tid (fresh login) — active is null, memberships still listed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_handles_no_tid_claim(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint(seed["multi_user"], None)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/me", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_tenant_id"] is None
    # The tenant-picker shows both options so the user can pick one.
    assert len(body["memberships"]) == 2


# ---------------------------------------------------------------------------
# Unauthenticated → 401
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_me_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/me")
    assert resp.status_code == 401
