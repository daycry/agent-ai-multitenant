"""Integration: task domain events are published AFTER the DB commit.

Root cause of the orchestrator "consumer se atasca" symptom (sesión
2026-06-18): the api-server published ``task.created`` /
``task.status_changed`` onto ``events:tasks`` from INSIDE the request
handler, i.e. BEFORE ``open_tenant_session`` committed on return. A fast
orchestrator consumes the event and runs ``_dispatch``'s ``SELECT Task``
before the row (or the new status) is committed → it reads ``task is
None`` / the old status and returns ``None`` silently (dispatch.py:285,
the only skip with no log). The task is never dispatched.

These tests pin the ORDERING behaviourally: at the instant the event is
published, a SEPARATE connection (fresh transaction, READ COMMITTED) must
already see the committed row / new status. They fail while the publish
is awaited inline (uncommitted) and pass once it is deferred to after the
request's commit.
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Tenant Commit",
            "tenant-commit",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "carol@commit.test",
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
            "Commit Project",
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


@pytest.mark.asyncio
async def test_task_created_event_published_after_commit(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At publish time, a separate connection must already see the row."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    import api_server.routers.tasks as tasks_router

    observed: dict[str, bool] = {}
    real_publish = tasks_router.publish_task_created

    async def spy(redis, task):  # type: ignore[no-untyped-def]
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow("SELECT id FROM tasks WHERE id = $1", task.id)
        finally:
            await conn.close()
        observed["committed_at_publish"] = row is not None
        await real_publish(redis, task)

    monkeypatch.setattr(tasks_router, "publish_task_created", spy)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['project_id']}/tasks",
            json={"title": "Commit-ordering task", "status": "ready"},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text

    assert observed.get("committed_at_publish") is True, (
        "task.created was published BEFORE the row was committed — a fast "
        "orchestrator would read task is None and silently skip the dispatch"
    )


@pytest.mark.asyncio
async def test_status_changed_event_published_after_commit(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At publish time, a separate connection must already see the NEW status."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    import api_server.routers.tasks as tasks_router

    observed: dict[str, str | None] = {}
    real_publish = tasks_router.publish_task_status_changed

    async def spy(redis, task, *, old_status, new_status):  # type: ignore[no-untyped-def]
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow("SELECT status FROM tasks WHERE id = $1", task.id)
        finally:
            await conn.close()
        observed["status_seen"] = row["status"] if row else None
        observed["new_status"] = new_status
        await real_publish(redis, task, old_status=old_status, new_status=new_status)

    monkeypatch.setattr(tasks_router, "publish_task_status_changed", spy)

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            f"/projects/{seeded['project_id']}/tasks",
            json={"title": "Status task", "status": "backlog"},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        moved = await client.put(
            f"/projects/{seeded['project_id']}/tasks/{task_id}",
            json={"status": "ready"},
            headers=headers,
        )
        assert moved.status_code == 200, moved.text

    assert observed.get("status_seen") == observed.get("new_status") == "ready", (
        "task.status_changed was published BEFORE the new status was committed — "
        f"a separate connection still saw {observed.get('status_seen')!r}"
    )
