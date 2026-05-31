"""Integration tests for the tenant eval QUALITY dashboard (Plan 14 task_14_11).

The aggregation read surface behind the quality dashboard:

  * ``GET /eval-quality/dashboard`` — aggregated quality over a window: headline
    totals + the per-AGENT / per-PROMPT-VERSION / per-DATASET / per-CRITERION
    breakdowns + the per-day pass-rate trend, over COMPLETED runs only;
  * ``GET /eval-quality/runs`` — the paginated, filterable run history.

Both are JWT-authenticated, gated on ``tenant_admin`` and run on a tenant-scoped
RLS session (this is a TENANT dashboard — cross-tenant comparison is the
System-Admin-only task_14_15). Costs are canonical USD.

Coverage:

  * aggregation correctness: items-weighted pass rate, per-agent / per-prompt /
    per-dataset / per-criterion breakdowns, headline totals;
  * the per-criterion breakdown unnests the ``criterion_scores`` JSONB;
  * the window excludes runs older than ``window_days`` and only counts
    completed runs (a failed run is ignored);
  * run-history list is paginated + filterable (by agent / status);
  * RBAC: a plain member (tenant_user) is denied (403);
  * cross-tenant (@pytest.mark.cross_tenant): tenant A never sees tenant B's
    runs / results in either endpoint.

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


async def _seed_dataset(dsn: str, *, tenant: UUID, name: str) -> UUID:
    dataset_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_datasets (id, tenant_id, name, kind) VALUES ($1, $2, $3, 'golden')",
            dataset_id,
            tenant,
            name,
        )
    finally:
        await conn.close()
    return dataset_id


async def _seed_criterion(dsn: str, *, tenant: UUID, dataset_id: UUID, name: str) -> UUID:
    crit_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_criteria (id, tenant_id, dataset_id, name, judge_instruction) "
            "VALUES ($1, $2, $3, $4, 'rubric')",
            crit_id,
            tenant,
            dataset_id,
            name,
        )
    finally:
        await conn.close()
    return crit_id


async def _seed_run(
    dsn: str,
    *,
    tenant: UUID,
    dataset_id: UUID,
    agent_id: UUID | None,
    prompt_version: str | None,
    total_items: int,
    passed_items: int,
    status: str = "completed",
    finished_at: datetime | None = None,
    mean_cost_usd: float | None = None,
    mean_tokens: float | None = None,
) -> UUID:
    run_id = uuid4()
    pass_rate = round(passed_items / total_items, 3) if total_items else None
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_runs "
            "(id, tenant_id, dataset_id, status, subject_agent_id, subject_prompt_version, "
            " finished_at, total_items, passed_items, pass_rate, mean_cost_usd, mean_tokens) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
            run_id,
            tenant,
            dataset_id,
            status,
            agent_id,
            prompt_version,
            finished_at or datetime.now(tz=UTC),
            total_items,
            passed_items,
            pass_rate,
            mean_cost_usd,
            mean_tokens,
        )
    finally:
        await conn.close()
    return run_id


async def _seed_result(
    dsn: str,
    *,
    tenant: UUID,
    run_id: UUID,
    verdict: str,
    criterion_scores: list[dict[str, object]],
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_results (id, tenant_id, run_id, verdict, criterion_scores) "
            "VALUES ($1, $2, $3, $4, $5::jsonb)",
            uuid4(),
            tenant,
            run_id,
            verdict,
            json.dumps(criterion_scores),
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE eval_results, eval_runs, eval_criteria, eval_dataset_items, "
            "eval_datasets, agents, executions, tasks, projects, user_org_memberships, "
            "organizations, users RESTART IDENTITY CASCADE"
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
# Aggregation correctness
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dashboard_aggregation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")

    agent_be = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Backend", role="backend")
    agent_fe = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Frontend", role="frontend")
    dataset_login = await _seed_dataset(migrations_pg_dsn, tenant=tenant, name="Login golden")
    dataset_cart = await _seed_dataset(migrations_pg_dsn, tenant=tenant, name="Cart golden")
    crit_pep8 = await _seed_criterion(
        migrations_pg_dsn, tenant=tenant, dataset_id=dataset_login, name="PEP 8"
    )

    # Backend, v1: 10 items, 8 passed. Backend, v2: 10 items, 9 passed.
    run_be_v1 = await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset_login,
        agent_id=agent_be,
        prompt_version="v1",
        total_items=10,
        passed_items=8,
        mean_cost_usd=0.02,
        mean_tokens=1000.0,
    )
    await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset_login,
        agent_id=agent_be,
        prompt_version="v2",
        total_items=10,
        passed_items=9,
        mean_cost_usd=0.04,
        mean_tokens=2000.0,
    )
    # Frontend, v1, different dataset: 5 items, 5 passed.
    await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset_cart,
        agent_id=agent_fe,
        prompt_version="v1",
        total_items=5,
        passed_items=5,
    )
    # A FAILED run is ignored by the dashboard (no trustworthy roll-up).
    await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset_login,
        agent_id=agent_be,
        prompt_version="v1",
        total_items=99,
        passed_items=0,
        status="failed",
    )
    # Per-criterion results on run_be_v1: 3 scored on PEP 8, 2 passed.
    await _seed_result(
        migrations_pg_dsn,
        tenant=tenant,
        run_id=run_be_v1,
        verdict="pass",
        criterion_scores=[{"criterion_id": str(crit_pep8), "passed": True, "score": 0.9}],
    )
    await _seed_result(
        migrations_pg_dsn,
        tenant=tenant,
        run_id=run_be_v1,
        verdict="pass",
        criterion_scores=[{"criterion_id": str(crit_pep8), "passed": True, "score": 0.8}],
    )
    await _seed_result(
        migrations_pg_dsn,
        tenant=tenant,
        run_id=run_be_v1,
        verdict="fail",
        criterion_scores=[{"criterion_id": str(crit_pep8), "passed": False, "score": 0.2}],
    )

    async with _client(configured_app) as client:
        resp = await client.get("/eval-quality/dashboard", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # Headline totals exclude the failed run: 25 items, 22 passed across 3 runs.
        assert body["total_runs"] == 3
        assert body["total_items"] == 25
        assert body["passed_items"] == 22
        assert body["overall_pass_rate"] == "0.880"
        assert body["currency"] == "USD"

        # by_agent: backend has 2 runs (20 items, 17 passed = 0.850).
        by_agent = {a["agent_name"]: a for a in body["by_agent"]}
        assert by_agent["Backend"]["run_count"] == 2
        assert by_agent["Backend"]["total_items"] == 20
        assert by_agent["Backend"]["passed_items"] == 17
        assert by_agent["Backend"]["pass_rate"] == "0.850"
        assert by_agent["Backend"]["agent_role"] == "backend"
        # mean cost USD is the mean of per-run means (0.02, 0.04) = 0.030000.
        assert by_agent["Backend"]["mean_cost_usd"] == "0.030000"
        assert by_agent["Frontend"]["pass_rate"] == "1.000"

        # by_prompt_version: v1 spans 2 runs (login be + cart fe) = 15 items, 13 passed.
        by_version = {v["subject_prompt_version"]: v for v in body["by_prompt_version"]}
        assert by_version["v1"]["total_items"] == 15
        assert by_version["v1"]["passed_items"] == 13
        assert by_version["v2"]["total_items"] == 10
        assert by_version["v2"]["passed_items"] == 9

        # by_dataset: login (be v1 + be v2) = 20 items, 17 passed.
        by_dataset = {d["dataset_name"]: d for d in body["by_dataset"]}
        assert by_dataset["Login golden"]["total_items"] == 20
        assert by_dataset["Login golden"]["passed_items"] == 17
        assert by_dataset["Cart golden"]["pass_rate"] == "1.000"

        # by_criterion: PEP 8 was scored 3 times, passed 2 = 0.667.
        by_crit = {c["criterion_name"]: c for c in body["by_criterion"]}
        assert by_crit["PEP 8"]["scored"] == 3
        assert by_crit["PEP 8"]["passed"] == 2
        assert by_crit["PEP 8"]["pass_rate"] == "0.667"
        assert by_crit["PEP 8"]["criterion_id"] == str(crit_pep8)

        # trend: at least one day with the completed runs.
        assert len(body["trend"]) >= 1
        assert sum(p["total_items"] for p in body["trend"]) == 25


# ---------------------------------------------------------------------------
# The window excludes old runs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dashboard_window_excludes_old_runs(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant=tenant, name="ds")

    # A recent run inside the 30-day window, and one 100 days ago (outside).
    await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset,
        agent_id=None,
        prompt_version="v1",
        total_items=4,
        passed_items=4,
        finished_at=datetime.now(tz=UTC) - timedelta(days=2),
    )
    await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset,
        agent_id=None,
        prompt_version="v1",
        total_items=4,
        passed_items=0,
        finished_at=datetime.now(tz=UTC) - timedelta(days=100),
    )

    async with _client(configured_app) as client:
        resp = await client.get("/eval-quality/dashboard?window_days=30", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_runs"] == 1
        assert body["total_items"] == 4
        assert body["overall_pass_rate"] == "1.000"

        # Out-of-range window is a clean 422, not a silent clamp.
        bad = await client.get("/eval-quality/dashboard?window_days=0", headers=_auth(jwt))
        assert bad.status_code == 422, bad.text


# ---------------------------------------------------------------------------
# Run-history list: paginated + filterable
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_history_filter_and_paginate(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")
    agent = await _seed_agent(migrations_pg_dsn, tenant=tenant, name="Backend", role="backend")
    dataset = await _seed_dataset(migrations_pg_dsn, tenant=tenant, name="ds")

    for n in range(3):
        await _seed_run(
            migrations_pg_dsn,
            tenant=tenant,
            dataset_id=dataset,
            agent_id=agent,
            prompt_version=f"v{n}",
            total_items=2,
            passed_items=n,
        )
    # A run with no agent + failed status.
    await _seed_run(
        migrations_pg_dsn,
        tenant=tenant,
        dataset_id=dataset,
        agent_id=None,
        prompt_version="vX",
        total_items=2,
        passed_items=0,
        status="failed",
    )

    async with _client(configured_app) as client:
        # All 4 runs, with resolved labels.
        all_runs = await client.get("/eval-quality/runs", headers=_auth(jwt))
        assert all_runs.status_code == 200, all_runs.text
        assert len(all_runs.json()) == 4
        named = [r for r in all_runs.json() if r["agent_name"] == "Backend"]
        assert len(named) == 3
        assert named[0]["dataset_name"] == "ds"

        # Filter by agent.
        by_agent = await client.get(f"/eval-quality/runs?agent_id={agent}", headers=_auth(jwt))
        assert by_agent.status_code == 200, by_agent.text
        assert len(by_agent.json()) == 3
        assert all(r["subject_agent_id"] == str(agent) for r in by_agent.json())

        # Filter by status.
        failed = await client.get("/eval-quality/runs?status_filter=failed", headers=_auth(jwt))
        assert failed.status_code == 200, failed.text
        assert len(failed.json()) == 1
        assert failed.json()[0]["agent_name"] is None

        # Pagination.
        page1 = await client.get("/eval-quality/runs?limit=2&offset=0", headers=_auth(jwt))
        assert len(page1.json()) == 2
        page2 = await client.get("/eval-quality/runs?limit=2&offset=2", headers=_auth(jwt))
        assert len(page2.json()) == 2


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
            await client.get("/eval-quality/dashboard", headers=_auth(member_jwt))
        ).status_code == 403
        assert (
            await client.get("/eval-quality/runs", headers=_auth(member_jwt))
        ).status_code == 403


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A never sees tenant B's runs / results
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

    # B owns a dataset + agent + run + a scored result.
    agent_b = await _seed_agent(migrations_pg_dsn, tenant=tenant_b, name="B-agent", role="backend")
    dataset_b = await _seed_dataset(migrations_pg_dsn, tenant=tenant_b, name="b-golden")
    crit_b = await _seed_criterion(
        migrations_pg_dsn, tenant=tenant_b, dataset_id=dataset_b, name="b-crit"
    )
    run_b = await _seed_run(
        migrations_pg_dsn,
        tenant=tenant_b,
        dataset_id=dataset_b,
        agent_id=agent_b,
        prompt_version="v1",
        total_items=10,
        passed_items=10,
    )
    await _seed_result(
        migrations_pg_dsn,
        tenant=tenant_b,
        run_id=run_b,
        verdict="pass",
        criterion_scores=[{"criterion_id": str(crit_b), "passed": True, "score": 1.0}],
    )

    async with _client(configured_app) as client:
        # A's dashboard sees NOTHING of B's data.
        dash = await client.get("/eval-quality/dashboard", headers=_auth(jwt_a))
        assert dash.status_code == 200, dash.text
        body = dash.json()
        assert body["total_runs"] == 0
        assert body["total_items"] == 0
        assert body["overall_pass_rate"] is None
        assert body["by_agent"] == []
        assert body["by_criterion"] == []

        # A's run history is empty (B's run never leaks).
        runs = await client.get("/eval-quality/runs", headers=_auth(jwt_a))
        assert runs.status_code == 200, runs.text
        assert runs.json() == []
