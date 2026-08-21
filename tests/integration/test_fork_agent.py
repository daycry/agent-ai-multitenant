"""Integration tests for POST /agents/{id}/fork (task_01_15).

Covers:
  - Forking a global_builtin into a tenant project yields a project_local
    copy with forked_from_agent_id pointing to the source.
  - forked_from_version captures the source's updated_at ISO string.
  - Optional name / system_prompt overrides apply at creation.
  - The new fork is fully editable (PUT works) and editing it does NOT
    mutate the source.
  - Forking to a project the tenant doesn't own returns 404.
  - Forking a non-existent source returns 404.
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed: two tenants + one project each + a global_builtin source agent.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()
    builtin_agent = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agents, projects, team_members, teams,"
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
            "a@x.test",
            "x",
            user_b,
            "b@x.test",
            "x",
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
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3), ($4, $5, $6)",
            project_a,
            tenant_a,
            "A Project",
            project_b,
            tenant_b,
            "B Project",
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt,"
            " model_config, scope, project_id)"
            " VALUES ($1, $2, $3, 'project_manager', $4, $5::jsonb,"
            " 'global_builtin', NULL)",
            builtin_agent,
            _PLATFORM_TENANT_ID,
            "Built-in PM",
            "You are a project manager.",
            '{"provider": "anthropic", "model": "claude-sonnet"}',
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "user_a": user_a,
        "user_b": user_b,
        "project_a": project_a,
        "project_b": project_b,
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


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_fork_builtin_into_project(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/agents/{seeded['builtin_agent']}/fork",
            json={"project_id": str(seeded["project_a"])},
            headers=headers,
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["scope"] == "project_local"
    assert body["project_id"] == str(seeded["project_a"])
    assert UUID(body["tenant_id"]) == seeded["tenant_a"]
    assert body["forked_from_agent_id"] == str(seeded["builtin_agent"])
    # forked_from_version is the source's updated_at as ISO string.
    assert body["forked_from_version"]
    assert "T" in body["forked_from_version"], "expected ISO timestamp"
    # Inherited fields:
    assert body["name"] == "Built-in PM"
    assert body["system_prompt"] == "You are a project manager."
    # is_template defaults to False on forks.
    assert body["is_template"] is False


@pytest.mark.asyncio
async def test_fork_with_overrides(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/agents/{seeded['builtin_agent']}/fork",
            json={
                "project_id": str(seeded["project_a"]),
                "name": "Project A's PM",
                "system_prompt": "You handle Project A specifically.",
            },
            headers=headers,
        )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Project A's PM"
    assert body["system_prompt"] == "You handle Project A specifically."


@pytest.mark.asyncio
async def test_fork_to_other_tenants_project_returns_404(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token_a = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token_a}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/agents/{seeded['builtin_agent']}/fork",
            json={"project_id": str(seeded["project_b"])},
            headers=headers,
        )

    assert resp.status_code == 404
    assert "project not found" in resp.text.lower()


@pytest.mark.asyncio
async def test_fork_unknown_source_returns_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/agents/{uuid4()}/fork",
            json={"project_id": str(seeded["project_a"])},
            headers=headers,
        )
    assert resp.status_code == 404
    assert "source agent not found" in resp.text.lower()


@pytest.mark.asyncio
async def test_editing_fork_does_not_mutate_source(configured_app, migrations_pg_dsn: str) -> None:
    """The core linked-vs-forked invariant: a fork is an independent
    row, so PUT on it never reaches back to the source."""
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

        upd = await client.put(
            f"/agents/{fork['id']}",
            json={"name": "Mutated", "system_prompt": "Mutated prompt."},
            headers=headers,
        )
        assert upd.status_code == 200
        assert upd.json()["name"] == "Mutated"

        # Source still has its original values.
        source = await client.get(f"/agents/{seeded['builtin_agent']}", headers=headers)
        assert source.status_code == 200
        assert source.json()["name"] == "Built-in PM"
        assert source.json()["system_prompt"] == "You are a project manager."
