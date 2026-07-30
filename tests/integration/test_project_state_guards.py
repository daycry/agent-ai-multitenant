"""P1-01 (auditoría proyecto 2026-07-17): `paused`/`archived` con efecto real.

Antes, pausar o archivar un proyecto era decorativo: se seguían creando
planes/tareas, arrancando ejecuciones y chateando con el equipo. Ahora:

- POST /plans, POST /tasks, start-execution y el chat del proyecto exigen
  `project.status == active` (409 `project_not_active`).
- Archivar un proyecto cancela sus tareas y runs en vuelo (espejo de la
  cascada del soft-delete).
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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _seed(dsn: str, *, project_status: str = "paused") -> dict[str, UUID]:
    tenant, admin, proj, plan = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, task_dependencies, tasks, plans, conversations, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"ps-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@ps.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, 'P', $3)",
            proj,
            tenant,
            project_status,
        )
        # Un plan approved con una tarea en el spec (para start-execution).
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, 'PL', 'approved',"
            ' \'{"tasks": [{"id": "t1", "title": "algo"}]}\'::jsonb)',
            plan,
            tenant,
            proj,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "proj": proj, "plan": plan}


@pytest.mark.asyncio
async def test_paused_project_rejects_new_plans(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['proj']}/plans", json={"title": "nuevo"}, headers=headers
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "project_not_active"


@pytest.mark.asyncio
async def test_paused_project_rejects_new_tasks(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['proj']}/tasks", json={"title": "nueva"}, headers=headers
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "project_not_active"


@pytest.mark.asyncio
async def test_paused_project_rejects_start_execution(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/plans/{seeded['plan']}/start-execution", headers=headers)
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "project_not_active"


@pytest.mark.asyncio
async def test_active_project_accepts_new_plans(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn, project_status="active")
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['proj']}/plans", json={"title": "nuevo"}, headers=headers
        )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_archiving_project_cancels_open_tasks(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn, project_status="active")
    task_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'viva', 'in_progress', 'medium')",
            task_id,
            seeded["tenant"],
            seeded["proj"],
            seeded["plan"],
        )
    finally:
        await conn.close()

    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/projects/{seeded['proj']}", json={"status": "archived"}, headers=headers
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "archived"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        task_status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", task_id)
    finally:
        await conn.close()
    assert task_status == "cancelled"


@pytest.mark.asyncio
async def test_archived_project_can_be_unarchived_by_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn, project_status="archived")
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/projects/{seeded['proj']}", json={"status": "active"}, headers=headers
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_archived_to_paused_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    """archived es terminal salvo unarchive (→active); archived→paused no existe."""
    seeded = await _seed(migrations_pg_dsn, project_status="archived")
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/projects/{seeded['proj']}", json={"status": "paused"}, headers=headers
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "invalid_project_transition"


@pytest.mark.asyncio
async def test_paused_project_chat_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    """P1-01: el chat del equipo (planning) también se detiene con el proyecto."""
    seeded = await _seed(migrations_pg_dsn)
    conv_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id) VALUES ($1, $2, $3)",
            conv_id,
            seeded["tenant"],
            seeded["proj"],
        )
    finally:
        await conn.close()
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/conversations/{conv_id}/messages",
            json={"author_kind": "user", "content": "hola"},
            headers=headers,
        )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "project_not_active"
