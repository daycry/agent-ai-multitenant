"""Integration tests for DAG enforcement on Kanban transitions
(Plan 03 task_03_30).

After plan→Kanban sync the dependency graph lives in
``task_dependencies``. The PUT /tasks/{id} endpoint must refuse to
move a card to a "starts-work" status (in_progress, awaiting_human_approval,
in_review) while any upstream dependency is still not ``done``.
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


_PLAN_SPEC = {
    "tasks": [
        {"id": "a", "title": "Design", "complexity": "m"},
        {"id": "b", "title": "Build", "complexity": "l", "depends_on": ["a"]},
    ],
}


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, tasks, plan_comments, plans, conversations,"
            " projects, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant DAG",
            "tenant-dag",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-dag",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@dag.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, $4)",
            uuid4(),
            tenant_id,
            user_id,
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "DAG Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


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


async def _bootstrap(client: AsyncClient, project_id: UUID, headers: dict) -> dict[str, str]:
    """Create plan + sync. Returns spec_id -> task UUID mapping."""
    plan = await client.post(
        f"/projects/{project_id}/plans",
        json={"title": "DAG plan", "specification": _PLAN_SPEC},
        headers=headers,
    )
    plan_id = plan.json()["id"]
    sync = await client.post(
        f"/plans/{plan_id}/sync-to-kanban", json={"scope": "total"}, headers=headers
    )
    assert sync.status_code == 200, sync.text
    return sync.json()["created_task_ids"]


@pytest.mark.asyncio
async def test_starting_a_task_with_pending_dependency_returns_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        ids = await _bootstrap(client, seeded["project_id"], headers)
        task_b = ids["b"]

        # b depends on a; a is still in backlog → 422.
        resp = await client.put(
            f"/projects/{seeded['project_id']}/tasks/{task_b}",
            json={"status": "in_progress"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "dependencies_not_done"
        assert detail["target_status"] == "in_progress"
        assert len(detail["pending"]) == 1
        assert detail["pending"][0]["task_id"] == ids["a"]
        assert detail["pending"][0]["status"] == "backlog"


@pytest.mark.asyncio
async def test_starting_a_task_succeeds_once_dependency_is_done(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        ids = await _bootstrap(client, seeded["project_id"], headers)
        task_a = ids["a"]
        task_b = ids["b"]

        # Mark a done (no dependencies so it's free to move).
        done = await client.put(
            f"/projects/{seeded['project_id']}/tasks/{task_a}",
            json={"status": "done"},
            headers=headers,
        )
        assert done.status_code == 200, done.text

        # Now b can move to in_progress.
        ok = await client.put(
            f"/projects/{seeded['project_id']}/tasks/{task_b}",
            json={"status": "in_progress"},
            headers=headers,
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_non_gated_transitions_are_not_blocked(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Moving to ``blocked`` or ``cancelled`` does not consume agent
    time, so the DAG guard ignores those targets."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        ids = await _bootstrap(client, seeded["project_id"], headers)
        task_b = ids["b"]

        cancel = await client.put(
            f"/projects/{seeded['project_id']}/tasks/{task_b}",
            json={"status": "cancelled"},
            headers=headers,
        )
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_awaiting_human_approval_is_gated_too(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        ids = await _bootstrap(client, seeded["project_id"], headers)
        task_b = ids["b"]

        resp = await client.put(
            f"/projects/{seeded['project_id']}/tasks/{task_b}",
            json={"status": "awaiting_human_approval"},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "dependencies_not_done"
        assert resp.json()["detail"]["target_status"] == "awaiting_human_approval"
