"""Integration tests for the tenant STATISTICS dashboard (Plan 14 task_14_12).

The aggregation read surface behind the tenant stats dashboard, the consumption
summary and the per-execution runs explorer — all over the ``executions`` table:

  * ``GET /tenant-stats/dashboard``    — agent stats (success rate, mean
    duration, mean cost) + top/bottom agents + temporal trend;
  * ``GET /tenant-stats/consumption``  — accumulated cost, total tokens
    (input/output/cached), run count, mean cost, costliest run;
  * ``GET /tenant-stats/runs``         — paginated, filterable runs explorer.

All three are JWT-authenticated, gated on ``tenant_admin`` and run on a
tenant-scoped RLS session (this is a TENANT dashboard — cross-tenant comparison
is the System-Admin-only task_14_15). Costs are canonical USD.

Coverage:

  * aggregation correctness: success rate (done/total), mean duration (ms),
    mean + accumulated cost, per-agent breakdown, top/bottom by success rate,
    per-day trend;
  * the consumption summary: accumulated cost, token input/output split from
    ``steps_log``, cached tokens reported as 0, costliest run;
  * the runs explorer: per-execution rows with resolved plan/task/agent labels,
    the model extracted from ``steps_log``, verdict + retry_count + duration,
    and filters (agent / role / plan / verdict / model / min-cost) + pagination;
  * RBAC: a plain member (tenant_user) is denied (403);
  * cross-tenant (@pytest.mark.cross_tenant): tenant A never sees tenant B's
    executions in any of the three endpoints.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB and flush Redis DB 15.
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

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_user_with_jwt(
    dsn: str, redis_url: str, *, tenant_id: UUID, email: str, role: str
) -> tuple[UUID, str]:
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, false)",
            user_id,
            email,
            "x",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, $4, true)",
            uuid4(),
            tenant_id,
            user_id,
            role,
        )
    finally:
        await conn.close()

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    jwt = encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant_id)
    return user_id, jwt


async def _admin(dsn: str, redis_url: str, *, tenant: UUID, slug: str) -> str:
    _id, jwt = await _seed_user_with_jwt(
        dsn, redis_url, tenant_id=tenant, email=f"admin@{slug}.example.com", role="tenant_admin"
    )
    return jwt


async def _seed_project(dsn: str, *, tenant: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_plan(dsn: str, *, tenant: UUID, project_id: UUID, title: str) -> UUID:
    plan_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, created_by) "
            "VALUES ($1, $2, $3, $4, NULL)",
            plan_id,
            tenant,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return plan_id


async def _seed_agent(dsn: str, *, tenant: UUID, name: str, role: str) -> UUID:
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope) "
            "VALUES ($1, $2, $3, $4, 'be helpful', 'global_tenant_template')",
            agent_id,
            tenant,
            name,
            role,
        )
    finally:
        await conn.close()
    return agent_id


async def _seed_task(
    dsn: str,
    *,
    tenant: UUID,
    project_id: UUID,
    plan_id: UUID | None,
    title: str,
    retry_count: int = 0,
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, retry_count) "
            "VALUES ($1, $2, $3, $4, $5, 'done', $6)",
            task_id,
            tenant,
            project_id,
            plan_id,
            title,
            retry_count,
        )
    finally:
        await conn.close()
    return task_id


def _model_steps(model: str, *, tokens_in: int, tokens_out: int, cost: float) -> list[dict]:
    """A minimal steps_log with one model_call step (the shape steps.py emits)."""
    return [
        {
            "index": 0,
            "kind": "model_call",
            "node": "act",
            "status": "ok",
            "model": model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "cost_usd": cost,
        }
    ]


async def _seed_execution(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    agent_id: UUID | None,
    status: str = "done",
    total_tokens: int = 0,
    total_cost_usd: float = 0.0,
    duration_ms: int | None = None,
    steps_log: list[dict] | None = None,
    created_at: datetime | None = None,
) -> UUID:
    execution_id = uuid4()
    now = created_at or datetime.now(tz=UTC)
    started = now
    completed = now + timedelta(milliseconds=duration_ms) if duration_ms is not None else None
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions "
            "(id, tenant_id, task_id, agent_id, status, steps_log, total_tokens, "
            " total_cost_usd, started_at, completed_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11)",
            execution_id,
            tenant,
            task_id,
            agent_id,
            status,
            json.dumps(steps_log or []),
            total_tokens,
            total_cost_usd,
            started,
            completed,
            now,
        )
    finally:
        await conn.close()
    return execution_id


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, agents, projects, "
            "user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture: real api-server wired to the test DB + Redis
# ---------------------------------------------------------------------------
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
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


# ---------------------------------------------------------------------------
# Dashboard aggregation correctness
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dashboard_aggregation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")

    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    plan = await _seed_plan(migrations_pg_dsn, tenant=tenant, project_id=project, title="Plan A")
    agent_be = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Backend", role="backend")
    agent_fe = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Frontend", role="frontend")
    task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, plan_id=plan, title="T"
    )

    # Backend: 2 done + 1 aborted (success 2/3). Durations 1000 + 3000 ms.
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent_be,
        status="done",
        total_tokens=1000,
        total_cost_usd=0.02,
        duration_ms=1000,
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent_be,
        status="done",
        total_tokens=2000,
        total_cost_usd=0.04,
        duration_ms=3000,
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent_be,
        status="aborted",
        total_tokens=500,
        total_cost_usd=0.01,
        duration_ms=None,  # never finished — skipped by the mean duration
    )
    # Frontend: 1 done (success 1/1).
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent_fe,
        status="done",
        total_tokens=300,
        total_cost_usd=0.005,
        duration_ms=2000,
    )

    async with _client(configured_app) as client:
        resp = await client.get("/tenant-stats/dashboard", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Headline: 4 runs, 3 done = 0.750. Total cost = 0.075000.
        assert body["total_runs"] == 4
        assert body["succeeded_runs"] == 3
        assert body["overall_success_rate"] == "0.750"
        assert body["total_cost_usd"] == "0.075000"
        assert body["currency"] == "USD"
        # Mean duration over the 3 finished runs (1000+3000+2000)/3 = 2000.00 ms.
        assert body["mean_duration_ms"] == "2000.00"

        by_agent = {a["agent_name"]: a for a in body["by_agent"]}
        assert by_agent["Backend"]["run_count"] == 3
        assert by_agent["Backend"]["succeeded"] == 2
        assert by_agent["Backend"]["success_rate"] == "0.667"
        assert by_agent["Backend"]["agent_role"] == "backend"
        # Backend mean duration over its 2 finished runs = (1000+3000)/2 = 2000.00.
        assert by_agent["Backend"]["mean_duration_ms"] == "2000.00"
        # Backend mean cost over its 3 runs (0.02, 0.04, 0.01) ~= 0.023333.
        assert by_agent["Backend"]["mean_cost_usd"] == "0.023333"
        assert by_agent["Backend"]["total_tokens"] == 3500
        assert by_agent["Frontend"]["success_rate"] == "1.000"

        # top / bottom by success rate: Frontend (1.0) on top, Backend (0.667) bottom.
        assert body["top_agents"][0]["agent_name"] == "Frontend"
        assert body["bottom_agents"][0]["agent_name"] == "Backend"

        # trend: one day, all 4 runs.
        assert sum(p["run_count"] for p in body["trend"]) == 4
        assert sum(p["succeeded"] for p in body["trend"]) == 3


# ---------------------------------------------------------------------------
# Window excludes old runs + out-of-range is 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dashboard_window(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, plan_id=None, title="T"
    )

    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=None,
        status="done",
        duration_ms=500,
        created_at=datetime.now(tz=UTC) - timedelta(days=2),
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=None,
        status="aborted",
        duration_ms=500,
        created_at=datetime.now(tz=UTC) - timedelta(days=100),
    )

    async with _client(configured_app) as client:
        resp = await client.get("/tenant-stats/dashboard?window_days=30", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_runs"] == 1
        assert body["overall_success_rate"] == "1.000"

        bad = await client.get("/tenant-stats/dashboard?window_days=0", headers=_auth(jwt))
        assert bad.status_code == 422, bad.text


# ---------------------------------------------------------------------------
# Consumption summary
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_consumption_summary(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    agent = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Backend", role="backend")
    task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, plan_id=None, title="Build login"
    )

    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent,
        status="done",
        total_tokens=1500,
        total_cost_usd=0.02,
        duration_ms=1000,
        steps_log=_model_steps("claude-sonnet", tokens_in=1000, tokens_out=500, cost=0.02),
    )
    # The costliest run.
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent,
        status="done",
        total_tokens=3000,
        total_cost_usd=0.10,
        duration_ms=2000,
        steps_log=_model_steps("claude-opus", tokens_in=2000, tokens_out=1000, cost=0.10),
    )

    async with _client(configured_app) as client:
        resp = await client.get("/tenant-stats/consumption", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["run_count"] == 2
        assert body["accumulated_cost_usd"] == "0.120000"
        assert body["mean_cost_usd"] == "0.060000"
        assert body["total_tokens"] == 4500
        # input/output split summed from steps_log: 1000+2000 in, 500+1000 out.
        assert body["total_tokens_input"] == 3000
        assert body["total_tokens_output"] == 1500
        # Cached counts not captured per step yet — reported as 0, not faked.
        assert body["total_tokens_cached"] == 0
        assert body["currency"] == "USD"

        # Costliest run is the 0.10 one.
        assert body["costliest_run"]["total_cost_usd"] == "0.100000"
        assert body["costliest_run"]["task_title"] == "Build login"
        assert body["costliest_run"]["agent_name"] == "Backend"


# ---------------------------------------------------------------------------
# Runs explorer: rows + filters + pagination
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_runs_explorer_rows_filters_pagination(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    plan = await _seed_plan(migrations_pg_dsn, tenant=tenant, project_id=project, title="Plan A")
    agent_be = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Backend", role="backend")
    agent_fe = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Frontend", role="frontend")
    task = await _seed_task(
        migrations_pg_dsn,
        tenant=tenant,
        project_id=project,
        plan_id=plan,
        title="Build login",
        retry_count=2,
    )

    ex_be = await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent_be,
        status="done",
        total_tokens=1500,
        total_cost_usd=0.05,
        duration_ms=1234,
        steps_log=_model_steps("claude-opus", tokens_in=1000, tokens_out=500, cost=0.05),
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=agent_fe,
        status="aborted",
        total_tokens=200,
        total_cost_usd=0.001,
        duration_ms=None,
        steps_log=_model_steps("claude-haiku", tokens_in=150, tokens_out=50, cost=0.001),
    )

    async with _client(configured_app) as client:
        # All rows with resolved labels.
        resp = await client.get("/tenant-stats/runs", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 2
        be_row = next(r for r in rows if r["id"] == str(ex_be))
        assert be_row["task_title"] == "Build login"
        assert be_row["plan_title"] == "Plan A"
        assert be_row["agent_name"] == "Backend"
        assert be_row["agent_role"] == "backend"
        assert be_row["model"] == "claude-opus"
        assert be_row["verdict"] == "done"
        assert be_row["succeeded"] is True
        assert be_row["retry_count"] == 2
        assert be_row["duration_ms"] == 1234
        assert be_row["total_cost_usd"] == "0.050000"

        # Filter by agent.
        by_agent = await client.get(f"/tenant-stats/runs?agent_id={agent_be}", headers=_auth(jwt))
        assert len(by_agent.json()) == 1
        assert by_agent.json()[0]["agent_name"] == "Backend"

        # Filter by role.
        by_role = await client.get("/tenant-stats/runs?role=frontend", headers=_auth(jwt))
        assert len(by_role.json()) == 1
        assert by_role.json()[0]["agent_role"] == "frontend"

        # Filter by plan.
        by_plan = await client.get(f"/tenant-stats/runs?plan_id={plan}", headers=_auth(jwt))
        assert len(by_plan.json()) == 2

        # Filter by verdict (status).
        by_verdict = await client.get("/tenant-stats/runs?verdict=aborted", headers=_auth(jwt))
        assert len(by_verdict.json()) == 1
        assert by_verdict.json()[0]["succeeded"] is False

        # Filter by model (extracted from steps_log).
        by_model = await client.get("/tenant-stats/runs?model=claude-opus", headers=_auth(jwt))
        assert len(by_model.json()) == 1
        assert by_model.json()[0]["model"] == "claude-opus"

        # Filter by min-cost.
        by_cost = await client.get("/tenant-stats/runs?min_cost=0.01", headers=_auth(jwt))
        assert len(by_cost.json()) == 1
        assert by_cost.json()[0]["id"] == str(ex_be)

        # Pagination.
        page1 = await client.get("/tenant-stats/runs?limit=1&offset=0", headers=_auth(jwt))
        assert len(page1.json()) == 1
        page2 = await client.get("/tenant-stats/runs?limit=1&offset=1", headers=_auth(jwt))
        assert len(page2.json()) == 1
        assert page1.json()[0]["id"] != page2.json()[0]["id"]


# ---------------------------------------------------------------------------
# RBAC: a plain member (tenant_user) is denied (403)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_plain_member_denied_403(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _uid, member_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )

    async with _client(configured_app) as client:
        assert (
            await client.get("/tenant-stats/dashboard", headers=_auth(member_jwt))
        ).status_code == 403
        assert (
            await client.get("/tenant-stats/consumption", headers=_auth(member_jwt))
        ).status_code == 403
        assert (
            await client.get("/tenant-stats/runs", headers=_auth(member_jwt))
        ).status_code == 403


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A never sees tenant B's executions
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    jwt_a = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant_a, slug="alpha")

    # B owns a project + agent + task + a costly execution.
    project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="PB")
    agent_b = await _seed_agent(migrations_pg_dsn, tenant=tenant_b, name="B-agent", role="backend")
    task_b = await _seed_task(
        migrations_pg_dsn, tenant=tenant_b, project_id=project_b, plan_id=None, title="B-task"
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_b,
        task_id=task_b,
        agent_id=agent_b,
        status="done",
        total_tokens=9999,
        total_cost_usd=1.23,
        duration_ms=4000,
        steps_log=_model_steps("b-model", tokens_in=8000, tokens_out=1999, cost=1.23),
    )

    async with _client(configured_app) as client:
        dash = await client.get("/tenant-stats/dashboard", headers=_auth(jwt_a))
        assert dash.status_code == 200, dash.text
        body = dash.json()
        assert body["total_runs"] == 0
        assert body["total_cost_usd"] == "0.000000"
        assert body["by_agent"] == []
        assert body["top_agents"] == []

        cons = await client.get("/tenant-stats/consumption", headers=_auth(jwt_a))
        assert cons.status_code == 200, cons.text
        cbody = cons.json()
        assert cbody["run_count"] == 0
        assert cbody["accumulated_cost_usd"] == "0.000000"
        assert cbody["total_tokens"] == 0
        assert cbody["total_tokens_input"] == 0
        assert cbody["costliest_run"] is None

        runs = await client.get("/tenant-stats/runs", headers=_auth(jwt_a))
        assert runs.status_code == 200, runs.text
        assert runs.json() == []


# ---------------------------------------------------------------------------
# Member-facing GET /runs (Work menu) — open to ANY tenant member, same query
# + tenant isolation as the admin explorer (routers/runs.py, runs-visor A2).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_member_can_list_runs(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """A plain member is DENIED /tenant-stats/runs but ALLOWED /runs."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _uid, member_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, plan_id=None, title="T"
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=None,
        status="done",
        total_cost_usd=0.5,
        duration_ms=1000,
    )

    async with _client(configured_app) as client:
        assert (
            await client.get("/tenant-stats/runs", headers=_auth(member_jwt))
        ).status_code == 403
        resp = await client.get("/runs", headers=_auth(member_jwt))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["task_title"] == "T"


@pytest.mark.asyncio
async def test_member_runs_filter_by_task(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """``/runs?task_id=`` narrows to one task — the Kanban run-history panel."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    _uid, member_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task1 = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, plan_id=None, title="T1"
    )
    task2 = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, plan_id=None, title="T2"
    )
    await _seed_execution(migrations_pg_dsn, tenant=tenant, task_id=task1, agent_id=None)
    await _seed_execution(migrations_pg_dsn, tenant=tenant, task_id=task2, agent_id=None)

    async with _client(configured_app) as client:
        resp = await client.get(f"/runs?task_id={task1}", headers=_auth(member_jwt))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["task_id"] == str(task1)


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_member_runs_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """A member of tenant A never sees tenant B's runs through /runs (RLS)."""
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    _uid, member_jwt_a = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_a,
        email="member@alpha.example.com",
        role="tenant_user",
    )
    project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="PB")
    task_b = await _seed_task(
        migrations_pg_dsn, tenant=tenant_b, project_id=project_b, plan_id=None, title="B-task"
    )
    await _seed_execution(
        migrations_pg_dsn, tenant=tenant_b, task_id=task_b, agent_id=None, status="done"
    )

    async with _client(configured_app) as client:
        resp = await client.get("/runs", headers=_auth(member_jwt_a))
        assert resp.status_code == 200, resp.text
        assert resp.json() == []
