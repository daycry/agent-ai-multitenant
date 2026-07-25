"""GET /plans/{id}/status — el estado del plan de un vistazo (task_wf_30).

Un endpoint en lugar de las cuatro secciones sueltas que proponía la primera
versión del plan: menos código, y el operador lee el estado del plan en un sitio
en vez de en cuatro. Cubre tres cegueras que venían del mismo origen — cosas
calculadas y nunca conectadas a su consumidor:

* **D-01** progreso X/Y (`compute_plan_progress`, escrito y testeado desde el
  Plan 06, sin endpoint ni pantalla),
* **D-02** el PR (`pr_url`/`pr_branch`/`pr_error`, cero ocurrencias en el
  frontend),
* **D-04** el gasto REAL contra el estimado (el estimado se calculaba entero, el
  real no se agregaba en ninguna parte).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_PLAN_SPEC = {
    "tasks": [
        {"id": "t1", "title": "Modelar", "complexity": "m", "estimated_hours": 4},
        {"id": "t2", "title": "Implementar", "complexity": "l", "estimated_hours": 12},
    ],
}


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id, user_id, project_id = uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plan_comments, plans, conversations, projects,"
            " agents, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Status",
            "tenant-status",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-status",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@status.test",
            "argon2-placeholder",
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
            "Status Project",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id, "project_id": project_id}


async def _add_task(dsn: str, *, tenant_id: UUID, project_id: UUID, plan_id: UUID, status: str):
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, $5, $6, 'medium')",
            task_id,
            tenant_id,
            project_id,
            plan_id,
            f"task {status}",
            status,
        )
    finally:
        await conn.close()
    return task_id


async def _add_execution(dsn: str, *, tenant_id: UUID, task_id: UUID, cost: str, tokens: int):
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, total_cost_usd,"
            " total_tokens) VALUES ($1, $2, $3, 'completed', $4::numeric, $5)",
            uuid4(),
            tenant_id,
            task_id,
            cost,
            tokens,
        )
    finally:
        await conn.close()


async def _set_pr(dsn: str, plan_id: UUID, *, url: str | None, error: str | None) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE plans SET pr_url = $2, pr_branch = 'plan/abc-slug', pr_error = $3"
            " WHERE id = $1",
            plan_id,
            url,
            error,
        )
    finally:
        await conn.close()


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


async def _create_plan(client: AsyncClient, project_id: UUID, headers: dict[str, str]) -> str:
    resp = await client.post(
        f"/projects/{project_id}/plans",
        json={"title": "Status plan", "specification": _PLAN_SPEC},
        headers=headers,
    )
    assert resp.status_code in (200, 201), resp.text
    return str(resp.json()["id"])


# ===========================================================================
@pytest.mark.asyncio
async def test_status_reports_progress_pr_and_actual_spend(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El criterio de aceptación: abrir un plan muestra en qué punto está, dónde
    está su PR y cuánto ha costado frente a lo previsto — sin desplazarse."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        done = await _add_task(
            migrations_pg_dsn,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            plan_id=UUID(plan_id),
            status="done",
        )
        await _add_task(
            migrations_pg_dsn,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            plan_id=UUID(plan_id),
            status="in_progress",
        )
        await _add_task(
            migrations_pg_dsn,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            plan_id=UUID(plan_id),
            status="cancelled",
        )
        await _add_execution(
            migrations_pg_dsn,
            tenant_id=seeded["tenant_id"],
            task_id=done,
            cost="2.50",
            tokens=120_000,
        )
        await _set_pr(
            migrations_pg_dsn, UUID(plan_id), url="https://github.com/x/y/pull/7", error=None
        )

        resp = await client.get(f"/plans/{plan_id}/status", headers=headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()

    # D-01: progreso. La cancelada no cuenta ni en total ni en done.
    assert body["progress"]["label"] == "1/2"
    assert body["progress"]["open"] == 1

    # D-02: el PR, visible.
    assert body["pr"]["url"] == "https://github.com/x/y/pull/7"
    assert body["pr"]["branch"] == "plan/abc-slug"
    assert body["pr"]["error"] is None

    # D-04: el gasto REAL, agregado. Se compara el VALOR y no la cadena: la
    # columna es NUMERIC(_, 6) y la API no recorta dígitos de una medición real
    # — dar formato es cosa de la UI.
    cost = body["cost"]
    assert Decimal(cost["actual_ai_cost"]) == Decimal("2.50")
    assert cost["actual_tokens"] == 120_000
    assert cost["actual_runs"] == 1
    # Y el estimado, en su propia moneda: 4 + 12 = 16 h × 50 €/h.
    assert cost["human_currency"] == "EUR"
    assert Decimal(cost["estimated_human_hours"]) == Decimal("16")
    assert Decimal(cost["estimated_human_cost"]) == Decimal("800")
    assert cost["ai_currency"] == "USD"


@pytest.mark.asyncio
async def test_a_failed_pr_says_why(configured_app, migrations_pg_dsn: str) -> None:
    """Sin esto el operador aprobaba el plan y no veía ni el PR ni el motivo."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)
        await _set_pr(migrations_pg_dsn, UUID(plan_id), url=None, error="403 from GitHub")
        resp = await client.get(f"/plans/{plan_id}/status", headers=headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["pr"] == {
        "url": None,
        "branch": "plan/abc-slug",
        "error": "403 from GitHub",
    }


@pytest.mark.asyncio
async def test_a_plan_without_tasks_reports_zeros_not_an_error(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un plan recién creado tiene progreso 0/0 y gasto cero — la cabecera debe
    poder pintarlo sin casos especiales."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)
        resp = await client.get(f"/plans/{plan_id}/status", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["progress"] == {"total": 0, "done": 0, "open": 0, "label": "0/0"}
    assert body["cost"]["actual_runs"] == 0
    assert body["cost"]["over_estimate"] is False


@pytest.mark.asyncio
async def test_another_tenant_cannot_read_the_status(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Frontera de tenant: el endpoint va por sesión con RLS como el resto."""
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        plan_id = await _create_plan(client, seeded["project_id"], headers)

        intruder_tenant, intruder_user = uuid4(), uuid4()
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Otro', 'otro-status')",
                intruder_tenant,
            )
            await conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')",
                intruder_user,
                "mallory@otro.test",
            )
            await conn.execute(
                "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
                " VALUES ($1, $2, $3, 'tenant_admin')",
                uuid4(),
                intruder_tenant,
                intruder_user,
            )
        finally:
            await conn.close()

        intruder_token = await _mint_token(intruder_user, intruder_tenant)
        resp = await client.get(
            f"/plans/{plan_id}/status",
            headers={"Authorization": f"Bearer {intruder_token}"},
        )

    assert resp.status_code == 404, resp.text
