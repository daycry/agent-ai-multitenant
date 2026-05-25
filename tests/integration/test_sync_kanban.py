"""Integration tests for `POST /plans/{id}/sync-to-kanban`
(Plan 03 task_03_27 + task_03_28).

Drives the three sync scopes (``total``, ``phase``, ``selection``)
end-to-end and asserts the materialised ``tasks`` + ``task_dependencies``
rows. Idempotency is covered in :mod:`test_sync_idempotency`.
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


# A three-phase, six-task plan with a deliberate dependency chain so we
# can prove the junction rows match the spec exactly:
#
#   t1 ── t2 ── t3
#               │
#               └── t6
#   t4 ── t5
_PLAN_SPEC = {
    "phases": [
        {"name": "Diseño", "tasks": ["t1", "t2"]},
        {"name": "Build", "tasks": ["t3", "t4", "t5"]},
        {"name": "QA", "tasks": ["t6"]},
    ],
    "tasks": [
        {"id": "t1", "title": "Modelar", "complexity": "m"},
        {"id": "t2", "title": "Diseñar API", "complexity": "m", "depends_on": ["t1"]},
        {"id": "t3", "title": "Implementar backend", "complexity": "l", "depends_on": ["t2"]},
        {"id": "t4", "title": "Implementar frontend", "complexity": "l"},
        {"id": "t5", "title": "Integrar", "complexity": "m", "depends_on": ["t4"]},
        {"id": "t6", "title": "QA full", "complexity": "s", "depends_on": ["t3", "t5"]},
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
            "Tenant Sync",
            "tenant-sync",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-sync",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@sync.test",
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
            "Sync Project",
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
        json={"title": "Sync plan", "specification": _PLAN_SPEC},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    return create.json()["id"]


@pytest.mark.asyncio
async def test_sync_total_materialises_every_task_with_dependencies(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        resp = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "total"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["created_task_ids"]) == {"t1", "t2", "t3", "t4", "t5", "t6"}
        assert body["skipped_task_ids"] == {}
        # 1 (t2->t1) + 1 (t3->t2) + 1 (t5->t4) + 2 (t6->t3,t5) = 5
        assert body["dependencies_created"] == 5

        # Kanban now reflects six tasks, all in backlog, all tagged.
        tasks_resp = await client.get(
            f"/projects/{seeded['project_id']}/tasks?plan_id={plan_id}", headers=headers
        )
        assert tasks_resp.status_code == 200, tasks_resp.text
        tasks = tasks_resp.json()
        assert len(tasks) == 6
        assert {t["status"] for t in tasks} == {"backlog"}
        by_spec = {t["inputs"]["plan_task_spec_id"]: t for t in tasks}
        assert set(by_spec) == {"t1", "t2", "t3", "t4", "t5", "t6"}

        # Dependencies wired correctly.
        t6 = by_spec["t6"]
        assert set(t6["depends_on"]) == {by_spec["t3"]["id"], by_spec["t5"]["id"]}
        assert by_spec["t1"]["depends_on"] == []


@pytest.mark.asyncio
async def test_sync_phase_only_materialises_phase_tasks(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        # Phase 0: "Diseño" → t1, t2 (t2 depends on t1).
        resp = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "phase", "phase_index": 0},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert set(body["created_task_ids"]) == {"t1", "t2"}
        assert body["dependencies_created"] == 1


@pytest.mark.asyncio
async def test_sync_selection_only_materialises_listed_ids(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        resp = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "selection", "task_ids": ["t1", "t4"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Neither t1 nor t4 has dependencies, so no junction rows.
        assert set(body["created_task_ids"]) == {"t1", "t4"}
        assert body["dependencies_created"] == 0


@pytest.mark.asyncio
async def test_sync_invalid_scope_returns_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        # Phase index out of range.
        oor = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "phase", "phase_index": 99},
            headers=headers,
        )
        assert oor.status_code == 422, oor.text
        assert oor.json()["detail"]["error"] == "invalid_sync_scope"

        # Selection with unknown ids.
        unknown = await client.post(
            f"/plans/{plan_id}/sync-to-kanban",
            json={"scope": "selection", "task_ids": ["t999"]},
            headers=headers,
        )
        assert unknown.status_code == 422, unknown.text
        assert unknown.json()["detail"]["error"] == "invalid_sync_scope"
