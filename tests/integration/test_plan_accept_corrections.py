"""Integration tests de `POST /plans/{id}/accept-corrections` (ADR 0107).

Un plan rechazado con tareas correctivas en el spec (`origin: correction`
+ entrada en `specification.corrections`) se reactiva aceptando la
selección: en una única transacción se materializan las tareas en el
Kanban (scope=selection), el plan transiciona `rejected -> in_progress`
y la entrada de corrections pasa a `accepted`. Tras el commit las tareas
raíz se promocionan a `ready` (patrón start-execution).
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

# Dos tareas originales + dos correctivas nacidas de un rechazo. `fix-2`
# depende de `fix-1` para probar que la promoción respeta el DAG.
_PLAN_SPEC = {
    "tasks": [
        {"id": "t1", "title": "Original A", "complexity": "m"},
        {"id": "t2", "title": "Original B", "complexity": "s", "depends_on": ["t1"]},
        {
            "id": "fix-1",
            "title": "Acotar filtro Content-Type a api/v1",
            "complexity": "s",
            "origin": "correction",
            "acceptance_criteria": ["La portada responde text/html"],
        },
        {
            "id": "fix-2",
            "title": "Test de regresión del filtro",
            "complexity": "s",
            "origin": "correction",
            "depends_on": ["fix-1"],
        },
    ],
    "corrections": [
        {
            "session_id": "sess-1",
            "reason": "El filtro JSON es global y rompe la portada HTML",
            "task_ids": ["fix-1", "fix-2"],
            "status": "proposed",
            "created_at": "2026-07-08T00:00:00+00:00",
        }
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
            "Tenant Corrections",
            "tenant-corrections",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-corrections",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@corrections.test",
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
            "Corrections Project",
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


async def _create_rejected_plan(client: AsyncClient, project_id: UUID, headers: dict) -> str:
    """Lleva un plan con el spec de arriba hasta `rejected` por el ciclo real:
    draft -> pending_approval -> approved (+ sync de las originales)
    -> in_progress -> pending_human_validation -> rejected."""
    create = await client.post(
        f"/projects/{project_id}/plans",
        json={"title": "Plan con correcciones", "specification": _PLAN_SPEC},
        headers=headers,
    )
    assert create.status_code == 201, create.text
    plan_id: str = create.json()["id"]

    moved = await client.put(
        f"/plans/{plan_id}", json={"status": "pending_approval"}, headers=headers
    )
    assert moved.status_code == 200, moved.text
    approved = await client.post(f"/plans/{plan_id}/approve", headers=headers)
    assert approved.status_code == 200, approved.text

    # Solo las originales se materializan antes del rechazo; las fix-* quedan
    # como propuestas en el spec (es el estado que deja generate-corrections).
    synced = await client.post(
        f"/plans/{plan_id}/sync-to-kanban",
        json={"scope": "selection", "task_ids": ["t1", "t2"]},
        headers=headers,
    )
    assert synced.status_code == 200, synced.text

    for next_status in ("in_progress", "pending_human_validation", "rejected"):
        upd = await client.put(f"/plans/{plan_id}", json={"status": next_status}, headers=headers)
        assert upd.status_code == 200, upd.text
    return plan_id


@pytest.mark.asyncio
async def test_accept_corrections_materialises_tasks_and_reactivates_plan(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_rejected_plan(client, seeded["project_id"], headers)

        resp = await client.post(
            f"/plans/{plan_id}/accept-corrections",
            json={"task_ids": ["fix-1", "fix-2"]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "in_progress"
        corrections = body["specification"]["corrections"]
        assert corrections[0]["status"] == "accepted"
        assert corrections[0]["accepted_task_ids"] == ["fix-1", "fix-2"]

        # Las correctivas están en el Kanban; fix-1 (raíz) promocionada a
        # ready, fix-2 espera a su dependencia en backlog.
        tasks_resp = await client.get(
            f"/projects/{seeded['project_id']}/tasks?plan_id={plan_id}", headers=headers
        )
        assert tasks_resp.status_code == 200, tasks_resp.text
        by_spec = {t["inputs"]["plan_task_spec_id"]: t for t in tasks_resp.json()}
        assert {"fix-1", "fix-2"} <= set(by_spec)
        assert by_spec["fix-1"]["status"] == "ready"
        assert by_spec["fix-2"]["status"] == "backlog"

        # Idempotencia (reintento tras perder la respuesta): re-aceptar no
        # duplica tareas ni rompe — el plan ya está in_progress.
        again = await client.post(
            f"/plans/{plan_id}/accept-corrections",
            json={"task_ids": ["fix-1", "fix-2"]},
            headers=headers,
        )
        assert again.status_code == 200, again.text
        tasks_after = await client.get(
            f"/projects/{seeded['project_id']}/tasks?plan_id={plan_id}", headers=headers
        )
        assert len(tasks_after.json()) == len(tasks_resp.json())


@pytest.mark.asyncio
async def test_accept_corrections_conflicts_outside_rejected(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        create = await client.post(
            f"/projects/{seeded['project_id']}/plans",
            json={"title": "Draft sin rechazo", "specification": _PLAN_SPEC},
            headers=headers,
        )
        plan_id = create.json()["id"]

        resp = await client.post(
            f"/plans/{plan_id}/accept-corrections",
            json={"task_ids": ["fix-1"]},
            headers=headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["error"] == "plan_not_rejected"

        # El plan no se movió ni materializó nada.
        plan = await client.get(f"/plans/{plan_id}", headers=headers)
        assert plan.json()["status"] == "draft"
        tasks_resp = await client.get(
            f"/projects/{seeded['project_id']}/tasks?plan_id={plan_id}", headers=headers
        )
        assert tasks_resp.json() == []


@pytest.mark.asyncio
async def test_accept_corrections_unknown_ids_return_422(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_rejected_plan(client, seeded["project_id"], headers)

        resp = await client.post(
            f"/plans/{plan_id}/accept-corrections",
            json={"task_ids": ["no-such-task"]},
            headers=headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "invalid_sync_scope"

        # Sin efectos colaterales: sigue rechazado.
        plan = await client.get(f"/plans/{plan_id}", headers=headers)
        assert plan.json()["status"] == "rejected"
