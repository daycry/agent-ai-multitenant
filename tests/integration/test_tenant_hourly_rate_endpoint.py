"""Integration test for /tenant-settings/hourly-rate (Plan 03 task_03_26)."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    admin_id = uuid4()
    member_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plans, projects, agents, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Rate",
            "tenant-rate",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-rate",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            admin_id,
            "admin@rate.test",
            "h",
            member_id,
            "member@rate.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4), ($5, $6, $7, $8)",
            uuid4(),
            tenant_id,
            admin_id,
            "tenant_admin",
            uuid4(),
            tenant_id,
            member_id,
            "tenant_user",
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "admin_id": admin_id,
        "member_id": member_id,
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


@pytest.mark.asyncio
async def test_get_returns_null_when_tenant_has_no_custom_rate(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_id"], seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/tenant-settings/hourly-rate",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["hourly_rate"] is None
        assert body["hourly_rate_currency"] is None


@pytest.mark.asyncio
async def test_tenant_admin_can_set_the_hourly_rate(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        upd = await client.put(
            "/tenant-settings/hourly-rate",
            json={"hourly_rate": "75.50", "hourly_rate_currency": "eur"},
            headers=headers,
        )
        assert upd.status_code == 200, upd.text
        body = upd.json()
        assert body["hourly_rate"] == "75.50"
        # Currency is uppercased server-side.
        assert body["hourly_rate_currency"] == "EUR"

        # Round-trip via GET.
        roundtrip = await client.get("/tenant-settings/hourly-rate", headers=headers)
        assert roundtrip.json()["hourly_rate"] == "75.50"


@pytest.mark.asyncio
async def test_non_admin_cannot_change_the_rate(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_id"], seeded["tenant_id"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        upd = await client.put(
            "/tenant-settings/hourly-rate",
            json={"hourly_rate": "100", "hourly_rate_currency": "EUR"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert upd.status_code == 403
        assert "tenant_admin" in upd.json()["detail"]


@pytest.mark.asyncio
async def test_configured_rate_drives_the_cost_breakdown(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Once the tenant configures a custom rate, the cost-breakdown
    endpoint uses it instead of the 50 EUR default."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Configure rate = 80 EUR/h.
        await client.put(
            "/tenant-settings/hourly-rate",
            json={"hourly_rate": "80", "hourly_rate_currency": "EUR"},
            headers=headers,
        )

        # Seed a tiny project + plan with one 4h task.
        from sqlalchemy.ext.asyncio import create_async_engine

        # We seed via a direct connection since the existing /projects
        # endpoint requires test infrastructure we already exercised.
        eng = create_async_engine(
            "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
            "@localhost:15432/agentic_platform_test"
        )
        async with eng.begin() as conn:
            project_id = uuid7()
            plan_id = uuid7()
            await conn.exec_driver_sql(
                "INSERT INTO projects (id, tenant_id, name) VALUES "
                f"('{project_id}', '{seeded['tenant_id']}', 'P')"
            )
            await conn.exec_driver_sql(
                "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
                f" VALUES ('{plan_id}', '{seeded['tenant_id']}', '{project_id}',"
                " 'P', 'draft',"
                """ '{"tasks": [{"id":"t1","title":"X","estimated_hours":4}]}'::jsonb)"""
            )
        await eng.dispose()

        resp = await client.get(f"/plans/{plan_id}/cost-breakdown", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        # 4 h × 80 €/h = 320 €.
        assert body["human"]["hourly_rate"] == "80.00"
        assert body["human"]["total_cost"] == "320.00"
