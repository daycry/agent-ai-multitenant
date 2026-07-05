"""ciclo-vida T7c (c3): the `retry` human action un-sticks a blocked task.

POST /tasks/{id}/human-action {action: retry} on a blocked task: the task moves to
`ready` (backlog if a dependency is pending), its retry_count resets to 0, and its
plan is reactivated blocked→in_progress so the promoter dispatches it again.
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
    ids = {
        "tenant": uuid4(),
        "user": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "task": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-retry'),"
            " ($2, 'Platform', 'platform-retry')",
            ids["tenant"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@retry.test', 'x')",
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["user"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status, is_template)"
            " VALUES ($1, $2, 'P', 'p', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, slug, status)"
            " VALUES ($1, $2, $3, 'Plan', 'plan', 'blocked')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority,"
            " acceptance_criteria, inputs, retry_count, max_retries)"
            " VALUES ($1, $2, $3, $4, 'T', 'blocked', 'medium', '[]'::jsonb, '{}'::jsonb, 2, 3)",
            ids["task"],
            ids["tenant"],
            ids["project"],
            ids["plan"],
        )
    finally:
        await conn.close()
    return ids


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
async def test_retry_unsticks_blocked_task_and_reactivates_plan(
    configured_app, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _mint_token(ids["user"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/tasks/{ids['task']}/human-action",
            json={"action": "retry"},
            headers=headers,
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()["task"]
    # Task un-stuck to ready (no deps) with the retry budget reset.
    assert body["status"] == "ready"
    assert body["retry_count"] == 0

    # The plan was reactivated blocked → in_progress so the promoter dispatches again.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        plan_status = await conn.fetchval("SELECT status FROM plans WHERE id = $1", ids["plan"])
        task_status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", ids["task"])
    finally:
        await conn.close()
    assert plan_status == "in_progress"
    assert task_status == "ready"


@pytest.mark.asyncio
async def test_plan_unblock_reactivates_and_reenqueues_blocked_tasks(
    configured_app, migrations_pg_dsn: str
) -> None:
    """T7c part D: POST /plans/{id}/unblock reactivates the plan and re-enqueues ALL
    its blocked tasks in one gesture."""
    ids = await _seed(migrations_pg_dsn)  # plan blocked + 1 blocked task (retry_count=2)
    token = await _mint_token(ids["user"], ids["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/plans/{ids['plan']}/unblock", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "in_progress"
    assert body["tasks_retried"] == 1

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        plan_status = await conn.fetchval("SELECT status FROM plans WHERE id = $1", ids["plan"])
        row = await conn.fetchrow(
            "SELECT status, retry_count FROM tasks WHERE id = $1", ids["task"]
        )
    finally:
        await conn.close()
    assert plan_status == "in_progress"
    assert row["status"] == "ready"
    assert row["retry_count"] == 0
