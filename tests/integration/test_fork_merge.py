"""Integration tests for POST /agents/{fork_id}/merge (task_01_17).

Covers selective absorption of upstream changes:
  - Merge a single field; other diverged fields stay diverged.
  - Merge advances forked_from_version, so a subsequent diff drops the
    merged fields and `source_moved` flips back to false.
  - Merging unknown fields returns 400.
  - Merge on a non-fork agent returns 400.
  - Merge when source has been soft-deleted returns 409.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    user_a = uuid4()
    project_a = uuid4()
    builtin_agent = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agents, projects, team_members, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
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
            "a@x.test",
            "x",
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
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_a,
            tenant_a,
            "A Project",
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
            " model_config, scope, project_id, max_concurrent_tasks)"
            " VALUES ($1, $2, $3, 'project_manager', $4, '{}'::jsonb,"
            " 'global_builtin', NULL, 2)",
            builtin_agent,
            _PLATFORM_TENANT_ID,
            "Built-in PM",
            "Prompt v1.",
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "user_a": user_a,
        "project_a": project_a,
        "builtin_agent": builtin_agent,
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


async def _bump_source(dsn: str, source_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE agents SET name = $1, system_prompt = $2, max_concurrent_tasks = $3,"
            " updated_at = $4 WHERE id = $5",
            "Built-in PM v2",
            "Prompt v2.",
            5,
            datetime.now(tz=UTC),
            source_id,
        )
    finally:
        await conn.close()


async def _soft_delete_source(dsn: str, source_id: UUID) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("UPDATE agents SET deleted_at = now() WHERE id = $1", source_id)
    finally:
        await conn.close()


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_selective_merge_only_touches_requested_fields(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

        # Tenant tweaks the fork locally first.
        await client.put(
            f"/agents/{fork['id']}",
            json={"name": "Custom PM"},
            headers=headers,
        )

        # Platform ships an upstream improvement.
        await _bump_source(migrations_pg_dsn, seeded["builtin_agent"])

        # Merge only `system_prompt`. Name and max_concurrent_tasks
        # stay where the fork left them.
        merged = await client.post(
            f"/agents/{fork['id']}/merge",
            json={"fields": ["system_prompt"]},
            headers=headers,
        )

    assert merged.status_code == 200, merged.text
    body = merged.json()
    assert body["system_prompt"] == "Prompt v2."
    assert body["name"] == "Custom PM"  # tenant edit preserved
    assert body["max_concurrent_tasks"] == 2  # original source value


@pytest.mark.asyncio
async def test_merge_advances_fork_version(configured_app, migrations_pg_dsn: str) -> None:
    """After merging everything that differs, the diff should consider
    `source_moved=false` because forked_from_version was re-anchored."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

        await _bump_source(migrations_pg_dsn, seeded["builtin_agent"])

        # Absorb every diverged field that came from upstream.
        await client.post(
            f"/agents/{fork['id']}/merge",
            json={"fields": ["name", "system_prompt", "max_concurrent_tasks"]},
            headers=headers,
        )

        diff = (await client.get(f"/agents/{fork['id']}/diff", headers=headers)).json()

    assert diff["source_moved"] is False
    assert diff["fields"] == {}


@pytest.mark.asyncio
async def test_merge_unknown_field_returns_400(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

        resp = await client.post(
            f"/agents/{fork['id']}/merge",
            json={"fields": ["tenant_id", "id"]},  # not mergeable
            headers=headers,
        )

    assert resp.status_code == 400
    assert "non-mergeable" in resp.text.lower() or "unknown" in resp.text.lower()


@pytest.mark.asyncio
async def test_merge_on_non_fork_returns_400(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        agent = (
            await client.post(
                "/agents",
                json={
                    "name": "Standalone",
                    "role": "qa",
                    "system_prompt": "x",
                    "scope": "global_tenant_template",
                },
                headers=headers,
            )
        ).json()

        resp = await client.post(
            f"/agents/{agent['id']}/merge",
            json={"fields": ["name"]},
            headers=headers,
        )
    assert resp.status_code == 400
    assert "not a fork" in resp.text.lower()


@pytest.mark.asyncio
async def test_merge_with_deleted_source_returns_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()

        await _soft_delete_source(migrations_pg_dsn, seeded["builtin_agent"])

        resp = await client.post(
            f"/agents/{fork['id']}/merge",
            json={"fields": ["system_prompt"]},
            headers=headers,
        )
    assert resp.status_code == 409
    assert "source" in resp.text.lower()


# ------------------------------------------------------------------ task_cv_33
# Auditoría 2026-09-01 (F-03): una migración cambia las tools de un built-in
# sin tocar texto, y la copia adoptada se queda con las de antes. `merge`
# acepta ahora `capabilities: ["tools", "skills"]` y el fork absorbe las
# capacidades ACTUALES del origen.


async def _seed_tools_and_grant(dsn: str, agent_id: UUID) -> UUID:
    """Siembra el catálogo de tools y asigna `read-file` al agente. Devuelve el id de la tool."""
    from api_server.seeds.builtin_tools import seed_builtin_tools
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    sa_dsn = dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )
    engine = create_async_engine(sa_dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_tools(session)
    finally:
        await engine.dispose()
    conn = await asyncpg.connect(dsn)
    try:
        # `tools` identifica por `name` (índice único tenant+name); la fila
        # builtin nace en el tenant de plataforma. `agent_tools` es tenant-scoped:
        # el tenant sale del propio agente.
        tool_id = await conn.fetchval(
            "SELECT id FROM tools WHERE name = 'read_file' AND is_builtin = true"
            " AND deleted_at IS NULL ORDER BY created_at LIMIT 1"
        )
        assert tool_id is not None
        await conn.execute(
            "INSERT INTO agent_tools (tenant_id, agent_id, tool_id)"
            " SELECT tenant_id, id, $2 FROM agents WHERE id = $1"
            " ON CONFLICT DO NOTHING",
            agent_id,
            tool_id,
        )
        return UUID(str(tool_id))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_merge_capabilities_absorbs_the_sources_current_tools(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()
        # El origen gana una tool DESPUÉS del fork (lo que hace una migración):
        tool_id = await _seed_tools_and_grant(migrations_pg_dsn, seeded["builtin_agent"])
        merged = await client.post(
            f"/agents/{fork['id']}/merge",
            json={"capabilities": ["tools"]},
            headers=headers,
        )
    assert merged.status_code == 200, merged.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT tool_id FROM agent_tools WHERE agent_id = $1", UUID(fork["id"])
        )
    finally:
        await conn.close()
    assert tool_id in {UUID(str(r["tool_id"])) for r in rows}, (
        "el fork no absorbió la tool que el origen ganó después del fork"
    )


@pytest.mark.asyncio
async def test_merge_with_nothing_to_merge_returns_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        fork = (
            await client.post(
                f"/agents/{seeded['builtin_agent']}/fork",
                json={"project_id": str(seeded["project_a"])},
                headers=headers,
            )
        ).json()
        resp = await client.post(f"/agents/{fork['id']}/merge", json={}, headers=headers)
    assert resp.status_code == 422, resp.text
