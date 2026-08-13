"""Superadmin cross-tenant read/write tests.

Validates the `is_system_admin` superpower wired through
`auth/deps.py`:

  - Without tenant context (no JWT tid, no X-Tenant-Id header) a
    superadmin's session uses migrations_user (BYPASSRLS) and reads
    return rows from every tenant.
  - With `X-Tenant-Id` header set, the superadmin acts AS that
    tenant — app_user session, RLS scoped, writes land in that
    tenant's bucket.
  - A non-admin user that sends `X-Tenant-Id` does NOT cross
    tenants — the header is silently ignored and RLS keeps them
    inside their JWT's tid.
  - Garbage in the header (non-UUID) yields 400.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_admin = uuid4()
    project_a = uuid4()
    project_b = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            tenant_b,
            "Tenant B",
            "tenant-b",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, true)",
            user_a,
            "alice@a.test",
            "argon2-placeholder",
            user_admin,
            "root@platform.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_a,
            user_a,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, $3, 'active', false),"
            "        ($4, $5, $6, 'active', false)",
            project_a,
            tenant_a,
            "A's project",
            project_b,
            tenant_b,
            "B's project",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_admin": user_admin,
        "project_a": project_a,
        "project_b": project_b,
    }


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import (
        _flush_redis,
        _grant_app_user_existing_tables,
    )

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


async def _mint_token(
    user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False
) -> str:
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


@pytest.mark.asyncio
async def test_superadmin_without_tenant_sees_all_tenants(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint_token(seed["user_admin"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/projects", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    rows = resp.json()
    names = {r["name"] for r in rows}
    assert names == {"A's project", "B's project"}


@pytest.mark.asyncio
async def test_superadmin_with_x_tenant_id_acts_as_that_tenant(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint_token(seed["user_admin"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # GET filtered to tenant A via header.
        resp_a = await client.get(
            "/projects",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": str(seed["tenant_a"]),
            },
        )
        # GET filtered to tenant B via header.
        resp_b = await client.get(
            "/projects",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": str(seed["tenant_b"]),
            },
        )

    assert resp_a.status_code == 200
    assert {r["name"] for r in resp_a.json()} == {"A's project"}
    assert resp_b.status_code == 200
    assert {r["name"] for r in resp_b.json()} == {"B's project"}


@pytest.mark.asyncio
async def test_superadmin_can_write_into_any_tenant_via_header(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint_token(seed["user_admin"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": str(seed["tenant_b"]),
            },
            json={"name": "Admin-created in B"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Admin-created in B"
    assert body["tenant_id"] == str(seed["tenant_b"])


@pytest.mark.asyncio
async def test_non_admin_x_tenant_header_is_ignored(configured_app, migrations_pg_dsn: str) -> None:
    """A regular tenant user that sends `X-Tenant-Id` for someone
    else's tenant must not escape their own scope — the header is
    silently dropped for non-admins."""
    seed = await _seed(migrations_pg_dsn)
    token = await _mint_token(seed["user_a"], seed["tenant_a"], is_system_admin=False)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/projects",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": str(seed["tenant_b"]),
            },
        )

    assert resp.status_code == 200
    names = {r["name"] for r in resp.json()}
    assert names == {"A's project"}  # NOT B's


@pytest.mark.asyncio
async def test_garbage_x_tenant_header_returns_400(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint_token(seed["user_admin"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/projects",
            headers={
                "Authorization": f"Bearer {token}",
                "X-Tenant-Id": "not-a-uuid",
            },
        )

    assert resp.status_code == 400
    assert "X-Tenant-Id" in resp.text


@pytest.mark.asyncio
async def test_superadmin_write_without_tenant_returns_400(
    configured_app, migrations_pg_dsn: str
) -> None:
    """No tenant context (no JWT tid, no header) on a write path
    must fail with the helpful "active tenant required" message
    instead of silently inventing one."""
    seed = await _seed(migrations_pg_dsn)
    token = await _mint_token(seed["user_admin"], None, is_system_admin=True)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "orphan"},
        )

    assert resp.status_code == 400
    assert "active tenant required" in resp.text.lower()
