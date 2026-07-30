"""Integration test: POST /projects/{id}/git/sync (cadena-pr T6 / P5).

The dedicated "Sincronizar" action enqueues a re-sync (clone_project_repo =
ensure_repo + git fetch --prune) for a project that HAS a git remote, and 400s
for one without. Harness mirrors test_project_command_config.py.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    user_a = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-gitsync",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-gitsync",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_a,
            "alice@gitsync.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_a,
            user_a,
        )
    finally:
        await conn.close()
    return {"tenant_a": tenant_a, "user_a": user_a}


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


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_sync_enqueues_for_project_with_git(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    calls: list[str] = []

    async def _fake_enqueue(project_id: UUID) -> bool:
        calls.append(str(project_id))
        return True

    monkeypatch.setattr("api_server.routers.projects.enqueue_clone_project_repo", _fake_enqueue)

    async with _client(configured_app) as client:
        resp = await client.post("/projects", json={"name": "Api CI"}, headers=headers)
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["id"]
        git = await client.put(
            f"/projects/{project_id}/git",
            json={"remote_url": "https://example.test/owner/repo.git"},
            headers=headers,
        )
        assert git.status_code == 200, git.text
        calls.clear()  # ignore the PUT's enqueue — assert on the dedicated sync
        sync = await client.post(f"/projects/{project_id}/git/sync", headers=headers)

    assert sync.status_code == 202, sync.text
    assert sync.json()["status"] == "enqueued"
    assert calls == [project_id]


@pytest.mark.asyncio
async def test_sync_400_without_git_config(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/projects", json={"name": "No Git"}, headers=headers)
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["id"]
        sync = await client.post(f"/projects/{project_id}/git/sync", headers=headers)

    assert sync.status_code == 400, sync.text
