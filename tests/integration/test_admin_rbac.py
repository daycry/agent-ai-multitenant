"""RBAC tests for /admin/* endpoints.

Covers:
  - System Admin can create/list/get/update/delete tenants.
  - Tenant Admin (regular user) gets 403 on every /admin/* call.
  - Unauthenticated requests get 401.
  - GET /admin/users returns a cross-tenant list.
  - GET /admin/system-health returns at least postgres status.
  - Tenant creation writes an audit_log row.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture: same shape as the others, plus a helper to promote a user to
# system admin in the DB.
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
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_COUNT", "100")
    monkeypatch.setenv("API_SERVER_LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60")

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


async def _promote_to_system_admin(dsn: str, email: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE users SET is_system_admin = true WHERE email = $1", email)
    finally:
        await conn.close()


async def _login(client: AsyncClient, email: str, password: str) -> str:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def _register_and_login_admin(
    client: AsyncClient,
    migrations_dsn: str,
    email: str = "root@example.com",
    password: str = "longenoughpw",
) -> str:
    """Register a fresh user, promote to system admin in the DB,
    log in, return the resulting JWT."""
    await client.post("/auth/register", json={"email": email, "password": password})
    await _promote_to_system_admin(migrations_dsn, email)
    return await _login(client, email, password)


async def _register_and_login_user(
    client: AsyncClient,
    email: str = "tenant-admin@example.com",
    password: str = "longenoughpw",
) -> str:
    """Register a regular (non-admin) user and return the JWT."""
    await client.post("/auth/register", json={"email": email, "password": password})
    return await _login(client, email, password)


# ---------------------------------------------------------------------------
# Tenants — RBAC
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_tenant_as_system_admin_succeeds(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_admin(client, migrations_pg_dsn)
        resp = await client.post(
            "/admin/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Acme", "slug": "acme"},
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Acme"
    assert body["slug"] == "acme"
    assert body["is_active"] is True
    assert body["deleted_at"] is None
    UUID(body["id"])  # parses


@pytest.mark.asyncio
async def test_create_tenant_as_tenant_admin_is_403(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_user(client)
        resp = await client.post(
            "/admin/tenants",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Hostile", "slug": "hostile"},
        )

    assert resp.status_code == 403
    assert "system admin" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_tenant_unauthenticated_is_401(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.post("/admin/tenants", json={"name": "X", "slug": "x"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Tenants — listing / get / update / delete
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_tenants_returns_all_active(configured_app, migrations_pg_dsn: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_admin(client, migrations_pg_dsn)
        headers = {"Authorization": f"Bearer {token}"}

        for slug in ("alpha", "beta", "gamma"):
            r = await client.post(
                "/admin/tenants",
                headers=headers,
                json={"name": slug.title(), "slug": slug},
            )
            assert r.status_code == 201

        listed = await client.get("/admin/tenants", headers=headers)

    assert listed.status_code == 200, listed.text
    slugs = {t["slug"] for t in listed.json()}
    # Subset assertion (other tests may have created their own tenants).
    assert {"alpha", "beta", "gamma"}.issubset(slugs)


@pytest.mark.asyncio
async def test_update_tenant_changes_name_and_status(
    configured_app, migrations_pg_dsn: str
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_admin(client, migrations_pg_dsn)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/admin/tenants",
            headers=headers,
            json={"name": "Original", "slug": "original"},
        )
        tenant_id = created.json()["id"]

        updated = await client.put(
            f"/admin/tenants/{tenant_id}",
            headers=headers,
            json={"name": "Renamed", "is_active": False},
        )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["name"] == "Renamed"
    assert body["is_active"] is False


@pytest.mark.asyncio
async def test_delete_tenant_soft_deletes(configured_app, migrations_pg_dsn: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_admin(client, migrations_pg_dsn)
        headers = {"Authorization": f"Bearer {token}"}

        created = await client.post(
            "/admin/tenants",
            headers=headers,
            json={"name": "GoneSoon", "slug": "gonesoon"},
        )
        tenant_id = created.json()["id"]

        deleted = await client.delete(f"/admin/tenants/{tenant_id}", headers=headers)
        assert deleted.status_code == 204

        # Soft-delete: row still exists when fetched by id.
        fetched = await client.get(f"/admin/tenants/{tenant_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["deleted_at"] is not None

        # And the listing excludes deleted rows.
        listed = await client.get("/admin/tenants", headers=headers)
    assert all(t["slug"] != "gonesoon" for t in listed.json())


# ---------------------------------------------------------------------------
# Cross-tenant user listing
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_users_cross_tenant(configured_app, migrations_pg_dsn: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        # Register a regular user + an admin user.
        await client.post(
            "/auth/register",
            json={"email": "regular@example.com", "password": "longenoughpw"},
        )
        token = await _register_and_login_admin(client, migrations_pg_dsn)

        users = await client.get("/admin/users", headers={"Authorization": f"Bearer {token}"})

    assert users.status_code == 200, users.text
    emails = {u["email"] for u in users.json()}
    assert "regular@example.com" in emails
    assert "root@example.com" in emails


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_system_health(configured_app, migrations_pg_dsn: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_admin(client, migrations_pg_dsn)
        resp = await client.get(
            "/admin/system-health",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Overall is driven by postgres only; the other services may be
    # unreachable from the test environment (CI may not start vault /
    # minio / clamav), so we assert they are *reported*, not their
    # individual statuses.
    assert body["status"] == "ok"
    names = {s["name"] for s in body["services"]}
    assert names == {"postgres", "redis", "vault", "minio", "clamav"}


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_log_records_tenant_creation(configured_app, migrations_pg_dsn: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        token = await _register_and_login_admin(client, migrations_pg_dsn)
        headers = {"Authorization": f"Bearer {token}"}
        created = await client.post(
            "/admin/tenants",
            headers=headers,
            json={"name": "Audited", "slug": "audited"},
        )
        assert created.status_code == 201
        new_tenant_id = created.json()["id"]

    # Query audit_log directly with the BYPASSRLS DSN.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT action, resource_type, resource_id, changes::text AS changes_json"
            " FROM audit_log WHERE resource_id = $1 AND action = $2",
            UUID(new_tenant_id),
            "tenant.created",
        )
    finally:
        await conn.close()

    assert row is not None, "no audit row recorded for tenant.created"
    assert row["resource_type"] == "tenant"
    assert "audited" in row["changes_json"]
