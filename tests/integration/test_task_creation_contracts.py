"""PROY2-03/13 + P1-06/07 (auditoría proyecto 2026-07-17): contratos de
nacimiento de tareas y borrado de plan.

- POST /tasks no puede nacer en un estado avanzado ni colgar de un plan de
  OTRO proyecto (el FK bypassea RLS).
- create_free_task exige un plan no terminal.
- DELETE /plans cancela tareas + runs en vuelo (no deja trabajo despachándose
  invisible) — su verificación fina vive en el test de cascada existente; aquí
  fijamos que el borrado marca el plan cancelado.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant, admin = uuid4(), uuid4()
    proj_a, proj_b = uuid4(), uuid4()
    plan_b = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"tc-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@tc.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'A'), ($3, $2, 'B')",
            proj_a,
            tenant,
            proj_b,
        )
        # Un plan que vive en el proyecto B.
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, 'PB', 'draft', '{}'::jsonb)",
            plan_b,
            tenant,
            proj_b,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "proj_a": proj_a, "proj_b": proj_b, "plan_b": plan_b}


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_create_task_rejects_advanced_initial_status(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['proj_a']}/tasks",
            json={"title": "Trampa", "status": "done"},
            headers=headers,
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_initial_task_status"


@pytest.mark.asyncio
async def test_create_task_rejects_plan_from_another_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Tarea en proyecto A colgando de un plan del proyecto B → 422.
        resp = await client.post(
            f"/projects/{seeded['proj_a']}/tasks",
            json={"title": "cruzada", "plan_id": str(seeded["plan_b"])},
            headers=headers,
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "plan_not_in_project"


@pytest.mark.asyncio
async def test_create_task_accepts_valid_plan_in_same_project(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Un plan del proyecto B + tarea en proyecto B → OK.
        ok = await client.post(
            f"/projects/{seeded['proj_b']}/tasks",
            json={"title": "buena", "plan_id": str(seeded["plan_b"])},
            headers=headers,
        )
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "backlog"


@pytest.mark.asyncio
async def test_dependency_cycle_across_two_puts_is_rejected(
    configured_app, migrations_pg_dsn: str
) -> None:
    """PROY2-04: un ciclo puede construirse en dos PUT (A→B, luego B→A); el
    validador por-request del spec no lo ve, pero _set_dependencies sí."""
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        a = await client.post(
            f"/projects/{seeded['proj_b']}/tasks",
            json={"title": "A", "plan_id": str(seeded["plan_b"])},
            headers=headers,
        )
        b = await client.post(
            f"/projects/{seeded['proj_b']}/tasks",
            json={"title": "B", "plan_id": str(seeded["plan_b"])},
            headers=headers,
        )
        a_id, b_id = a.json()["id"], b.json()["id"]
        # A depende de B — OK.
        r1 = await client.put(
            f"/projects/{seeded['proj_b']}/tasks/{a_id}",
            json={"depends_on": [b_id]},
            headers=headers,
        )
        assert r1.status_code == 200, r1.text
        # B depende de A — cerraría el ciclo → 422.
        r2 = await client.put(
            f"/projects/{seeded['proj_b']}/tasks/{b_id}",
            json={"depends_on": [a_id]},
            headers=headers,
        )
    assert r2.status_code == 422, r2.text
    assert r2.json()["detail"]["error"] == "dag_cycle"


@pytest.mark.asyncio
async def test_cross_plan_dependency_is_rejected(configured_app, migrations_pg_dsn: str) -> None:
    """PROY2-05: una tarea no puede depender de una tarea de OTRO plan."""
    seeded = await _seed(migrations_pg_dsn)
    # Un segundo plan en el MISMO proyecto B + una tarea suya.
    plan_c, task_in_c = uuid4(), uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, 'PC', 'draft', '{}'::jsonb)",
            plan_c,
            seeded["tenant"],
            seeded["proj_b"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'en C', 'backlog', 'medium')",
            task_in_c,
            seeded["tenant"],
            seeded["proj_b"],
            plan_c,
        )
    finally:
        await conn.close()

    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        a = await client.post(
            f"/projects/{seeded['proj_b']}/tasks",
            json={"title": "en B", "plan_id": str(seeded["plan_b"])},
            headers=headers,
        )
        # Tarea de plan_b dependiendo de una tarea de plan_c → 422.
        r = await client.put(
            f"/projects/{seeded['proj_b']}/tasks/{a.json()['id']}",
            json={"depends_on": [str(task_in_c)]},
            headers=headers,
        )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == "cross_plan_dependency"


@pytest.mark.asyncio
async def test_delete_plan_cancels_open_tasks(configured_app, migrations_pg_dsn: str) -> None:
    """PROY2-13: borrar un plan no puede dejar sus tareas vivas — el dispatch
    las seguiría despachando contra un plan soft-deleted (invisible)."""
    seeded = await _seed(migrations_pg_dsn)
    task_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Una tarea in_progress colgando del plan del proyecto B.
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'viva', 'in_progress', 'medium')",
            task_id,
            seeded["tenant"],
            seeded["proj_b"],
            seeded["plan_b"],
        )
    finally:
        await conn.close()

    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.delete(f"/plans/{seeded['plan_b']}", headers=headers)
    assert resp.status_code == 204, resp.text

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        task_status = await conn.fetchval("SELECT status FROM tasks WHERE id = $1", task_id)
        plan_deleted = await conn.fetchval(
            "SELECT deleted_at FROM plans WHERE id = $1", seeded["plan_b"]
        )
    finally:
        await conn.close()
    assert task_status == "cancelled"
    assert plan_deleted is not None
