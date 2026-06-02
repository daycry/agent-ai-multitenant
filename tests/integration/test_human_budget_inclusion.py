"""Integration tests for budget_includes_human_cost (Plan 16 task_16_12).

A project's human cost (``hourly_rate * hours`` from ``human_work_sessions``) is
ALWAYS imputed + segmented in the dashboard. Whether it ALSO counts toward the
project's BUDGET — consumption, threshold alerts, auto-pause — is the per-project
``projects.budget_includes_human_cost`` flag (migration 0074):

  * ``false`` (default): only the canonical-USD AI cost
    (``executions.total_cost_usd``) counts (the Plan 11.1 behaviour, unchanged);
  * ``true``: the project's human cost is FOLDED into the consumption the
    thresholds / auto-pause compare against the cap.

What this verifies against the REAL Postgres (dev stack on PG 15432):

  * with the flag TRUE, a project under-budget on AI alone but over-budget once
    human cost is added trips the 100% pause + fires the threshold alerts (the
    folded human spend is segmented in ``human_spend_usd``);
  * with the flag FALSE the SAME spend stays under threshold — no pause, no
    alert (only AI counts);
  * a tenant-wide budget with an opted-in project folds that project's human
    cost into the tenant consumption too;
  * cross-tenant (@pytest.mark.cross_tenant): tenant A's human spend never folds
    into / pauses tenant B's budget.

Mirrors the test_budget_alerts / test_budget_pause fixture pattern (BYPASSRLS
seed + NOBYPASSRLS app_user evaluation with ``app.tenant_id`` set; a fake
dispatcher captures alerts without a live broker).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy resolves the FKs the spend + human
# cost queries join (Execution.task_id -> tasks.id, Task.assigned_agent_id ->
# agents.id, HumanAgentConfig.agent_id -> agents.id, ...).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.budgets import (
    compute_budget_consumption,
    evaluate_budget_alerts,
    refresh_budget_pause_flags,
)
from api_server.db.budget_alert_state import BudgetScope
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

# A fixed evaluation moment inside a monthly period; spend + sessions are seeded
# on the same day so they always fall in [period_start, period_end).
_NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE budget_alert_states, human_work_sessions, human_agent_config,"
            " exchange_rates, executions, tasks, plans, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
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
            "INSERT INTO organizations (id, name, slug, tenant_budget_amount,"
            " tenant_budget_currency, tenant_budget_period) VALUES ($1, $2, $3, $4, $5, $6)",
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
    includes_human_cost: bool = False,
    budget_amount: Decimal | None = None,
    budget_currency: str | None = None,
    budget_period: str | None = None,
) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, budget_includes_human_cost,"
            " budget_amount, budget_currency, budget_period)"
            " VALUES ($1, $2, $3, 'active', $4, $5, $6, $7)",
            project_id,
            tenant,
            name,
            includes_human_cost,
            budget_amount,
            budget_currency,
            budget_period,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_user(dsn: str, *, email: str) -> UUID:
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')",
            user_id,
            email,
        )
    finally:
        await conn.close()
    return user_id


async def _seed_human_agent(
    dsn: str, *, tenant: UUID, user_id: UUID, hourly_rate: Decimal, currency: str = "USD"
) -> UUID:
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt,"
            " model_config, scope, is_template, project_id)"
            " VALUES ($1, $2, 'HA', 'human', 'reviewer', 'h', '{}'::jsonb,"
            " 'global_tenant_template', true, NULL)",
            agent_id,
            tenant,
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
            currency,
        )
    finally:
        await conn.close()
    return agent_id


async def _seed_task(
    dsn: str,
    *,
    tenant: UUID,
    project_id: UUID,
    title: str,
    status: str = "done",
    assigned_agent_id: UUID | None = None,
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority,"
            " retry_count, assigned_agent_id) VALUES ($1, $2, $3, NULL, $4, $5, 'medium', 0, $6)",
            task_id,
            tenant,
            project_id,
            title,
            status,
            assigned_agent_id,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_execution(
    dsn: str, *, tenant: UUID, task_id: UUID, total_cost_usd: Decimal, created_at: datetime = _NOW
) -> UUID:
    execution_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, agent_id, status, steps_log,"
            " total_tokens, total_cost_usd, started_at, completed_at, created_at)"
            " VALUES ($1, $2, $3, NULL, 'done', '[]'::jsonb, 0, $4, $5, $5, $5)",
            execution_id,
            tenant,
            task_id,
            total_cost_usd,
            created_at,
        )
    finally:
        await conn.close()
    return execution_id


async def _seed_work_session(
    dsn: str,
    *,
    tenant: UUID,
    task_id: UUID,
    user_id: UUID,
    hours_logged: Decimal,
    start_at: datetime = _NOW,
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO human_work_sessions (id, tenant_id, task_id, user_id, start_at, end_at,"
            " hours_logged, output_files_attached)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, '[]'::jsonb)",
            uuid4(),
            tenant,
            task_id,
            user_id,
            start_at,
            start_at + timedelta(hours=1),
            hours_logged,
        )
    finally:
        await conn.close()


async def _project_paused(dsn: str, *, project_id: UUID) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval("SELECT paused_by_budget FROM projects WHERE id = $1", project_id)
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


class _FakeDispatcher:
    """Captures dispatched budget alerts instead of enqueuing a real task."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def dispatch(self, event: dict[str, object]) -> bool:
        self.events.append(event)
        return True


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _build_under_ai_over_human_project(
    dsn: str, *, slug: str, includes_human_cost: bool
) -> tuple[UUID, UUID]:
    """A $100/monthly PROJECT budget with $50 AI spend + $80 human cost.

    AI alone = 50% (under every threshold). AI + human = $130 = 130% (over 100).
    Returns (tenant_id, project_id)."""
    tenant = await _seed_tenant(dsn, slug=slug)
    project = await _seed_project(
        dsn,
        tenant=tenant,
        name="Costly",
        includes_human_cost=includes_human_cost,
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    user = await _seed_user(dsn, email=f"{slug}-worker@example.com")
    # $50 AI spend.
    ai_task = await _seed_task(dsn, tenant=tenant, project_id=project, title="AI")
    await _seed_execution(dsn, tenant=tenant, task_id=ai_task, total_cost_usd=Decimal("50"))
    # $80 human cost: 100 USD/h agent, 0.8h.
    agent = await _seed_human_agent(dsn, tenant=tenant, user_id=user, hourly_rate=Decimal("100"))
    h_task = await _seed_task(
        dsn, tenant=tenant, project_id=project, title="Legal", assigned_agent_id=agent
    )
    await _seed_work_session(
        dsn, tenant=tenant, task_id=h_task, user_id=user, hours_logged=Decimal("0.8")
    )
    return tenant, project


# ===========================================================================
# Flag TRUE: human cost folds in -> over 100% -> pause + alerts
# ===========================================================================
@pytest.mark.asyncio
async def test_included_human_cost_trips_pause_and_alerts(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant, project = await _build_under_ai_over_human_project(
        migrations_pg_dsn, slug="inc-on", includes_human_cost=True
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        # Consumption: spend folds AI ($50) + human ($80) = $130 (130%).
        consumptions = await compute_budget_consumption(
            session, tenant_id=tenant, on_date=_NOW.date()
        )
        proj = next(c for c in consumptions if c.scope is BudgetScope.PROJECT)
        assert proj.ai_spend_usd == Decimal("50.000000")
        assert proj.human_spend_usd == Decimal("80.000000")
        assert proj.spend_usd == Decimal("130.000000")
        assert proj.percent_used == Decimal("130.0")
        assert proj.is_over_budget is True

        # Alerts: 80 / 90 / 100 all crossed -> three alerts fire.
        result = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()
        assert {e["context"]["threshold"] for e in dispatcher.events} == {80, 90, 100}
        assert {f.threshold for f in result.fired} == {80, 90, 100}

        # Auto-pause: at/over 100% the project pauses.
        refresh = await refresh_budget_pause_flags(session, tenant_id=tenant, now=_NOW)
        await session.commit()
        assert [c.scope for c in refresh.newly_paused] == [BudgetScope.PROJECT]
    finally:
        await session.close()
        await engine.dispose()

    assert await _project_paused(migrations_pg_dsn, project_id=project) is True


# ===========================================================================
# Flag FALSE: human cost excluded -> AI alone (50%) -> no pause, no alert
# ===========================================================================
@pytest.mark.asyncio
async def test_excluded_human_cost_stays_under_threshold(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant, project = await _build_under_ai_over_human_project(
        migrations_pg_dsn, slug="inc-off", includes_human_cost=False
    )

    dispatcher = _FakeDispatcher()
    engine, session = await _open_session(app_database_url, tenant)
    try:
        consumptions = await compute_budget_consumption(
            session, tenant_id=tenant, on_date=_NOW.date()
        )
        proj = next(c for c in consumptions if c.scope is BudgetScope.PROJECT)
        # AI-only: human is NOT folded -> spend == AI == $50 (50%).
        assert proj.ai_spend_usd == Decimal("50.000000")
        assert proj.human_spend_usd == Decimal("0")
        assert proj.spend_usd == Decimal("50.000000")
        assert proj.percent_used == Decimal("50.0")
        assert proj.is_over_budget is False

        result = await evaluate_budget_alerts(
            session, tenant_id=tenant, dispatcher=dispatcher, now=_NOW
        )
        await session.commit()
        assert dispatcher.events == []
        assert result.fired == []

        refresh = await refresh_budget_pause_flags(session, tenant_id=tenant, now=_NOW)
        await session.commit()
        assert refresh.newly_paused == []
    finally:
        await session.close()
        await engine.dispose()

    assert await _project_paused(migrations_pg_dsn, project_id=project) is False


# ===========================================================================
# Tenant-wide budget folds an opted-in project's human cost
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_budget_folds_opted_in_project_human_cost(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # Tenant budget $100/monthly; ONE project opted in with $50 AI + $80 human.
    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="tenant-fold",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(
        migrations_pg_dsn, tenant=tenant, name="Opted", includes_human_cost=True
    )
    user = await _seed_user(migrations_pg_dsn, email="tf-worker@example.com")
    ai_task = await _seed_task(migrations_pg_dsn, tenant=tenant, project_id=project, title="AI")
    await _seed_execution(
        migrations_pg_dsn, tenant=tenant, task_id=ai_task, total_cost_usd=Decimal("50")
    )
    agent = await _seed_human_agent(
        migrations_pg_dsn, tenant=tenant, user_id=user, hourly_rate=Decimal("100")
    )
    h_task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="Legal", assigned_agent_id=agent
    )
    await _seed_work_session(
        migrations_pg_dsn, tenant=tenant, task_id=h_task, user_id=user, hours_logged=Decimal("0.8")
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        consumptions = await compute_budget_consumption(
            session, tenant_id=tenant, on_date=_NOW.date()
        )
        tenant_scope = next(c for c in consumptions if c.scope is BudgetScope.TENANT)
        # The tenant budget folds the opted-in project's $80 human cost: 50+80=130.
        assert tenant_scope.ai_spend_usd == Decimal("50.000000")
        assert tenant_scope.human_spend_usd == Decimal("80.000000")
        assert tenant_scope.spend_usd == Decimal("130.000000")
        assert tenant_scope.is_over_budget is True
    finally:
        await session.close()
        await engine.dispose()


# ===========================================================================
# Cross-tenant: A's human spend never folds into / pauses B
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_human_cost_inclusion_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # Tenant A: opted-in project, AI $50 + human $80 -> over budget.
    tenant_a, project_a = await _build_under_ai_over_human_project(
        migrations_pg_dsn, slug="cs-alpha", includes_human_cost=True
    )

    # Tenant B: SAME-shaped budget + an opted-in project but NO spend / sessions.
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="cs-bravo")
    project_b = await _seed_project(
        migrations_pg_dsn,
        tenant=tenant_b,
        name="Empty",
        includes_human_cost=True,
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )

    # Refresh + evaluate each tenant on its OWN RLS session.
    for t in (tenant_a, tenant_b):
        engine, session = await _open_session(app_database_url, t)
        try:
            await refresh_budget_pause_flags(session, tenant_id=t, now=_NOW)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    # A's project is paused (its own human cost tipped it over); B's is not
    # (A's $80 never folds into B).
    assert await _project_paused(migrations_pg_dsn, project_id=project_a) is True
    assert await _project_paused(migrations_pg_dsn, project_id=project_b) is False

    # B's consumption sees ZERO human spend folded.
    engine_b, session_b = await _open_session(app_database_url, tenant_b)
    try:
        consumptions = await compute_budget_consumption(
            session_b, tenant_id=tenant_b, on_date=_NOW.date()
        )
        proj_b = next(c for c in consumptions if c.scope is BudgetScope.PROJECT)
        assert proj_b.human_spend_usd == Decimal("0")
        assert proj_b.spend_usd == Decimal("0.000000")
    finally:
        await session_b.close()
        await engine_b.dispose()
