"""PROY2-01/02 (auditoría proyecto 2026-07-17): la superficie genérica de
planes respeta los gates — verificación e2e a través del router.

`POST /plans` no puede nacer en un estado avanzado; `PUT /plans/{id}` no puede
aprobar (va por `POST /approve`), completar (va por el veredicto) ni entrar en
`pending_human_validation` con tareas sin hacer.
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
    tenant, admin, project = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE tasks, plans, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2)",
            tenant,
            f"gate-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@gate.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')", project, tenant
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "project": project}


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
async def test_post_plan_rejects_privileged_initial_status(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/projects/{seeded['project']}/plans",
            json={"title": "Trampa", "status": "approved"},
            headers=headers,
        )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "invalid_initial_status"


@pytest.mark.asyncio
async def test_put_plan_cannot_self_approve(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        created = await client.post(
            f"/projects/{seeded['project']}/plans",
            json={"title": "P", "status": "pending_approval"},
            headers=headers,
        )
        plan_id = created.json()["id"]
        resp = await client.put(f"/plans/{plan_id}", json={"status": "approved"}, headers=headers)
    assert resp.status_code == 403
    body = resp.json()["detail"]
    assert body["error"] == "privileged_transition_requires_gated_endpoint"
    assert "approve" in body["use"]


@pytest.mark.asyncio
async def test_put_plan_cannot_enter_validation_with_open_tasks(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    # Un plan in_progress con una tarea backlog (open).
    conn = await asyncpg.connect(migrations_pg_dsn)
    plan_id = uuid4()
    try:
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1, $2, $3, 'P', 'in_progress', '{}'::jsonb)",
            plan_id,
            seeded["tenant"],
            seeded["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'T', 'backlog', 'medium')",
            uuid4(),
            seeded["tenant"],
            seeded["project"],
            plan_id,
        )
    finally:
        await conn.close()
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/plans/{plan_id}", json={"status": "pending_human_validation"}, headers=headers
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "plan_has_open_tasks"
