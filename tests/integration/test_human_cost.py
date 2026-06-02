"""Integration tests for human cost imputation + dashboard segmentation (task_16_12).

Human tasks (``agent_type='human'``) log their work in ``human_work_sessions``
(the Execution-equivalent audit trail). This module's
:func:`api_server.budgets.human_cost.compute_human_cost_usd` turns those sessions
into a CANONICAL-USD human cost (``hours_logged * hourly_rate``), and the 13.7
dashboard (``GET /tenant-stats/consumption``) SEGMENTS AI cost vs human cost.

What this verifies against the REAL Postgres (dev stack on PG 15432):

  * the per-scope human cost = sum of ``hours_logged * rate`` where the rate +
    currency come from the task's assigned Human Agent's ``human_agent_config``,
    falling back to ``DEFAULT_HOURLY_RATE_EUR`` (50 EUR) when none is set; a
    session with NULL ``hours_logged`` contributes 0;
  * a non-USD configured rate is converted to USD via the ``exchange_rates`` FX
    catalog at the session's own date (here USD-priced rates keep the maths
    exact; a seeded EUR rate exercises the conversion);
  * the dashboard ``/tenant-stats/consumption`` returns ``ai_cost_usd`` (the
    executions roll-up), ``human_cost_usd`` (the sessions roll-up) and their
    ``total_cost_usd`` — i.e. the two are segmented, not conflated;
  * cross-tenant (@pytest.mark.cross_tenant): tenant A's human cost never shows
    in tenant B's roll-up, and B's sessions never leak into A's dashboard.

The human roll-up runs on the caller's TENANT-SCOPED RLS session with a
defence-in-depth ``tenant_id ==`` predicate.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import api_server.db.domain  # noqa: F401  (resolve FKs the human-cost joins traverse)
import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# A fixed "now" so the consumption window (default 90d) and seeded session dates
# are deterministic. Sessions are seeded on this day so they always fall inside.
_NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_work_sessions, human_task_assignments, human_agent_config,"
            " exchange_rates, executions, tasks, plans, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


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


async def _seed_admin_jwt(dsn: str, redis_url: str, *, tenant: UUID, slug: str) -> str:
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin)"
            " VALUES ($1, $2, 'x', false)",
            user_id,
            f"admin@{slug}.example.com",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active)"
            " VALUES ($1, $2, $3, 'tenant_admin', true)",
            uuid4(),
            tenant,
            user_id,
        )
    finally:
        await conn.close()

    sid = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(sid, user_id=user_id, tenant_id=tenant, ttl_seconds=3600)
    finally:
        await redis.aclose()
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant)


async def _seed_user(dsn: str, *, tenant: UUID, email: str) -> UUID:
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')",
            user_id,
            email,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_user')",
            uuid4(),
            tenant,
            user_id,
        )
    finally:
        await conn.close()
    return user_id


async def _seed_project(
    dsn: str, *, tenant: UUID, name: str, includes_human_cost: bool = False
) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, budget_includes_human_cost)"
            " VALUES ($1, $2, $3, 'active', $4)",
            project_id,
            tenant,
            name,
            includes_human_cost,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_human_agent(
    dsn: str,
    *,
    tenant: UUID,
    user_id: UUID,
    name: str,
    hourly_rate: Decimal | None,
    rate_currency: str | None,
) -> UUID:
    """A human Agent + its human_agent_config (rate + currency optional)."""
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt,"
            " model_config, scope, is_template, project_id)"
            " VALUES ($1, $2, $3, 'human', 'reviewer', 'h', '{}'::jsonb,"
            " 'global_tenant_template', true, NULL)",
            agent_id,
            tenant,
            name,
        )
        await conn.execute(
            "INSERT INTO human_agent_config (id, tenant_id, agent_id, assignment_mode,"
            " assigned_user_id, hourly_rate, hourly_rate_currency, acceptance_timeout_hours,"
            " notification_channels)"
            " VALUES ($1, $2, $3, 'specific_user', $4, $5, $6, 24, '[]'::jsonb)",
            uuid4(),
            tenant,
            agent_id,
            user_id,
            hourly_rate,
            rate_currency,
        )
    finally:
        await conn.close()
    return agent_id


async def _seed_human_task(
    dsn: str,
    *,
    tenant: UUID,
    project_id: UUID,
    plan_id: UUID | None,
    agent_id: UUID | None,
    title: str,
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status,"
            " assigned_agent_id) VALUES ($1, $2, $3, $4, $5, 'done', $6)",
            task_id,
            tenant,
            project_id,
            plan_id,
            title,
            agent_id,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_work_session(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    user_id: UUID | None,
    hours_logged: Decimal | None,
    start_at: datetime = _NOW,
) -> UUID:
    session_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO human_work_sessions (id, tenant_id, task_id, user_id, start_at,"
            " end_at, hours_logged, output_files_attached)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, '[]'::jsonb)",
            session_id,
            tenant,
            task_id,
            user_id,
            start_at,
            start_at + timedelta(hours=1),
            hours_logged,
        )
    finally:
        await conn.close()
    return session_id


async def _seed_exchange_rate(
    dsn: str, *, currency: str, rate_vs_usd: Decimal, as_of: datetime
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date, source)"
            " VALUES ($1, $2, $3, $4, 'test')",
            uuid4(),
            currency.upper(),
            rate_vs_usd,
            as_of.date(),
        )
    finally:
        await conn.close()


async def _open_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


# ---------------------------------------------------------------------------
# App fixture
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


# ===========================================================================
# Human cost computation (direct, RLS session)
# ===========================================================================
@pytest.mark.asyncio
async def test_human_cost_config_rate_and_fallback(
    configured_app, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Sessions priced at the agent's USD rate + the EUR default fallback;
    a NULL-hours session adds 0."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="hc-rate")
    user = await _seed_user(migrations_pg_dsn, tenant=tenant, email="u@hc.test")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")

    # Agent A: configured 100 USD/h. Two sessions: 2h + 0.5h = 2.5h -> 250 USD.
    agent_usd = await _seed_human_agent(
        migrations_pg_dsn,
        tenant=tenant,
        user_id=user,
        name="USD agent",
        hourly_rate=Decimal("100"),
        rate_currency="USD",
    )
    task_usd = await _seed_human_task(
        migrations_pg_dsn,
        tenant=tenant,
        project_id=project,
        plan_id=None,
        agent_id=agent_usd,
        title="USD task",
    )
    await _seed_work_session(
        migrations_pg_dsn, tenant=tenant, task_id=task_usd, user_id=user, hours_logged=Decimal("2")
    )
    await _seed_work_session(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task_usd,
        user_id=user,
        hours_logged=Decimal("0.5"),
    )
    # A NULL-hours session contributes 0 cost but counts as a session.
    await _seed_work_session(
        migrations_pg_dsn, tenant=tenant, task_id=task_usd, user_id=user, hours_logged=None
    )

    # Agent B: NO configured rate -> fall back to DEFAULT_HOURLY_RATE_EUR (50 EUR).
    # 3h * 50 EUR. With 1 EUR = 2 USD-per-EUR... rate_vs_usd is units of EUR per
    # 1 USD; seed 1.25 (1 USD = 1.25 EUR) so 150 EUR / 1.25 = 120 USD.
    agent_default = await _seed_human_agent(
        migrations_pg_dsn,
        tenant=tenant,
        user_id=user,
        name="No-rate agent",
        hourly_rate=None,
        rate_currency=None,
    )
    task_default = await _seed_human_task(
        migrations_pg_dsn,
        tenant=tenant,
        project_id=project,
        plan_id=None,
        agent_id=agent_default,
        title="Default-rate task",
    )
    await _seed_work_session(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task_default,
        user_id=user,
        hours_logged=Decimal("3"),
    )
    await _seed_exchange_rate(
        migrations_pg_dsn, currency="EUR", rate_vs_usd=Decimal("1.25"), as_of=_NOW
    )

    from api_server.budgets.human_cost import compute_human_cost_usd

    engine, session = await _open_session(app_database_url, tenant)
    try:
        scope = await compute_human_cost_usd(session, tenant_id=tenant, project_id=project)
        # USD: 2.5h * 100 = 250. EUR default: 3h * 50 EUR = 150 EUR / 1.25 = 120 USD.
        assert scope.human_cost_usd == Decimal("370.000000")
        assert scope.hours_logged == Decimal("5.5")
        assert scope.session_count == 4  # 3 USD-task sessions + 1 default-task session
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# Dashboard segments AI cost vs human cost
# ===========================================================================
@pytest.mark.asyncio
async def test_dashboard_segments_ai_vs_human(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="hc-dash")
    jwt = await _seed_admin_jwt(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="hc-dash")
    user = await _seed_user(migrations_pg_dsn, tenant=tenant, email="worker@hc.test")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")

    # AI side: one execution, 0.12 USD.
    ai_agent = uuid4()
    ai_task = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope)"
            " VALUES ($1, $2, 'AI', 'backend', 'h', 'global_tenant_template')",
            ai_agent,
            tenant,
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status)"
            " VALUES ($1, $2, $3, NULL, 'AI task', 'done')",
            ai_task,
            tenant,
            project,
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, steps_log,"
            " total_tokens, total_cost_usd, started_at, completed_at, created_at)"
            " VALUES ($1, $2, $3, $4, 'done', '[]'::jsonb, 100, 0.12, $5, $5, $5)",
            uuid4(),
            tenant,
            ai_task,
            ai_agent,
            datetime.now(tz=UTC),
        )
    finally:
        await conn.close()

    # Human side: 100 USD/h agent, 1.5h -> 150 USD.
    h_agent = await _seed_human_agent(
        migrations_pg_dsn,
        tenant=tenant,
        user_id=user,
        name="Legal",
        hourly_rate=Decimal("100"),
        rate_currency="USD",
    )
    h_task = await _seed_human_task(
        migrations_pg_dsn,
        tenant=tenant,
        project_id=project,
        plan_id=None,
        agent_id=h_agent,
        title="Legal review",
    )
    await _seed_work_session(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=h_task,
        user_id=user,
        hours_logged=Decimal("1.5"),
        start_at=datetime.now(tz=UTC),
    )

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/tenant-stats/consumption", headers=_auth(jwt))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # AI cost from the executions roll-up (back-compat: accumulated_cost_usd).
    assert body["accumulated_cost_usd"] == "0.120000"
    assert body["ai_cost_usd"] == "0.120000"
    # Human cost from the work sessions: 1.5h * 100 USD = 150.
    assert body["human_cost_usd"] == "150.000000"
    assert body["total_cost_usd"] == "150.120000"
    # hours_logged is Numeric(8,2) so it serialises with 2 decimals.
    assert Decimal(body["human_hours_logged"]) == Decimal("1.5")


# ===========================================================================
# Cross-tenant: A's human cost never appears in B's roll-up
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_human_cost_is_tenant_scoped(
    configured_app, migrations_pg_dsn: str, test_redis_url: str, app_database_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)

    # Tenant A: 200 USD of human work.
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="hc-alpha")
    user_a = await _seed_user(migrations_pg_dsn, tenant=tenant_a, email="a@hc.test")
    project_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="A")
    agent_a = await _seed_human_agent(
        migrations_pg_dsn,
        tenant=tenant_a,
        user_id=user_a,
        name="A agent",
        hourly_rate=Decimal("100"),
        rate_currency="USD",
    )
    task_a = await _seed_human_task(
        migrations_pg_dsn,
        tenant=tenant_a,
        project_id=project_a,
        plan_id=None,
        agent_id=agent_a,
        title="A task",
    )
    await _seed_work_session(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        user_id=user_a,
        hours_logged=Decimal("2"),
        start_at=datetime.now(tz=UTC),
    )

    # Tenant B: no human work at all.
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="hc-bravo")
    jwt_b = await _seed_admin_jwt(
        migrations_pg_dsn, test_redis_url, tenant=tenant_b, slug="hc-bravo"
    )

    # B's dashboard sees ZERO human cost (A's never leaks).
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get("/tenant-stats/consumption", headers=_auth(jwt_b))
    assert resp.status_code == 200, resp.text
    assert resp.json()["human_cost_usd"] == "0.000000"

    # And A's own RLS session sees exactly its 200 USD.
    from api_server.budgets.human_cost import compute_human_cost_usd

    engine, session = await _open_session(app_database_url, tenant_a)
    try:
        scope = await compute_human_cost_usd(session, tenant_id=tenant_a)
        assert scope.human_cost_usd == Decimal("200.000000")
    finally:
        await session.close()
        await engine.dispose()

    # B's RLS session sees nothing (the work session is A's).
    engine_b, session_b = await _open_session(app_database_url, tenant_b)
    try:
        scope_b = await compute_human_cost_usd(session_b, tenant_id=tenant_b)
        assert scope_b.human_cost_usd == Decimal("0.000000")
        assert scope_b.session_count == 0
    finally:
        await session_b.close()
        await engine_b.dispose()
