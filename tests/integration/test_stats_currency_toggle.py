"""Integration tests for the runs-explorer currency toggle (Plan 11.1 task_11_1_03).

The ``GET /tenant-stats/runs`` explorer always returns the CANONICAL USD cost
(``total_cost_usd``, the stored value, never mutated) and, when the caller's
display currency is not USD, ALSO a per-row DISPLAY conversion computed on the
fly with the FX rate of **each run's own date** plus the applied rate (for
traceability). FX is display-only — the stored USD is untouched.

Coverage:

  * default (tenant display_currency=USD) → no conversion: the display fields
    (``display_currency`` / ``display_cost`` / ``applied_rate`` /
    ``applied_rate_date``) are ``None`` and ``total_cost_usd`` is canonical;
  * with display_currency=EUR (the tenant's stored currency) each row converts
    with the rate of THAT RUN'S date — two runs on different dates carry
    different applied rates / converted amounts — and the applied rate +
    rate-date travel with the row;
  * a run whose date has no rate on or before it falls back sanely (display
    fields ``None``; USD figure intact);
  * the ``display_currency`` query param overrides the tenant's stored currency;
  * tenant-scoped (@pytest.mark.cross_tenant): tenant A never sees tenant B's
    runs, and the toggle does not leak B's data.

exchange_rates is the GLOBAL catalog (seeded directly as the BYPASSRLS
migrations user); no network is touched (the ECB fetcher is task_11_1_02).

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str, display_currency: str | None = None) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        if display_currency is None:
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                tenant,
                slug.title(),
                slug,
            )
        else:
            await conn.execute(
                "INSERT INTO organizations (id, name, slug, display_currency)"
                " VALUES ($1, $2, $3, $4)",
                tenant,
                slug.title(),
                slug,
                display_currency,
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

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    return encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant)


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


async def _seed_task(dsn: str, *, tenant: UUID, project_id: UUID, title: str) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, retry_count)"
            " VALUES ($1, $2, $3, NULL, $4, 'done', 0)",
            task_id,
            tenant,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_execution(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    total_cost_usd: float,
    created_at: datetime,
) -> UUID:
    execution_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions"
            " (id, tenant_id, task_id, agent_id, status, steps_log, total_tokens,"
            "  total_cost_usd, started_at, completed_at, created_at)"
            " VALUES ($1, $2, $3, NULL, 'done', '[]'::jsonb, 0, $4, $5, $5, $5)",
            execution_id,
            tenant,
            task_id,
            Decimal(str(total_cost_usd)),
            created_at,
        )
    finally:
        await conn.close()
    return execution_id


async def _seed_rate(dsn: str, *, currency: str, rate: str, on: date) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date, source)"
            " VALUES ($1, $2, $3, $4, 'ecb')",
            uuid4(),
            currency,
            Decimal(rate),
            on,
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, projects, exchange_rates,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
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


# Two fixed run dates (with EUR rates seeded for each).
DAY_A = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)  # EUR rate 0.90
DAY_B = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)  # EUR rate 0.92


async def _grant() -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()


# ---------------------------------------------------------------------------
# Default (tenant display_currency = USD) → no conversion.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_default_usd_no_conversion(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _grant()
    # Tenant explicitly on USD → conversion is the identity / absent.
    tenant = await _seed_tenant(migrations_pg_dsn, slug="usdco", display_currency="USD")
    jwt = await _seed_admin_jwt(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="usdco")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    # Even with a EUR rate present, a USD tenant gets no display conversion.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=DAY_B.date())
    await _seed_execution(
        migrations_pg_dsn, tenant=tenant, task_id=task, total_cost_usd=10.0, created_at=DAY_B
    )

    async with _client(configured_app) as client:
        resp = await client.get("/tenant-stats/runs?window_days=730", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert len(rows) == 1
        row = rows[0]
        assert row["total_cost_usd"] == "10.000000"
        # No conversion: the display fields are all null.
        assert row["display_currency"] is None
        assert row["display_cost"] is None
        assert row["applied_rate"] is None
        assert row["applied_rate_date"] is None


# ---------------------------------------------------------------------------
# Tenant display_currency = EUR → per-run-date conversion + applied rate.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_eur_converts_per_run_date(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _grant()
    tenant = await _seed_tenant(migrations_pg_dsn, slug="eurco", display_currency="EUR")
    jwt = await _seed_admin_jwt(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="eurco")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")

    # Two EUR rates on two days; conversion must pick EACH run's own date.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.90", on=DAY_A.date())
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=DAY_B.date())

    ex_a = await _seed_execution(
        migrations_pg_dsn, tenant=tenant, task_id=task, total_cost_usd=10.0, created_at=DAY_A
    )
    ex_b = await _seed_execution(
        migrations_pg_dsn, tenant=tenant, task_id=task, total_cost_usd=10.0, created_at=DAY_B
    )

    async with _client(configured_app) as client:
        resp = await client.get("/tenant-stats/runs?window_days=730", headers=_auth(jwt))
        assert resp.status_code == 200, resp.text
        rows = {r["id"]: r for r in resp.json()}

        # Run A (the 19th, rate 0.90): 10 USD → 9.00 EUR.
        row_a = rows[str(ex_a)]
        assert row_a["total_cost_usd"] == "10.000000"  # USD untouched
        assert row_a["display_currency"] == "EUR"
        assert Decimal(row_a["display_cost"]) == Decimal("9.00")
        assert Decimal(row_a["applied_rate"]) == Decimal("0.9000000000")
        assert row_a["applied_rate_date"] == DAY_A.date().isoformat()

        # Run B (the 20th, rate 0.92): 10 USD → 9.20 EUR — a DIFFERENT rate.
        row_b = rows[str(ex_b)]
        assert row_b["total_cost_usd"] == "10.000000"
        assert row_b["display_currency"] == "EUR"
        assert Decimal(row_b["display_cost"]) == Decimal("9.20")
        assert Decimal(row_b["applied_rate"]) == Decimal("0.9200000000")
        assert row_b["applied_rate_date"] == DAY_B.date().isoformat()


# ---------------------------------------------------------------------------
# The query param overrides the tenant's stored currency; a run with no rate
# for its date falls back sanely (no display fields, USD intact).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_query_override_and_missing_rate_fallback(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _grant()
    # Tenant stored on USD, but the request overrides to EUR.
    tenant = await _seed_tenant(migrations_pg_dsn, slug="ovr", display_currency="USD")
    jwt = await _seed_admin_jwt(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="ovr")
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")

    # A EUR rate exists ONLY on day B; day A has no rate on or before it.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=DAY_B.date())
    ex_priced = await _seed_execution(
        migrations_pg_dsn, tenant=tenant, task_id=task, total_cost_usd=10.0, created_at=DAY_B
    )
    # A run BEFORE any rate publish: no rate on/before → falls back sanely.
    early = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    ex_norate = await _seed_execution(
        migrations_pg_dsn, tenant=tenant, task_id=task, total_cost_usd=7.0, created_at=early
    )

    async with _client(configured_app) as client:
        resp = await client.get(
            "/tenant-stats/runs?window_days=730&display_currency=EUR", headers=_auth(jwt)
        )
        assert resp.status_code == 200, resp.text
        rows = {r["id"]: r for r in resp.json()}

        # Override took effect: the priced run converted at day B's rate.
        priced = rows[str(ex_priced)]
        assert priced["display_currency"] == "EUR"
        assert Decimal(priced["display_cost"]) == Decimal("9.20")
        assert priced["applied_rate_date"] == DAY_B.date().isoformat()

        # The run with no rate for its date: USD intact, display fields null.
        norate = rows[str(ex_norate)]
        assert norate["total_cost_usd"] == "7.000000"
        assert norate["display_currency"] is None
        assert norate["display_cost"] is None
        assert norate["applied_rate"] is None
        assert norate["applied_rate_date"] is None


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A never sees tenant B's runs; the toggle does not leak.
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    await _grant()
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha", display_currency="EUR")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo", display_currency="EUR")
    jwt_a = await _seed_admin_jwt(migrations_pg_dsn, test_redis_url, tenant=tenant_a, slug="alpha")

    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.92", on=DAY_B.date())

    # A has one run; B has one run. A must see only its own.
    project_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="PA")
    task_a = await _seed_task(migrations_pg_dsn, tenant=tenant_a, project_id=project_a, title="TA")
    ex_a = await _seed_execution(
        migrations_pg_dsn, tenant=tenant_a, task_id=task_a, total_cost_usd=10.0, created_at=DAY_B
    )

    project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="PB")
    task_b = await _seed_task(migrations_pg_dsn, tenant=tenant_b, project_id=project_b, title="TB")
    await _seed_execution(
        migrations_pg_dsn, tenant=tenant_b, task_id=task_b, total_cost_usd=99.0, created_at=DAY_B
    )

    async with _client(configured_app) as client:
        resp = await client.get("/tenant-stats/runs?window_days=730", headers=_auth(jwt_a))
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        # Only A's single run — B's is invisible (RLS).
        assert len(rows) == 1
        assert rows[0]["id"] == str(ex_a)
        # And it is correctly converted with the GLOBAL EUR rate.
        assert rows[0]["display_currency"] == "EUR"
        assert Decimal(rows[0]["display_cost"]) == Decimal("9.20")
