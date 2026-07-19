"""GET /human-queue (ADR 0123) — todo lo que espera decisión humana, en uno.

El cuello de botella nº1 del flujo: lo pendiente del humano vive repartido en
4 pantallas (planes pending_human_validation, approval_requests pendientes,
runs needs_human_review y awaiting_human_approval). El endpoint las agrega
con un shape uniforme ordenado por antigüedad (lo más viejo primero), RLS
tenant-scoped.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    user = uuid4()
    project = uuid4()
    plan = uuid4()
    task = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE approval_requests, executions, tasks, plans, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'HQ','hq-t'), ($2,'P','hq-p')",
            tenant,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,'hq@t.test','h')", user
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,'tenant_admin')",
            uuid4(),
            tenant,
            user,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,'HQ')", project, tenant
        )
        old = datetime.now(tz=UTC) - timedelta(hours=30)
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification,"
            " updated_at) VALUES ($1,$2,$3,'Plan esperando','pending_human_validation',"
            " '{}'::jsonb, $4)",
            plan,
            tenant,
            project,
            old,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
            " VALUES ($1,$2,$3,$4,'Tarea escalada','in_review')",
            task,
            tenant,
            project,
            plan,
        )
        exec_review = uuid4()
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1,$2,$3,'needs_human_review','[]'::jsonb)",
            exec_review,
            tenant,
            task,
        )
        exec_await = uuid4()
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1,$2,$3,'awaiting_human_approval','[]'::jsonb)",
            exec_await,
            tenant,
            task,
        )
        await conn.execute(
            "INSERT INTO approval_requests (id, tenant_id, execution_id, task_id, project_id,"
            " category, action, status) VALUES ($1,$2,$3,$4,$5,'file_write','{}'::jsonb,"
            " 'pending')",
            uuid4(),
            tenant,
            exec_await,
            task,
            project,
        )
        # Ruido que NO debe aparecer: plan draft + run done + approval resuelta.
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1,$2,$3,'Borrador','draft','{}'::jsonb)",
            uuid4(),
            tenant,
            project,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "user": user, "plan": plan}


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


@pytest.mark.asyncio
async def test_queue_aggregates_the_four_sources_oldest_first(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _token(seeded["user"], seeded["tenant"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/human-queue", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    items = resp.json()
    kinds = [i["kind"] for i in items]
    assert sorted(kinds) == sorted(
        ["plan_validation", "approval_request", "run_review", "run_approval"]
    )
    # El plan (30 h de antigüedad) va PRIMERO — lo más viejo arriba.
    assert items[0]["kind"] == "plan_validation"
    assert items[0]["title"] == "Plan esperando"
    assert items[0]["age_seconds"] > 24 * 3600
    # Cada item lleva la ruta del panel donde se resuelve.
    assert all(i["url_path"].startswith("/admin/") for i in items)
