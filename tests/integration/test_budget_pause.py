"""Integration tests for budget auto-pause + manual override (Plan 11.1 task_11_1_06).

When a tenant or project reaches 100% of its budget for the active period the
consumption evaluator sets ``paused_by_budget`` and the orchestrator's
execution-START path refuses to enqueue NEW runs for that scope — while ANY
already-running execution keeps going (never killed). A manual override
(System Admin / Tenant Admin) clears the pause + writes an ``audit_log`` row; a
NEW budget period auto-clears the pause (the fresh window is under 100% again).
What this verifies:

  * at 100% the auto-pause flag is set and the orchestrator REFUSES to start a
    new execution (task stays ``ready``, nothing enqueued) while a pre-existing
    ``running`` execution is untouched;
  * the manual override clears the pause (a subsequent start succeeds) and
    writes a ``budget_pause_override`` audit row;
  * a NEW period auto-clears the pause (re-derived from the fresh window);
  * tenant/project scoped (@pytest.mark.cross_tenant): tenant A's pause never
    blocks tenant B.

USD is canonical; the cap is converted to USD with the period-start rate (no
real network — USD budgets need no rate). RLS is exercised by seeding as the
BYPASSRLS migrations user and running the pause refresh / override as the
NOBYPASSRLS app_user with ``app.tenant_id`` set; the orchestrator dispatcher
runs on the BYPASSRLS admin engine (like in production) and relies on the
guard's explicit tenant predicate.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

# Import the full domain ORM so SQLAlchemy resolves the FKs the spend query
# joins (Execution.task_id -> tasks.id, Task.project_id -> projects.id).
import api_server.db.domain  # noqa: F401
import asyncpg
import pytest
from alembic import command
from api_server.budgets.pause import (
    BUDGET_PAUSE_OVERRIDE_ACTION,
    budget_pause_block,
    clear_budget_pause,
    refresh_budget_pause_flags,
)
from api_server.db.budget_alert_state import BudgetScope
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

from ._partitions import ensure_partition_for
from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration

# A one-step scripted model so the dispatcher has a usable agent to pick.
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
}

# A fixed evaluation moment inside a monthly period; spend is seeded on the same
# day so it always falls in [period_start, period_end).
_NOW = datetime(2026, 5, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE budget_alert_states, exchange_rates, executions, task_dependencies, "
            "tasks, plans, agents, projects, organizations, audit_log RESTART IDENTITY CASCADE"
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
            "(id, tenant_id, name, status, worker_config, budget_amount, "
            " budget_currency, budget_period) "
            "VALUES ($1, $2, $3, 'active', '{\"assignment_policy\": \"load_balanced\"}'::jsonb, "
            "$4, $5, $6)",
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


async def _seed_user(dsn: str, *, email: str) -> UUID:
    """A real user row so an audit_log.user_id FK is satisfied (the override
    actor is always an authenticated user in production)."""
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_active) VALUES ($1, $2, 'x', true)",
            user_id,
            email,
        )
    finally:
        await conn.close()
    return user_id


async def _seed_agent(dsn: str, *, tenant: UUID, project_id: UUID) -> UUID:
    agent_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents "
            "(id, tenant_id, name, role, system_prompt, agent_type, scope, "
            " project_id, model_config) "
            "VALUES ($1, $2, 'Writer', 'backend-dev', 'You write things.', 'ai', "
            "'project_local', $3, $4::jsonb)",
            agent_id,
            tenant,
            project_id,
            json.dumps(_SCRIPTED_FINISH),
        )
    finally:
        await conn.close()
    return agent_id


async def _seed_task(
    dsn: str, *, tenant: UUID, project_id: UUID, title: str, status: str = "ready"
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, "
            " priority, retry_count) "
            "VALUES ($1, $2, $3, NULL, $4, $5, 'medium', 0)",
            task_id,
            tenant,
            project_id,
            title,
            status,
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
    status: str = "done",
) -> UUID:
    execution_id = uuid4()
    # Los llamantes siembran en `_NOW` (mayo de 2026, fijo) y en junio: `executions`
    # está particionada por mes y SIN DEFAULT (ADR 0151), y esos meses no existen en
    # una base recién migrada. Ver
    # docs/03-guides/gotchas/sembrar-filas-retrofechadas-en-tabla-particionada.md
    await ensure_partition_for(dsn, "executions", created_at)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions "
            "(id, tenant_id, task_id, agent_id, status, steps_log, total_tokens, "
            " total_cost_usd, started_at, completed_at, created_at) "
            "VALUES ($1, $2, $3, NULL, $4, $5::jsonb, 0, $6, $7, $8, $7)",
            execution_id,
            tenant,
            task_id,
            status,
            json.dumps([]),
            total_cost_usd,
            created_at,
            created_at if status == "done" else None,
        )
    finally:
        await conn.close()
    return execution_id


async def _execution_status(dsn: str, *, execution_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(await conn.fetchval("SELECT status FROM executions WHERE id = $1", execution_id))
    finally:
        await conn.close()


async def _task_status(dsn: str, *, task_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(await conn.fetchval("SELECT status FROM tasks WHERE id = $1", task_id))
    finally:
        await conn.close()


async def _org_paused(dsn: str, *, tenant: UUID) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval(
                "SELECT tenant_paused_by_budget FROM organizations WHERE id = $1", tenant
            )
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


async def _count_audit(dsn: str, *, tenant: UUID, action: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(
            await conn.fetchval(
                "SELECT count(*) FROM audit_log WHERE tenant_id = $1 AND action = $2",
                tenant,
                action,
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


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    celery_app = build_celery_app(
        WorkerSettings(broker_url=TEST_REDIS_URL, result_backend=TEST_REDIS_URL)
    )
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


def _ready_event(*, tenant: UUID, project: UUID, task: UUID) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(tenant),
        project_id=str(project),
        task_id=str(task),
        occurred_at="2026-05-15T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


# ===========================================================================
# At 100% the start is refused; an active execution is untouched
# ===========================================================================
@pytest.mark.asyncio
async def test_at_100pct_new_start_refused_active_untouched(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str, admin_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # A $100 monthly TENANT budget, spend $120 → 120% → at/over 100% → pause.
    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="pause-tenant",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    await _seed_agent(migrations_pg_dsn, tenant=tenant, project_id=project)
    spent_task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="spent", status="done"
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=spent_task,
        total_cost_usd=Decimal("120"),
        created_at=_NOW,
    )
    # A pre-existing ACTIVE (running) execution that must NEVER be touched.
    active_task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="active", status="in_progress"
    )
    active_exec = await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=active_task,
        total_cost_usd=Decimal("0"),
        created_at=_NOW,
        status="running",
    )
    # A NEW task waiting to start.
    new_task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="new", status="ready"
    )

    # 1) Refresh the pause flags on the tenant-scoped (RLS) session.
    engine, session = await _open_session(app_database_url, tenant)
    try:
        refresh = await refresh_budget_pause_flags(session, tenant_id=tenant, now=_NOW)
        await session.commit()
        assert [c.scope for c in refresh.newly_paused] == [BudgetScope.TENANT]
    finally:
        await session.close()
        await engine.dispose()

    assert await _org_paused(migrations_pg_dsn, tenant=tenant) is True

    # 2) The orchestrator (BYPASSRLS admin engine, like prod) refuses to START
    #    the new run: task stays ready, nothing enqueued.
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    admin_engine = create_async_engine(admin_database_url)
    try:
        await redis.delete("default")
        sm = async_sessionmaker(admin_engine, expire_on_commit=False)
        await _dispatcher(sm).handle(_ready_event(tenant=tenant, project=project, task=new_task))
        assert await _task_status(migrations_pg_dsn, task_id=new_task) == "ready"
        assert await redis.llen("default") == 0
    finally:
        await redis.delete("default")
        await redis.aclose()
        await admin_engine.dispose()

    # 3) The pre-existing active execution is untouched — still running.
    assert await _execution_status(migrations_pg_dsn, execution_id=active_exec) == "running"


# ===========================================================================
# Manual override clears the pause (a start then succeeds) + writes audit
# ===========================================================================
@pytest.mark.asyncio
async def test_override_resumes_and_audits(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str, admin_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="override-tenant",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    await _seed_agent(migrations_pg_dsn, tenant=tenant, project_id=project)
    spent_task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="spent", status="done"
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=spent_task,
        total_cost_usd=Decimal("120"),
        created_at=_NOW,
    )
    new_task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="new", status="ready"
    )
    actor = await _seed_user(migrations_pg_dsn, email="override-actor@example.com")

    engine, session = await _open_session(app_database_url, tenant)
    try:
        await refresh_budget_pause_flags(session, tenant_id=tenant, now=_NOW)
        await session.commit()
        assert await _org_paused(migrations_pg_dsn, tenant=tenant) is True

        # Manual override clears the tenant pause + writes the audit row.
        cleared = await clear_budget_pause(
            session,
            tenant_id=tenant,
            scope=BudgetScope.TENANT,
            project_id=None,
            actor_user_id=actor,
            is_system_admin=False,
            reason="approved extra spend for May",
        )
        await session.commit()
        assert cleared is True
    finally:
        await session.close()
        await engine.dispose()

    assert await _org_paused(migrations_pg_dsn, tenant=tenant) is False
    assert (
        await _count_audit(migrations_pg_dsn, tenant=tenant, action=BUDGET_PAUSE_OVERRIDE_ACTION)
        == 1
    )

    # After the override the orchestrator starts the new run again.
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    admin_engine = create_async_engine(admin_database_url)
    try:
        await redis.delete("default")
        sm = async_sessionmaker(admin_engine, expire_on_commit=False)
        await _dispatcher(sm).handle(_ready_event(tenant=tenant, project=project, task=new_task))
        assert await _task_status(migrations_pg_dsn, task_id=new_task) == "in_progress"
        assert await redis.llen("default") == 1
    finally:
        await redis.delete("default")
        await redis.aclose()
        await admin_engine.dispose()


# ===========================================================================
# A NEW budget period auto-clears the pause
# ===========================================================================
@pytest.mark.asyncio
async def test_new_period_auto_clears_pause(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    tenant = await _seed_tenant(
        migrations_pg_dsn,
        slug="period-tenant",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
    task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="T", status="done"
    )
    # $120 in MAY → over budget in May.
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("120"),
        created_at=_NOW,
    )

    engine, session = await _open_session(app_database_url, tenant)
    try:
        await refresh_budget_pause_flags(session, tenant_id=tenant, now=_NOW)
        await session.commit()
        assert await _org_paused(migrations_pg_dsn, tenant=tenant) is True

        # A NEW period (June): no June spend → 0% → auto-clears on the next
        # refresh (the flag is re-derived from the fresh window).
        june_now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
        refresh = await refresh_budget_pause_flags(session, tenant_id=tenant, now=june_now)
        await session.commit()
        assert [c.scope for c in refresh.newly_cleared] == [BudgetScope.TENANT]

        # And the START guard now allows (no block) for the June period.
        block = await budget_pause_block(session, tenant_id=tenant, project_id=project)
        assert block is None
    finally:
        await session.close()
        await engine.dispose()

    assert await _org_paused(migrations_pg_dsn, tenant=tenant) is False


# ===========================================================================
# Project-scoped pause blocks that project's runs; the override clears it
# ===========================================================================
@pytest.mark.asyncio
async def test_project_scope_pause_and_override(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # No tenant budget; ONE project at 100%+.
    tenant = await _seed_tenant(migrations_pg_dsn, slug="proj-pause")
    project = await _seed_project(
        migrations_pg_dsn,
        tenant=tenant,
        name="Costly",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    task = await _seed_task(
        migrations_pg_dsn, tenant=tenant, project_id=project, title="T", status="done"
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant,
        task_id=task,
        total_cost_usd=Decimal("105"),
        created_at=_NOW,
    )
    actor = await _seed_user(migrations_pg_dsn, email="proj-actor@example.com")

    engine, session = await _open_session(app_database_url, tenant)
    try:
        refresh = await refresh_budget_pause_flags(session, tenant_id=tenant, now=_NOW)
        await session.commit()
        assert [c.scope for c in refresh.newly_paused] == [BudgetScope.PROJECT]
        assert await _project_paused(migrations_pg_dsn, project_id=project) is True

        # The START guard blocks a run in THIS project (PROJECT scope), but the
        # tenant itself is not paused.
        block = await budget_pause_block(session, tenant_id=tenant, project_id=project)
        assert block is not None
        assert block.scope is BudgetScope.PROJECT
        assert block.project_id == project

        # Override the project pause.
        cleared = await clear_budget_pause(
            session,
            tenant_id=tenant,
            scope=BudgetScope.PROJECT,
            project_id=project,
            actor_user_id=actor,
            is_system_admin=True,
        )
        await session.commit()
        assert cleared is True

        block_after = await budget_pause_block(session, tenant_id=tenant, project_id=project)
        assert block_after is None
    finally:
        await session.close()
        await engine.dispose()

    assert await _project_paused(migrations_pg_dsn, project_id=project) is False
    assert (
        await _count_audit(migrations_pg_dsn, tenant=tenant, action=BUDGET_PAUSE_OVERRIDE_ACTION)
        == 1
    )


# ===========================================================================
# Tenant-scoped: A's pause never blocks B (@cross_tenant)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_pause_is_tenant_scoped(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str, admin_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    await _truncate_all(migrations_pg_dsn)

    # Tenant A: over budget → paused.
    tenant_a = await _seed_tenant(
        migrations_pg_dsn,
        slug="alpha-pause",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="A")
    task_a = await _seed_task(
        migrations_pg_dsn, tenant=tenant_a, project_id=project_a, title="TA", status="done"
    )
    await _seed_execution(
        migrations_pg_dsn,
        tenant=tenant_a,
        task_id=task_a,
        total_cost_usd=Decimal("150"),
        created_at=_NOW,
    )

    # Tenant B: SAME budget but no spend → never paused.
    tenant_b = await _seed_tenant(
        migrations_pg_dsn,
        slug="bravo-pause",
        budget_amount=Decimal("100"),
        budget_currency="USD",
        budget_period="monthly",
    )
    project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="B")
    await _seed_agent(migrations_pg_dsn, tenant=tenant_b, project_id=project_b)
    new_task_b = await _seed_task(
        migrations_pg_dsn, tenant=tenant_b, project_id=project_b, title="TB", status="ready"
    )

    # Refresh each tenant on its OWN RLS session.
    for t in (tenant_a, tenant_b):
        engine, session = await _open_session(app_database_url, t)
        try:
            await refresh_budget_pause_flags(session, tenant_id=t, now=_NOW)
            await session.commit()
        finally:
            await session.close()
            await engine.dispose()

    assert await _org_paused(migrations_pg_dsn, tenant=tenant_a) is True
    assert await _org_paused(migrations_pg_dsn, tenant=tenant_b) is False

    # The guard: A is blocked, B is allowed.
    engine_a, session_a = await _open_session(app_database_url, tenant_a)
    try:
        block_a = await budget_pause_block(session_a, tenant_id=tenant_a, project_id=project_a)
        assert block_a is not None and block_a.scope is BudgetScope.TENANT
    finally:
        await session_a.close()
        await engine_a.dispose()

    # B's task starts fine despite A being paused — the orchestrator (admin
    # engine) sees B's flag, not A's.
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    admin_engine = create_async_engine(admin_database_url)
    try:
        await redis.delete("default")
        sm = async_sessionmaker(admin_engine, expire_on_commit=False)
        await _dispatcher(sm).handle(
            _ready_event(tenant=tenant_b, project=project_b, task=new_task_b)
        )
        assert await _task_status(migrations_pg_dsn, task_id=new_task_b) == "in_progress"
        assert await redis.llen("default") == 1
    finally:
        await redis.delete("default")
        await redis.aclose()
        await admin_engine.dispose()
