"""PROY2-11 + PROY2-04 (auditoría proyecto 2026-07-17): gates de contenido del
plan.

- Un plan sin NINGUNA tarea no puede aprobarse ni arrancarse: sería un plan
  vacío que se marca en curso y el reconciler lo rebota a
  pending_human_validation al instante (todas sus 0 tareas están "done").
- accept-corrections valida el DAG del spec resultante: el LLM de correcciones
  puede emitir una tanda cíclica.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant, admin, proj = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"pg-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@pg.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, 'P', 'active')",
            proj,
            tenant,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "proj": proj}


async def _make_plan(dsn: str, seeded: dict[str, UUID], status: str, spec: str) -> UUID:
    plan_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, 'P', $4, $5::jsonb)",
            plan_id,
            seeded["tenant"],
            seeded["proj"],
            status,
            spec,
        )
    finally:
        await conn.close()
    return plan_id


@pytest.mark.asyncio
async def test_approve_plan_without_tasks_is_rejected(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    plan_id = await _make_plan(migrations_pg_dsn, seeded, "pending_approval", "{}")
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/plans/{plan_id}/approve", headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "plan_has_no_tasks"


@pytest.mark.asyncio
async def test_start_execution_plan_without_tasks_is_rejected(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    plan_id = await _make_plan(migrations_pg_dsn, seeded, "approved", "{}")
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/plans/{plan_id}/start-execution", headers=headers)
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "plan_has_no_tasks"


@pytest.mark.asyncio
async def test_accept_corrections_with_cyclic_spec_is_rejected(
    configured_app, migrations_pg_dsn: str
) -> None:
    """PROY2-04: el LLM de correcciones puede emitir una tanda cíclica; el DAG
    del spec resultante debe validarse antes de materializar."""
    seeded = await _seed(migrations_pg_dsn)
    spec = (
        '{"tasks": ['
        '{"id": "t1", "title": "uno", "depends_on": ["t2"], "origin": "correction"},'
        '{"id": "t2", "title": "dos", "depends_on": ["t1"], "origin": "correction"}'
        '], "corrections": ['
        '{"session_id": "s", "status": "proposed", "task_ids": ["t1", "t2"]}'
        "]}"
    )
    plan_id = await _make_plan(migrations_pg_dsn, seeded, "rejected", spec)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/plans/{plan_id}/accept-corrections",
            json={"task_ids": ["t1", "t2"]},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "dag_cycle"


@pytest.mark.asyncio
async def test_approve_plan_with_a_task_succeeds(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    spec = '{"tasks": [{"id": "t1", "title": "hacer algo", "depends_on": []}]}'
    plan_id = await _make_plan(migrations_pg_dsn, seeded, "pending_approval", spec)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/plans/{plan_id}/approve", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "approved"
