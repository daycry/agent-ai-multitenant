"""Córtex — endpoints ``GET/PUT /owner/cortex/autonomy`` (kill-switch + web, UI).

El owner gestiona desde la UI el kill-switch de los bucles autónomos y el gate
de la web del córtex (``cortex.web_enabled``, ADR 0067 — hasta ahora sin setter
ni UI). PUT parcial: cada campo es opcional; sin ninguno ⇒ 422. Gated
``require_system_owner`` (DB-authoritative).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = pytest.mark.integration


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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_owner(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE platform_settings, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Autonomy Tenant",
            "autonomy-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner, is_system_admin)"
            " VALUES ($1, $2, 'h', true, true)",
            owner_id,
            "owner@autonomy.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=True)


@pytest.mark.asyncio
async def test_put_autonomy_togglea_kill_switch_y_web(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Defaults seguros: todo OFF.
        snapshot = await client.get("/owner/cortex/autonomy", headers=headers)
        assert snapshot.status_code == 200, snapshot.text
        assert snapshot.json()["autonomy_enabled"] is False
        assert snapshot.json()["web_enabled"] is False

        # PUT parcial: solo la web (el kill-switch no cambia).
        resp = await client.put(
            "/owner/cortex/autonomy", json={"web_enabled": True}, headers=headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["web_enabled"] is True
        assert body["autonomy_enabled"] is False

        # PUT parcial: solo el kill-switch (la web se conserva).
        resp = await client.put(
            "/owner/cortex/autonomy", json={"autonomy_enabled": True}, headers=headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["autonomy_enabled"] is True
        assert body["web_enabled"] is True

        # Sin ningún campo ⇒ 422 honesto.
        resp = await client.put("/owner/cortex/autonomy", json={}, headers=headers)
        assert resp.status_code == 422
