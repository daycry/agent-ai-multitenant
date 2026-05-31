"""Integration tests for budget consumption + threshold alerts + the
personal-assistant ``tenant_budget_status`` tool (Plan 11.1 task_11_1_05).

The budget evaluator sums the CANONICAL-USD cost of a tenant's / project's
executions within the active budget period, converts the (own-currency) cap
INTO USD, computes the percent used, and — when a platform-global threshold
(``[80, 90, 100]``) is newly crossed — fires ONE ``budget_alert`` event via the
Plan 10 notifier (MOCKED here with a fake dispatcher). The debounce is "one
alert per threshold per period per scope" (a ``budget_alert_states`` row). What
this verifies:

  * crossing 80 / 90 / 100 fires exactly one alert PER crossed threshold
    (notification enqueued, mocked);
  * staying under every threshold fires nothing;
  * a re-evaluation in the same period is DEBOUNCED (no re-fire); a NEW period
    re-arms (the period_start is part of the debounce key);
  * the assistant ``tenant_budget_status`` tool returns REAL budget data
    (spend, % of budget, period, status);
  * tenant/project scoped (@pytest.mark.cross_tenant): evaluating tenant B sees
    NONE of tenant A's spend, so A's budget can never alert B.

USD is canonical; the budget cap is converted to USD with the rate of the
period-start date (a seeded ECB row — no real network). RLS is exercised by
seeding as the BYPASSRLS migrations user and evaluating as the NOBYPASSRLS
app_user with ``app.tenant_id`` set.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy resolves the FKs the spend query
# joins (Execution.task_id -> tasks.id, Task.project_id -> projects.id).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.budgets import (
    compute_budget_consumption,
    evaluate_budget_alerts,
    tenant_budget_summary,
)
from api_server.db.budget_alert_state import BudgetScope
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE budget_alert_states, exchange_rates, executions, tasks, plans, "
            "projects, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _seed_tenant(
    dsn: str,
    *,
    slug: str,
    budget_amount: Decimal | None = None,
    budget_currency: str | None = None,
    budget_period: str | None = None,
) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations "
            "(id, name, slug, tenant_budget_amount, tenant_budget_currency, "
            " tenant_budget_period) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            tenant,
            slug.title(),
            slug,
            budget_amount,
            budget_currency,
            budget_period,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_project(
    dsn: str,
    *,
    tenant: UUID,
    name: str,
    budget_amount: Decimal | None = None,
    budget_currency: str | None = None,
    budget_period: str | None = None,
) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects "
            "(id, tenant_id, name, status, budget_amount, budget_currency, budget_period) "
            "VALUES ($1, $2, $3, 'active', $4, $5, $6)",
            project_id,
            tenant,
            name,
            budget_amount,
            budget_currency,
            budget_period,
        )
    finally:
        await conn.close()
    return project_id


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


async def _seed_execution(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    total_cost_usd: Decimal,
    created_at: datetime,
) -> UUID:
    execution_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions "
            "(id, tenant_id, task_id, agent_id, status, steps_log, total_tokens, "
            " total_cost_usd, started_at, completed_at, created_at) "
            "VALUES ($1, $2, $3, NULL, 'done', $4::jsonb, 0, $5, $6, $6, $6)",
            execution_id,
            tenant,
            task_id,
            json.dumps([]),
            total_cost_usd,
            created_at,
        )
    finally:
        await conn.close()
    return execution_id


async def _seed_rate(dsn: str, *, currency: str, rate: str, on: date) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO exchange_rates (id, currency, rate_vs_usd, as_of_date, source) "
            "VALUES ($1, $2, $3, $4, 'ecb')",
            uuid4(),
            currency,
            Decimal(rate),
            on,
        )
    finally:
        await conn.close()


async def _count_states(dsn: str, *, tenant: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM budget_alert_states WHERE tenant_id = $1", tenant
            )
        )
    finally:
        await conn.close()


async def _open_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


class _FakeDispatcher:
    """Stands in for the Plan 10 notifier — captures the dispatched events
    instead of enqueuing a real ``dispatch_event`` task (mocks the send)."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def dispatch(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return True


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# A fixed evaluation moment inside a monthly period; spend is seeded on the same
# day so it always falls in [period_start, period_end).
_NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)
_PERIOD_START = date(2026, 5, 1)


# ===========================================================================
# Crossing each threshold fires exactly one alert per crossed threshold
# ===========================================================================
@pytest.mark.asyncio
async def test_crossing_thresholds_fires_one_alert_each(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # A USD tenant budget of $100 / monthly. Spend $85 → crosses ONLY 80%.
    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="acme-bud",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("85"),
        created_at=_NOW,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()

        # Exactly ONE threshold (80) crossed → ONE alert.
        assert [f.threshold for f in result.fired] == [80]
        assert len(dispatcher.events) == 1
        event = dispatcher.events[0]
        assert event["event_type"] == "budget_alert"
        assert event["tenant_id"] == str(tenant)
        ctx = event["context"]
        assert ctx["threshold"] == 80
        assert ctx["scope"] == "tenant"
        assert ctx["percent_used"] == pytest.approx(85.0)
        assert ctx["budget_usd"] == pytest.approx(100.0)
    finally:
        await session.close()
        await engine.dispose()

    assert await _count_states(migrations_pg_dsn, tenant=tenant) == 1


@pytest.mark.asyncio
async def test_crossing_all_thresholds_fires_each_once(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # Spend $120 on a $100 budget → crosses 80, 90 AND 100 → three alerts.
    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="over-bud",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("120"),
        created_at=_NOW,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()
        assert sorted(f.threshold for f in result.fired) == [80, 90, 100]
        assert len(dispatcher.events) == 3
        assert {e["context"]["threshold"] for e in dispatcher.events} == {80, 90, 100}
    finally:
        await session.close()
        await engine.dispose()

    assert await _count_states(migrations_pg_dsn, tenant=tenant) == 3


# ===========================================================================
# Staying under every threshold fires nothing
# ===========================================================================
@pytest.mark.asyncio
async def test_under_threshold_no_alert(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # Spend $50 on a $100 budget → 50% < 80% → no alert.
    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="safe-bud",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("50"),
        created_at=_NOW,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()
        assert result.fired == []
        assert dispatcher.events == []
        # The consumption is still reported (50% used).
        assert len(result.consumptions) == 1
        assert result.consumptions[0].percent_used == Decimal("50.0")
    finally:
        await session.close()
        await engine.dispose()

    assert await _count_states(migrations_pg_dsn, tenant=tenant) == 0


# ===========================================================================
# Debounce within a period; a new period re-arms
# ===========================================================================
@pytest.mark.asyncio
async def test_debounce_within_period_and_new_period_rearm(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="debounce-bud",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    # $85 in May → crosses 80%.
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("85"),
        created_at=_NOW,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        r1 = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()
        assert [f.threshold for f in r1.fired] == [80]
        assert len(dispatcher.events) == 1

        # Re-evaluate later the SAME period — debounced (no re-fire).
        r2 = await evaluate_budget_alerts(
            session,
            tenant_id=tenant,
            dispatcher=dispatcher,
            now=_NOW + timedelta(days=5),
        )
        await session.commit()
        assert r2.fired == []
        assert [f.threshold for f in r2.suppressed] == [80]
        assert len(dispatcher.events) == 1  # still ONE — no spam

        # A NEW period (June): spend $85 in June re-arms the 80% alert.
        june_now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
        await _seed_execution(
            migrations_pg_dsn,
            tenant=tenant,
            task_id=task,
            total_cost_usd=Decimal("85"),
            created_at=june_now,
        )
        r3 = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=june_now
        )
        await session.commit()
        assert [f.threshold for f in r3.fired] == [80]
        assert len(dispatcher.events) == 2  # one for May, one for June
    finally:
        await session.close()
        await engine.dispose()

    # Two debounce rows: (May, 80) and (June, 80).
    assert await _count_states(migrations_pg_dsn, tenant=tenant) == 2


# ===========================================================================
# Non-USD budget cap is converted to USD with the period-start rate
# ===========================================================================
@pytest.mark.asyncio
async def test_non_usd_budget_converted_with_period_rate(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # EUR budget of 100 EUR. Rate 0.80 EUR/USD on the period start →
    # budget_usd = 100 / 0.80 = 125 USD. Spend $110 USD → 88% → crosses 80%.
    await _seed_rate(migrations_pg_dsn, currency="EUR", rate="0.80", on=_PERIOD_START)
    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="eur-bud",
        budget_amount=Decimal("100"),
        budget_currency="EUR",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("110"),
        created_at=_NOW,
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        consumptions = await compute_budget_consumption(
            session, tenant_id=tenant, on_date=_NOW.date()
        )
        assert len(consumptions) == 1
        c = consumptions[0]
        assert c.budget_usd == Decimal("125.000000")
        assert c.percent_used == Decimal("88.0")
        assert c.crossed_thresholds == (80,)
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# Per-project budget alerts independently of the tenant budget
# ===========================================================================
@pytest.mark.asyncio
async def test_project_budget_fires_independently(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # No tenant budget; ONE project with a $100 monthly budget, spend $95 → 95%
    # crosses 80 and 90 (not 100).
    tenant = await _seed_tenant(migrations_pg_dsn, slug="proj-bud")
    project = await _seed_project(
        migrations_pg_dsn,
        tenant=tenant,
        name="Costly",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("95"),
        created_at=_NOW,
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        result = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()
        assert sorted(f.threshold for f in result.fired) == [80, 90]
        # Both alerts are PROJECT-scoped, naming the project.
        assert all(f.scope is BudgetScope.PROJECT for f in result.fired)
        assert {e["context"]["scope"] for e in dispatcher.events} == {"project"}
        assert {e["context"]["project_name"] for e in dispatcher.events} == {"Costly"}
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# The assistant tool returns real budget data
# ===========================================================================
@pytest.mark.asyncio
async def test_assistant_summary_returns_real_data(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="summary-bud",
        budget_amount=Decimal("200"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="T")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("60"),
        created_at=_NOW,
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        summary = await tenant_budget_summary(session, tenant_id=tenant, on_date=_NOW.date())
    finally:
        await session.close()
        await engine.dispose()

    assert summary["available"] is True
    assert summary["currency"] == "USD"
    tenant_scope = summary["tenant"]
    assert tenant_scope is not None
    assert tenant_scope["scope"] == "tenant"
    assert Decimal(tenant_scope["spend_usd"]) == Decimal("60")
    assert Decimal(tenant_scope["budget_usd"]) == Decimal("200")
    assert tenant_scope["percent_used"] == pytest.approx(30.0)
    assert tenant_scope["status"] == "ok"
    assert tenant_scope["period"] == "monthly"
    assert tenant_scope["period_start"] == _PERIOD_START.isoformat()


@pytest.mark.asyncio
async def test_assistant_summary_no_budget_configured(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(migrations_pg_dsn, slug="nobudget-bud")

    engine, session = await _open_session(app_database_url, tenant)
    try:
        summary = await tenant_budget_summary(session, tenant_id=tenant)
    finally:
        await session.close()
        await engine.dispose()

    assert summary["available"] is False
    assert summary["reason"] == "no_budget_configured"


# ===========================================================================
# Tenant-scoped: evaluating tenant B sees NONE of tenant A's spend
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_budget_evaluation_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # Tenant A: $100 budget, spend $95 (would cross 80 & 90).
    tenant_a = await _seed_tenant(
        migrations_pg_dsn,
        slug="alpha-bud",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="A")
    task_a = await _seed_task(migrations_pg_dsn, tenant=tenant_a, project_id=project_a, title="TA")
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        total_cost_usd=Decimal("95"),
        created_at=_NOW,
    )

    # Tenant B: SAME $100 budget, but NO spend.
    tenant_b = await _seed_tenant(
        migrations_pg_dsn,
        slug="bravo-bud",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="B")
    task_b = await _seed_task(migrations_pg_dsn, tenant=tenant_b, project_id=project_b, title="TB")
    # B's spend is recorded but only $5 (5%) — and must NOT be polluted by A.
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_b,
        task_id=task_b,
        total_cost_usd=Decimal("5"),
        created_at=_NOW,
    )

    # Evaluating B: only B's $5 spend → 5% → no alert.
    dispatcher_b = _FakeDispatcher()
    engine_b, session_b = await _open_session(app_database_url, tenant_b)
    try:
        result_b = await evaluate_budget_alerts(
            session_b, tenant_id=tenant_b, dispatcher=dispatcher_b, now=_NOW
        )
        await session_b.commit()
        assert result_b.fired == []
        assert dispatcher_b.events == []
        assert result_b.consumptions[0].spend_usd == Decimal("5")
        assert result_b.consumptions[0].percent_used == Decimal("5.0")
    finally:
        await session_b.close()
        await engine_b.dispose()

    # Evaluating A: A's $95 → crosses 80 & 90, scoped to A.
    dispatcher_a = _FakeDispatcher()
    engine_a, session_a = await _open_session(app_database_url, tenant_a)
    try:
        result_a = await evaluate_budget_alerts(
            session_a, tenant_id=tenant_a, dispatcher=dispatcher_a, now=_NOW
        )
        await session_a.commit()
        assert sorted(f.threshold for f in result_a.fired) == [80, 90]
        assert all(e["tenant_id"] == str(tenant_a) for e in dispatcher_a.events)
    finally:
        await session_a.close()
        await engine_a.dispose()

    # B never fired; A fired twice.
    assert await _count_states(migrations_pg_dsn, tenant=tenant_b) == 0
    assert await _count_states(migrations_pg_dsn, tenant=tenant_a) == 2
