"""Integration tests for GET /agents/{fork_id}/diff (task_01_16).

Covers:
  - A fresh fork has an empty diff against its source.
  - Editing the fork's name/prompt shows up in the diff.
  - Editing the source after fork sets `source_moved=true`.
  - Soft-deleting the source sets `source_deleted=true`.
  - Asking for a diff on a non-fork agent returns 400.
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


async def _bump_source(migrations_dsn: str, source_id: UUID) -> None:
    """Simulate the platform updating a built-in: rewrite some fields
    and tick updated_at."""
    conn = await asyncpg.connect(migrations_dsn)
    try:
        await conn.execute(
            "UPDATE agents SET name = $1, system_prompt = $2, updated_at = $3" " WHERE id = $4",
            "Built-in PM v2",
            "Prompt v2.",
            datetime.now(tz=UTC),
            source_id,
        )
    finally:
        await conn.close()


# ===========================================================================
# Tests
# ===========================================================================
@pytest.mark.asyncio
async def test_fresh_fork_has_no_diff(configured_app, migrations_pg_dsn: str) -> None:
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

        diff = await client.get(f"/agents/{fork['id']}/diff", headers=headers)

    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["fields"] == {}
    assert body["source_moved"] is False
    assert body["source_deleted"] is False
    assert body["source_id"] == str(seeded["builtin_agent"])
    assert body["forked_from_version"] == body["source_current_version"]


@pytest.mark.asyncio
async def test_editing_fork_shows_in_diff(configured_app, migrations_pg_dsn: str) -> None:
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
        await client.put(
            f"/agents/{fork['id']}",
            json={"name": "Custom PM", "max_concurrent_tasks": 8},
            headers=headers,
        )

        diff = await client.get(f"/agents/{fork['id']}/diff", headers=headers)

    body = diff.json()
    assert set(body["fields"]) == {"name", "max_concurrent_tasks"}
    assert body["fields"]["name"] == {"fork": "Custom PM", "source": "Built-in PM"}
    assert body["fields"]["max_concurrent_tasks"]["fork"] == 8
    assert body["fields"]["max_concurrent_tasks"]["source"] == 2
    # We didn't touch the source, so it hasn't moved.
    assert body["source_moved"] is False


@pytest.mark.asyncio
async def test_source_moved_flag_when_source_updated(
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

        # Platform ships a refined version of the built-in.
        await _bump_source(migrations_pg_dsn, seeded["builtin_agent"])

        diff = await client.get(f"/agents/{fork['id']}/diff", headers=headers)

    body = diff.json()
    assert body["source_moved"] is True
    # Source changes show up as diff entries (fork stayed put).
    assert "name" in body["fields"]
    assert body["fields"]["name"]["source"] == "Built-in PM v2"
    assert body["fields"]["name"]["fork"] == "Built-in PM"
    assert "system_prompt" in body["fields"]


@pytest.mark.asyncio
async def test_source_deleted_flag(configured_app, migrations_pg_dsn: str) -> None:
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

        # Soft-delete the source via SQL (no admin API to do this in v1).
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "UPDATE agents SET deleted_at = now() WHERE id = $1",
                seeded["builtin_agent"],
            )
        finally:
            await conn.close()

        diff = await client.get(f"/agents/{fork['id']}/diff", headers=headers)

    body = diff.json()
    assert body["source_deleted"] is True


@pytest.mark.asyncio
async def test_diff_on_non_fork_returns_400(configured_app, migrations_pg_dsn: str) -> None:
    """A regular tenant_template agent has forked_from_agent_id=NULL,
    so the diff endpoint should refuse with 400 not 500."""
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
                    "name": "Free-standing",
                    "role": "qa",
                    "system_prompt": "Independent.",
                    "scope": "global_tenant_template",
                },
                headers=headers,
            )
        ).json()

        diff = await client.get(f"/agents/{agent['id']}/diff", headers=headers)

    assert diff.status_code == 400
    assert "not a fork" in diff.text.lower()
