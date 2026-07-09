"""Integration — vías huérfanas del hallazgo #2 que ahora re-evalúan el plan blocked.

Tras el fix de 50f4e5d (human-action + PUT del Kanban ya reactivan), quedaban 3 vías
que cambiaban el snapshot de un plan ``blocked`` SIN re-evaluarlo → el plan quedaba
varado esperando un segundo click humano:

  (A) DELETE de la tarea blocked (hoja) — borra la CAUSA del bloqueo.
  (B) PUT de SOLO dependencias (sin cambio de status) — quita la arista que ataba un
      backlog transitivamente bloqueado.
  (C) POST /plans/{id}/free-task — añade una tarea avanzable a un plan blocked.

Cada una llama ahora ``reactivate_plan_if_unstuck`` (no-op si el plan no está blocked).
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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _seed_base(conn: asyncpg.Connection) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "user": uuid4(), "project": uuid4(), "plan": uuid4()}
    await conn.execute(
        "TRUNCATE task_dependencies, tasks, plans, projects, user_org_memberships,"
        " organizations, users RESTART IDENTITY CASCADE"
    )
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Org', 'org-ub')",
        ids["tenant"],
    )
    await conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@ub.test', 'x')",
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
    return ids


async def _add_task(
    conn: asyncpg.Connection, ids: dict[str, UUID], task_id: UUID, *, status: str
) -> None:
    await conn.execute(
        "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority,"
        " acceptance_criteria, inputs) VALUES ($1, $2, $3, $4, 'T', $5, 'medium',"
        " '[]'::jsonb, '{}'::jsonb)",
        task_id,
        ids["tenant"],
        ids["project"],
        ids["plan"],
        status,
    )


async def _plan_status(dsn: str, plan_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT status FROM plans WHERE id = $1", plan_id)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_delete_blocked_leaf_task_reactivates_plan(
    configured_app, migrations_pg_dsn: str
) -> None:
    """(A) Borrar la única tarea blocked (hoja) de un plan blocked lo revierte."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_base(conn)
        task_blocked, task_done = uuid4(), uuid4()
        await _add_task(conn, ids, task_blocked, status="blocked")
        await _add_task(conn, ids, task_done, status="done")
    finally:
        await conn.close()
    token = await _mint_token(ids["user"], ids["tenant"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.delete(
            f"/projects/{ids['project']}/tasks/{task_blocked}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 204, resp.text
    assert await _plan_status(migrations_pg_dsn, ids["plan"]) == "in_progress"


@pytest.mark.asyncio
async def test_deps_only_edit_unsticks_plan(configured_app, migrations_pg_dsn: str) -> None:
    """(B) Quitar la dependencia que ataba un backlog a la tarea blocked (PUT de
    solo depends_on, sin cambio de status) revierte el plan."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_base(conn)
        task_a, task_b = uuid4(), uuid4()  # A blocked, B backlog dep-of-A (atascado)
        await _add_task(conn, ids, task_a, status="blocked")
        await _add_task(conn, ids, task_b, status="backlog")
        await conn.execute(
            "INSERT INTO task_dependencies (task_id, depends_on_task_id) VALUES ($1, $2)",
            task_b,
            task_a,
        )
    finally:
        await conn.close()
    token = await _mint_token(ids["user"], ids["tenant"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # PUT de SOLO dependencias: quitar la arista B→A. Status de B sin cambiar.
        resp = await client.put(
            f"/projects/{ids['project']}/tasks/{task_b}",
            json={"depends_on": []},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    assert await _plan_status(migrations_pg_dsn, ids["plan"]) == "in_progress"


@pytest.mark.asyncio
async def test_free_task_on_blocked_plan_reactivates(
    configured_app, migrations_pg_dsn: str
) -> None:
    """(C) Añadir una free-task avanzable a un plan blocked lo revierte."""
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ids = await _seed_base(conn)
        await _add_task(conn, ids, uuid4(), status="blocked")
    finally:
        await conn.close()
    token = await _mint_token(ids["user"], ids["tenant"])
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/plans/{ids['plan']}/free-task",
            json={"title": "Tarea extra", "description": "algo que faltaba"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 201, resp.text
    assert await _plan_status(migrations_pg_dsn, ids["plan"]) == "in_progress"
