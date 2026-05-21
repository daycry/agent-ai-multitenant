"""Integration tests for the read-only `/approval-policies` catalog
endpoint (task_01_23 substrate).

The endpoint surfaces the four built-in approval-policy presets seeded
under PLATFORM_TENANT_ID. RLS's `approval_policy_templates_builtin_read`
policy lets tenant sessions read them; this test exercises both the
no-tid case (system admin / fresh login) and the with-tid case.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed_db(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    user_a = uuid4()
    policy_id = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE approval_policy_templates, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES" " ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_a,
            "alice@a.test",
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
            "INSERT INTO approval_policy_templates"
            " (id, tenant_id, name, description, categories, is_builtin)"
            " VALUES ($1, $2, $3, $4, $5::jsonb, true)",
            policy_id,
            _PLATFORM_TENANT_ID,
            "Sandbox",
            "auto everywhere",
            json.dumps({"categories": {"code_changes": "auto"}}),
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "user_a": user_a,
        "policy_id": policy_id,
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


async def _mint_token(user_id: UUID, tenant_id: UUID | None) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_unauthenticated_is_401(configured_app, migrations_pg_dsn: str) -> None:
    await _seed_db(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/approval-policies")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_lists_builtin_with_tenant_session(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint_token(seed["user_a"], seed["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/approval-policies",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Sandbox"
    assert data[0]["is_builtin"] is True
    assert data[0]["categories"] == {"categories": {"code_changes": "auto"}}


@pytest.mark.asyncio
async def test_lists_builtin_with_no_tid_claim(configured_app, migrations_pg_dsn: str) -> None:
    """A fresh-login token (no tid claim yet) should still see the
    built-in catalog because the table's RLS policy permits
    `is_builtin = true` SELECTs unconditionally."""
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint_token(seed["user_a"], None)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/approval-policies",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert {row["name"] for row in data} == {"Sandbox"}


@pytest.mark.asyncio
async def test_builtin_only_filter(configured_app, migrations_pg_dsn: str) -> None:
    """The `?builtin_only=true` query stays equivalent to the default
    when the only rows are built-ins, but ensures the path through
    the filter still returns a 200."""
    seed = await _seed_db(migrations_pg_dsn)
    token = await _mint_token(seed["user_a"], seed["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app),
        base_url="http://test",
    ) as client:
        resp = await client.get(
            "/approval-policies?builtin_only=true",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    assert len(resp.json()) == 1
