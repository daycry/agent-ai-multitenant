"""GET /tenant/dashboard — el dashboard del tenant en una respuesta (`task_ui_02`).

Seis secciones, en el orden que el operador fijó el 2026-09-09: lo que espera a un
humano, el trabajo en curso por proyecto, el dinero contra presupuesto, la salud
sólo cuando duele, la actividad reciente de los agentes y el estado de los MCP.

Dos propiedades que estos tests fijan porque son las que se rompen sin avisar:

* **Las cifras son las de las pantallas de origen.** Los pendientes salen de la misma
  función que `/human-queue`; si el dashboard contara con su propia consulta, las dos
  cifras acabarían divergiendo y nadie sabría cuál creer.
* **Cross-tenant.** El segundo tenant del seed tiene de todo; nada suyo aparece.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed_tenant(conn: asyncpg.Connection, *, slug: str) -> dict[str, UUID]:
    tenant, user, project, quiet = uuid4(), uuid4(), uuid4(), uuid4()
    admin = uuid4()
    active_plan, waiting_plan = uuid4(), uuid4()
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $2)", tenant, slug
    )
    await conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
        user,
        f"{slug}@t.test",
    )
    await conn.execute(
        "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
        " VALUES ($1, $2, $3, 'tenant_member')",
        uuid4(),
        tenant,
        user,
    )
    await conn.execute(
        "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
        admin,
        f"{slug}-admin@t.test",
    )
    await conn.execute(
        "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
        " VALUES ($1, $2, $3, 'tenant_admin')",
        uuid4(),
        tenant,
        admin,
    )
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name, mcp_servers) VALUES ($1, $2, $3, $4::jsonb)",
        project,
        tenant,
        f"Proyecto {slug}",
        json.dumps([{"name": "jira", "transport": "http", "url": "https://mcp.example.com/sse"}]),
    )
    # Un proyecto sin nada en marcha: no debe salir en «trabajo en curso».
    await conn.execute(
        "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
        quiet,
        tenant,
        f"Tranquilo {slug}",
    )
    await conn.execute(
        "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
        " VALUES ($1, $2, $3, 'Plan activo', 'in_progress', '{}'::jsonb)",
        active_plan,
        tenant,
        project,
    )
    old = datetime.now(tz=UTC) - timedelta(hours=5)
    await conn.execute(
        "INSERT INTO plans (id, tenant_id, project_id, title, status, specification, updated_at)"
        " VALUES ($1, $2, $3, 'Plan esperando', 'pending_human_validation', '{}'::jsonb, $4)",
        waiting_plan,
        tenant,
        project,
        old,
    )
    tasks = [uuid4(), uuid4(), uuid4()]
    for task, status in zip(tasks, ("done", "in_progress", "ready"), strict=True):
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            task,
            tenant,
            project,
            active_plan,
            f"Tarea {status}",
            status,
        )
    await conn.execute(
        "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
        " VALUES ($1, $2, $3, 'running', '[]'::jsonb)",
        uuid4(),
        tenant,
        tasks[1],
    )
    await conn.execute(
        "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
        " VALUES ($1, $2, $3, 'done', '[]'::jsonb)",
        uuid4(),
        tenant,
        tasks[0],
    )
    return {
        "tenant": tenant,
        "user": user,
        "admin": admin,
        "project": project,
        "quiet": quiet,
        "task_ready": tasks[2],
    }


async def _seed(dsn: str) -> dict[str, dict[str, UUID]]:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE approval_requests, executions, tasks, plans, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'P', 'dash-p')",
            _PLATFORM_TENANT_ID,
        )
        a = await _seed_tenant(conn, slug="dash-a")
        b = await _seed_tenant(conn, slug="dash-b")
    finally:
        await conn.close()
    return {"a": a, "b": b}


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


async def _get(app, path: str, token: str) -> dict:  # type: ignore[no-untyped-def]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_the_six_sections_come_in_one_response(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert list(body) == [
        "pending",
        "projects",
        "spend",
        "issues",
        "activity",
        "mcp",
        "generated_at",
    ]


@pytest.mark.asyncio
async def test_pending_counts_are_the_human_queue(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    token = await _token(a["user"], a["tenant"])
    body = await _get(configured_app, "/tenant/dashboard", token)
    queue = await _get(configured_app, "/human-queue", token)
    assert body["pending"]["total"] == len(queue) == 1
    assert body["pending"]["by_kind"] == {"plan_validation": 1}
    assert body["pending"]["oldest"][0]["title"] == "Plan esperando"


@pytest.mark.asyncio
async def test_work_in_progress_lists_only_projects_with_something_moving(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert [p["project_id"] for p in body["projects"]] == [str(a["project"])]
    row = body["projects"][0]
    counts = (row["active_plans"], row["tasks_done"], row["tasks_total"], row["running"])
    assert counts == (1, 1, 3, 1)


@pytest.mark.asyncio
async def test_spend_says_when_there_is_no_budget(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert body["spend"]["available"] is False


@pytest.mark.asyncio
async def test_health_only_speaks_when_it_hurts(configured_app, migrations_pg_dsn: str) -> None:
    """El MCP apunta a un host externo fuera de la allowlist: eso duele y se dice.
    Un proyecto pausado por presupuesto también."""
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("UPDATE projects SET paused_by_budget = true WHERE id = $1", a["quiet"])
    finally:
        await conn.close()
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["admin"], a["tenant"]))
    kinds = sorted(i["kind"] for i in body["issues"])
    assert kinds == ["budget_paused_project", "mcp_egress_blocked"]
    assert all(i["url_path"].startswith("/admin/") for i in body["issues"])
    assert body["mcp"] == [
        {
            "project_id": str(a["project"]),
            "project_name": "Proyecto dash-a",
            "servers": 1,
            "warnings": 1,
        }
    ]


@pytest.mark.asyncio
async def test_activity_is_recent_and_links_to_the_run(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert {r["status"] for r in body["activity"]} == {"running", "done"}
    assert all(r["url_path"].startswith("/admin/executions/") for r in body["activity"])
    assert all(r["project_name"] == "Proyecto dash-a" for r in body["activity"])


@pytest.mark.asyncio
async def test_nothing_from_another_tenant_leaks(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a, b = seeded["a"], seeded["b"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    text = json.dumps(body)
    assert "dash-b" not in text
    assert str(b["project"]) not in text


@pytest.mark.asyncio
async def test_requires_authentication(configured_app, migrations_pg_dsn: str) -> None:
    await _seed(migrations_pg_dsn)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/tenant/dashboard")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_member_does_not_see_spend_nor_budget_pauses(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Gasto y pausas por presupuesto piden `tenant_admin` en sus endpoints de origen
    (CWE-863): el miembro raso no los ve; el admin del mismo tenant sí (misma caché)."""
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("UPDATE projects SET paused_by_budget = true WHERE id = $1", a["quiet"])
        await conn.execute(
            "UPDATE organizations SET tenant_paused_by_budget = true WHERE id = $1", a["tenant"]
        )
    finally:
        await conn.close()
    admin_body = await _get(
        configured_app, "/tenant/dashboard", await _token(a["admin"], a["tenant"])
    )
    member_body = await _get(
        configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"])
    )
    assert member_body["spend"] == {"available": False, "reason": "forbidden"}
    assert not [i for i in member_body["issues"] if i["kind"].startswith("budget_paused")]
    assert admin_body["spend"].get("reason") != "forbidden"
    admin_kinds = {i["kind"] for i in admin_body["issues"]}
    assert {"budget_paused_project", "budget_paused_tenant"} <= admin_kinds
    # El recorte es por petición: leer la caché como admin no lo destapa al miembro.
    again = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert again["spend"]["reason"] == "forbidden"


@pytest.mark.asyncio
async def test_spend_has_its_own_slow_cache(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    await _get(configured_app, "/tenant/dashboard", await _token(a["admin"], a["tenant"]))
    from api_server.auth.deps import get_redis

    ttl = await get_redis().ttl(f"dashboard:tenant-spend:{a['tenant']}")
    assert 30 < ttl <= 300


@pytest.mark.asyncio
async def test_project_row_carries_the_last_failure(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    failed = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, steps_log)"
            " VALUES ($1, $2, $3, 'failed', '[]'::jsonb)",
            failed,
            a["tenant"],
            a["task_ready"],
        )
    finally:
        await conn.close()
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert body["projects"][0]["last_failure"] == {
        "execution_id": str(failed),
        "task_title": "Tarea ready",
        "url_path": f"/admin/executions/{failed}",
    }


@pytest.mark.asyncio
async def test_project_without_failures_has_null_last_failure(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    assert body["projects"][0]["last_failure"] is None


@pytest.mark.asyncio
async def test_activity_orders_by_creation_and_reports_it(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    a = seeded["a"]
    body = await _get(configured_app, "/tenant/dashboard", await _token(a["user"], a["tenant"]))
    ats = [r["at"] for r in body["activity"]]
    assert ats == sorted(ats, reverse=True)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        created = await conn.fetchval(
            "SELECT max(created_at) FROM executions WHERE tenant_id = $1", a["tenant"]
        )
    finally:
        await conn.close()
    assert body["activity"][0]["at"] == created.isoformat()


@pytest.mark.asyncio
async def test_system_admin_without_tenant_gets_a_typed_400(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        sysadmin = uuid4()
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin)"
            " VALUES ($1, 'sys@t.test', 'h', true)",
            sysadmin,
        )
    finally:
        await conn.close()
    sid = uuid7()
    await SessionStore(get_redis()).create(sid, user_id=sysadmin, tenant_id=None, ttl_seconds=3600)
    token = encode_jwt(user_id=sysadmin, session_id=sid, tenant_id=None)
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/tenant/dashboard", headers={"Authorization": f"Bearer {token}"})
    # 4xx tipado (nunca un 500): el panel lo reconoce por el detalle y pide elegir tenant.
    assert resp.status_code in (400, 403), resp.text
    assert "tenant" in resp.json()["detail"]
