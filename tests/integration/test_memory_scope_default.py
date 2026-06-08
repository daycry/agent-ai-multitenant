"""Default de ``memory_scope`` operator-configurable (Plan 06.17 task_06_17_04).

Antes de esta tarea ``AgentCreateRequest.memory_scope`` defaulteaba SIEMPRE a
``private`` (``domain.py`` ``server_default 'private'`` + el schema), de modo que
un agente IA creado por UI sin elegir scope nacía ``private`` y el Memorizer no
memorizaba nada en silencio. Aquí se verifica contra Postgres real que:

  * cuando el ``POST /agents`` NO envía ``model_config``/``memory_scope``, el
    scope del agente creado se lee del platform setting
    ``memory.default_scope`` (operator-configurable), NO hardcode ``private``;
  * el default sigue siendo ``private`` cuando el operador no lo ha cambiado
    (backward-compat: no rompe agentes existentes ni el comportamiento previo);
  * un ``memory_scope`` EXPLÍCITO en el body gana sobre el default de plataforma.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


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


async def _seed_admin(dsn: str) -> dict[str, UUID]:
    """Tenant + proyecto + un usuario tenant_admin (puede crear agentes)."""
    tenant_id = uuid4()
    project_id = uuid4()
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE platform_settings, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant Scope",
            "tenant-scope-default",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "admin@scope-default.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Scope Default Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "project_id": project_id, "user_id": user_id}


async def _set_setting(dsn: str, key: str, value: Any, *, updated_by: UUID) -> None:
    import json

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value, updated_by)"
            " VALUES ($1, $2::jsonb, $3)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            json.dumps(value),
            updated_by,
        )
    finally:
        await conn.close()


async def _mint_user_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _create_agent(app: Any, token: str, body: dict[str, Any]) -> Any:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/agents", json=body, headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# 1. sin override → default sigue siendo 'private' (backward-compat)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_scope_is_private_without_override(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed_admin(migrations_pg_dsn)
    token = await _mint_user_token(seeded["user_id"], seeded["tenant_id"])

    resp = await _create_agent(
        configured_app,
        token,
        {
            "name": "Default Scope Agent",
            "role": "backend_dev",
            "system_prompt": "You are a backend dev.",
            "scope": "project_local",
            "project_id": str(seeded["project_id"]),
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["memory_scope"] == "private"


# ---------------------------------------------------------------------------
# 2. con override 'project_shared' → el agente nace con ese scope (no private)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_scope_reads_platform_setting(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed_admin(migrations_pg_dsn)
    await _set_setting(
        migrations_pg_dsn,
        "memory.default_scope",
        "project_shared",
        updated_by=seeded["user_id"],
    )
    token = await _mint_user_token(seeded["user_id"], seeded["tenant_id"])

    resp = await _create_agent(
        configured_app,
        token,
        {
            "name": "Override Scope Agent",
            "role": "backend_dev",
            "system_prompt": "You are a backend dev.",
            "scope": "project_local",
            "project_id": str(seeded["project_id"]),
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["memory_scope"] == "project_shared", resp.text


# ---------------------------------------------------------------------------
# 3. un memory_scope EXPLÍCITO gana sobre el default de plataforma
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_explicit_scope_overrides_default(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed_admin(migrations_pg_dsn)
    await _set_setting(
        migrations_pg_dsn,
        "memory.default_scope",
        "project_shared",
        updated_by=seeded["user_id"],
    )
    token = await _mint_user_token(seeded["user_id"], seeded["tenant_id"])

    resp = await _create_agent(
        configured_app,
        token,
        {
            "name": "Explicit Scope Agent",
            "role": "backend_dev",
            "system_prompt": "You are a backend dev.",
            "memory_scope": "global",
            "scope": "project_local",
            "project_id": str(seeded["project_id"]),
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["memory_scope"] == "global", resp.text
