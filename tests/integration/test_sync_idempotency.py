"""Integration tests for sync idempotency (Plan 03 task_03_29).

Calling `POST /plans/{id}/sync-to-kanban` twice with the same scope
must not duplicate Kanban tasks or dependency rows. Re-syncing with a
wider scope must materialise only the missing siblings and still wire
their dependencies to the already-existing tasks.
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
    "phases": [
        {"name": "Design", "tasks": ["t1", "t2"]},
        {"name": "Build", "tasks": ["t3"]},
    ],
    "tasks": [
        {"id": "t1", "title": "Modelar", "complexity": "m"},
        {"id": "t2", "title": "API", "complexity": "m", "depends_on": ["t1"]},
        {"id": "t3", "title": "Backend", "complexity": "l", "depends_on": ["t2"]},
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
            "Tenant Idemp",
            "tenant-idemp",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-idemp",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@idemp.test",
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
            "Idemp Project",
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


async def _create_plan(client: AsyncClient, project_id: UUID, headers: dict) -> str:
    create = await client.post(
        f"/projects/{project_id}/plans",
        json={"title": "Idemp plan", "specification": _PLAN_SPEC},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    plan_id = create.json()["id"]
    # Lifecycle: only an APPROVED plan may sync to the Kanban; approve before syncing.
    moved = await client.put(
        f"/plans/{plan_id}", json={"status": "pending_approval"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    approved = await client.post(f"/plans/{plan_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text
    return plan_id


@pytest.mark.asyncio
async def test_repeated_total_sync_is_a_no_op(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        first = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "total"},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        assert set(first.json()["created_task_ids"]) == {"t1", "t2", "t3"}
        original_ids = first.json()["created_task_ids"]

        second = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "total"},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        # Nothing new created; existing rows reported as skipped with
        # the same DB ids as the first run.
        assert body["created_task_ids"] == {}
        assert body["skipped_task_ids"] == original_ids
        assert body["dependencies_created"] == 0

        # Confirm the Kanban still has exactly three tasks.
        tasks = await client.get(
            f"/projects/{seeded['project_id']}/tasks?plan_id={plan_id}", headers=headers
        )
        assert len({t["id"] for t in tasks.json()}) == 3


@pytest.mark.asyncio
async def test_widening_scope_adds_only_missing_tasks_and_wires_deps(
    configured_app, migrations_pg_dsn: str
) -> None:
    """First sync phase 0 (t1, t2). Then total sync materialises only
    t3 — and wires its t2→ dependency to the existing t2 row."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        first = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "phase", "phase_index": 0},
            headers=headers,
        )
        assert first.status_code == 200, first.text
        t2_id_first = first.json()["created_task_ids"]["t2"]

        second = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "total"},
            headers=headers,
        )
        assert second.status_code == 200, second.text
        body = second.json()
        assert set(body["created_task_ids"]) == {"t3"}
        # t1 and t2 must surface as skipped with the same UUIDs.
        assert set(body["skipped_task_ids"]) == {"t1", "t2"}
        assert body["skipped_task_ids"]["t2"] == t2_id_first
        # One new junction row: t3 -> t2 (existing).
        assert body["dependencies_created"] == 1

        # Verify the wiring: t3's depends_on contains the original t2 id.
        tasks = await client.get(
            f"/projects/{seeded['project_id']}/tasks?plan_id={plan_id}", headers=headers
        )
        by_spec = {t["inputs"]["plan_task_spec_id"]: t for t in tasks.json()}
        assert by_spec["t3"]["depends_on"] == [by_spec["t2"]["id"]]
