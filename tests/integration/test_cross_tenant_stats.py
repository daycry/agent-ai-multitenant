"""Integration tests for the CROSS-TENANT comparison (Plan 14 task_14_15).

``GET /admin/cross-tenant-stats`` is the ONE deliberately cross-tenant surface
in Plan 14: a platform operator (System Admin) compares how each tenant's agents
perform — success rate, cost, throughput — side by side, over the BYPASSRLS
admin session. Every other Plan 14 stats / dashboard / export surface is
tenant-scoped (RLS); this one is System-Admin-ONLY.

Coverage:

  * a System Admin gets per-tenant comparative stats spanning MULTIPLE tenants
    (one aggregate row per tenant: success rate, mean cost, total cost,
    throughput; roll-up totals across tenants);
  * RBAC: a tenant_admin and a tenant_user are each 403 (the gate fires before
    any query — this is platform-operator-only);
  * the response is strictly AGGREGATE — counts / rates / sums only, no raw
    per-execution rows and no per-tenant secret / PII (no prompts / completions /
    steps_log / output) leak across tenants;
  * out-of-range window is a clean 422.

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
from uuid6 import uuid7

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


async def _seed_user(dsn: str, *, email: str, is_system_admin: bool = False) -> UUID:
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, $4)",
            user_id,
            email,
            "x",
            is_system_admin,
        )
    finally:
        await conn.close()
    return user_id


async def _seed_membership(dsn: str, *, tenant_id: UUID, user_id: UUID, role: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
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


async def _mint_jwt(
    redis_url: str, *, user_id: UUID, tenant_id: UUID | None, is_system_admin: bool = False
) -> str:
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    return encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


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


async def _seed_task(dsn: str, *, tenant: UUID, project_id: UUID, title: str) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, retry_count) "
            "VALUES ($1, $2, $3, NULL, $4, 'done', 0)",
            task_id,
            tenant,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


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


def _model_steps(model: str, *, tokens_in: int, tokens_out: int, cost: float) -> list[dict]:
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
            # A secret-ish payload that MUST NOT leak through the aggregate.
            "prompt": "SECRET-PROMPT-DO-NOT-LEAK",
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
    output: str | None = None,
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
            "(id, tenant_id, task_id, agent_id, status, output, steps_log, total_tokens, "
            " total_cost_usd, started_at, completed_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12)",
            execution_id,
            tenant,
            task_id,
            agent_id,
            status,
            output,
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
# A System Admin gets per-tenant comparative stats spanning MULTIPLE tenants
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_system_admin_gets_per_tenant_comparison(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    sysadmin = await _seed_user(migrations_pg_dsn, email="root@platform.test", is_system_admin=True)
    jwt = await _mint_jwt(test_redis_url, user_id=sysadmin, tenant_id=None, is_system_admin=True)

    # Tenant A: 2 done + 1 aborted (success 2/3 = 0.667), cost 0.02+0.04+0.01.
    proj_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="PA")
    agent_a = await _seed_agent(migrations_pg_dsn, tenant=tenant_a, name="A-be", role="backend")
    task_a = await _seed_task(migrations_pg_dsn, tenant=tenant_a, project_id=proj_a, title="TA")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        agent_id=agent_a,
        status="done",
        total_tokens=1000,
        total_cost_usd=0.02,
        duration_ms=1000,
        steps_log=_model_steps("claude-opus", tokens_in=700, tokens_out=300, cost=0.02),
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        agent_id=agent_a,
        status="done",
        total_tokens=2000,
        total_cost_usd=0.04,
        duration_ms=3000,
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        agent_id=agent_a,
        status="aborted",
        total_tokens=500,
        total_cost_usd=0.01,
        duration_ms=None,
    )

    # Tenant B: 1 done (success 1/1 = 1.000), cost 1.23.
    proj_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="PB")
    agent_b = await _seed_agent(migrations_pg_dsn, tenant=tenant_b, name="B-be", role="backend")
    task_b = await _seed_task(migrations_pg_dsn, tenant=tenant_b, project_id=proj_b, title="TB")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_b,
        task_id=task_b,
        agent_id=agent_b,
        status="done",
        total_tokens=9999,
        total_cost_usd=1.23,
        duration_ms=4000,
        output="B-CONFIDENTIAL-OUTPUT",
        steps_log=_model_steps("b-model", tokens_in=8000, tokens_out=1999, cost=1.23),
    )

    async with _client(configured_app) as client:
        resp = await client.get("/admin/cross-tenant-stats?window_days=30", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Both tenants present, ranked by success rate (B=1.0 first, A=0.667).
        assert body["tenant_count"] == 2
        assert body["currency"] == "USD"
        assert len(body["tenants"]) == 2
        rows = {r["tenant_slug"]: r for r in body["tenants"]}
        assert set(rows) == {"alpha", "bravo"}

        a = rows["alpha"]
        assert a["tenant_name"] == "Alpha"
        assert a["run_count"] == 3
        assert a["succeeded_runs"] == 2
        assert a["success_rate"] == "0.667"
        assert a["total_cost_usd"] == "0.070000"
        # mean duration over the 2 finished runs (1000+3000)/2 = 2000.00 ms.
        assert a["mean_duration_ms"] == "2000.00"
        # throughput = 3 runs / 30 days = 0.100 runs/day.
        assert a["throughput_runs_per_day"] == "0.100"

        b = rows["bravo"]
        assert b["run_count"] == 1
        assert b["succeeded_runs"] == 1
        assert b["success_rate"] == "1.000"
        assert b["total_cost_usd"] == "1.230000"

        # Leaderboard order: B (1.000) before A (0.667).
        assert body["tenants"][0]["tenant_slug"] == "bravo"
        assert body["tenants"][1]["tenant_slug"] == "alpha"

        # Roll-up totals across BOTH tenants.
        assert body["total_runs"] == 4
        assert body["total_succeeded_runs"] == 3
        assert body["overall_success_rate"] == "0.750"
        assert body["total_cost_usd"] == "1.300000"


# ---------------------------------------------------------------------------
# The response is strictly AGGREGATE — no raw rows / secrets leak
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_response_is_aggregate_no_pii_leak(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    sysadmin = await _seed_user(migrations_pg_dsn, email="root@platform.test", is_system_admin=True)
    jwt = await _mint_jwt(test_redis_url, user_id=sysadmin, tenant_id=None, is_system_admin=True)
    proj = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=proj, title="T")
    exec_id = await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        agent_id=None,
        status="done",
        total_tokens=100,
        total_cost_usd=0.5,
        duration_ms=500,
        output="CONFIDENTIAL-OUTPUT-DO-NOT-LEAK",
        steps_log=_model_steps("m", tokens_in=80, tokens_out=20, cost=0.5),
    )

    async with _client(configured_app) as client:
        resp = await client.get("/admin/cross-tenant-stats", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        raw = resp.text
        body = resp.json()

        # Aggregate only: one row per tenant, no per-execution payload anywhere.
        assert body["tenant_count"] == 1
        row = body["tenants"][0]
        assert set(row) == {
            "tenant_id",
            "tenant_name",
            "tenant_slug",
            "run_count",
            "succeeded_runs",
            "success_rate",
            "mean_duration_ms",
            "mean_cost_usd",
            "total_cost_usd",
            "total_tokens",
            "throughput_runs_per_day",
        }
        # No raw execution id, no prompt/output text, no steps_log.
        assert str(exec_id) not in raw
        assert "CONFIDENTIAL-OUTPUT-DO-NOT-LEAK" not in raw
        assert "SECRET-PROMPT-DO-NOT-LEAK" not in raw
        assert "steps_log" not in raw
        assert "output" not in raw


# ---------------------------------------------------------------------------
# RBAC: a tenant_admin and a tenant_user are each 403
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_roles_are_forbidden_403(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="alpha")

    admin_uid = await _seed_user(migrations_pg_dsn, email="admin@alpha.test")
    await _seed_membership(
        migrations_pg_dsn, tenant_id=tenant, user_id=admin_uid, role="tenant_admin"
    )
    admin_jwt = await _mint_jwt(
        test_redis_url, user_id=admin_uid, tenant_id=tenant, is_system_admin=False
    )

    member_uid = await _seed_user(migrations_pg_dsn, email="member@alpha.test")
    await _seed_membership(
        migrations_pg_dsn, tenant_id=tenant, user_id=member_uid, role="tenant_user"
    )
    member_jwt = await _mint_jwt(
        test_redis_url, user_id=member_uid, tenant_id=tenant, is_system_admin=False
    )

    async with _client(configured_app) as client:
        # A tenant_admin (NOT a system admin) is 403 — platform-operator-only.
        admin_resp = await client.get("/admin/cross-tenant-stats", headers=_auth(admin_jwt))
        assert admin_resp.status_code == 403, admin_resp.text

        # A plain tenant_user is 403 too.
        member_resp = await client.get("/admin/cross-tenant-stats", headers=_auth(member_jwt))
        assert member_resp.status_code == 403, member_resp.text


# ---------------------------------------------------------------------------
# Out-of-range window is a clean 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_window_out_of_range_is_422(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    sysadmin = await _seed_user(migrations_pg_dsn, email="root@platform.test", is_system_admin=True)
    jwt = await _mint_jwt(test_redis_url, user_id=sysadmin, tenant_id=None, is_system_admin=True)

    async with _client(configured_app) as client:
        bad = await client.get("/admin/cross-tenant-stats?window_days=0", headers=_auth(jwt))
        assert bad.status_code == 422, bad.text
