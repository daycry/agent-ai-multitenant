"""Integration tests for Human Agents in the PM planning chat (task_16_13).

In the planning sub-graph (``chat/planning_graph`` + ``chat/planning_context``)
the PM agent SEES the tenant's Human Agents (the gallery) and can assign a plan
task to one exactly like to an AI agent. The plan ESTIMATE then sizes each such
human task from the agent's configured figures (Plan 16 task_16_02 / task_16_13):

  * duration = ``expected_response_time_hours + expected_execution_time_hours``
  * cost     = ``hourly_rate * expected_execution_time_hours``

reusing the pure ``chat/cost.py`` layer
(:func:`~api_server.chat.cost.compute_human_agent_plan_estimate`).

What this verifies against the REAL Postgres (dev stack on PG 15432):

  * ``build_planning_context`` surfaces the tenant's own Human Agents (NOT the
    global templates) with their rate + expected times + a live workload count
    + an overload flag, on an RLS-scoped (app_user) session as in production;
  * the estimate integrates a human-agent-assigned task's expected times
    (duration) and rate-based cost, while a task with no ``human_agent_id`` is
    left to the existing AI / generic-human paths (behaviour unchanged);
  * cross-tenant (@pytest.mark.cross_tenant): tenant A's planning context never
    shows tenant B's Human Agents, and vice versa — RLS-enforced.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import api_server.db.domain  # noqa: F401  (resolve FKs the context joins traverse)
import asyncpg
import pytest
from alembic import command
from api_server.chat.cost import compute_human_agent_plan_estimate
from api_server.chat.planning_context import (
    HUMAN_AGENT_OVERLOAD_THRESHOLD,
    build_planning_context,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE messages, conversations, human_task_assignments, human_agent_config,"
            " tasks, plans, agents, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
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


async def _seed_project(dsn: str, *, tenant: UUID, name: str, team_id: UUID | None = None) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, team_id)"
            " VALUES ($1, $2, $3, 'active', $4)",
            project_id,
            tenant,
            name,
            team_id,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_human_agent(
    dsn: str,
    *,
    tenant: UUID,
    name: str,
    assigned_user_id: UUID | None,
    hourly_rate: Decimal | None,
    rate_currency: str | None,
    response_hours: int | None,
    execution_hours: int | None,
    scope: str = "global_tenant_template",
) -> UUID:
    """A human Agent + its human_agent_config (rate / expected times optional)."""
    agent_id = uuid4()
    project_id = None
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, agent_type, role, system_prompt,"
            " model_config, scope, is_template, project_id)"
            " VALUES ($1, $2, $3, 'human', 'reviewer', 'h', '{}'::jsonb, $4, true, $5)",
            agent_id,
            tenant,
            name,
            scope,
            project_id,
        )
        # global_builtin templates carry NO config (assignment is tenant-intrinsic);
        # tenant-owned ones do. Mirror GET /human-agents.
        if scope != "global_builtin":
            await conn.execute(
                "INSERT INTO human_agent_config (id, tenant_id, agent_id, assignment_mode,"
                " assigned_user_id, hourly_rate, hourly_rate_currency, acceptance_timeout_hours,"
                " notification_channels, expected_response_time_hours,"
                " expected_execution_time_hours)"
                " VALUES ($1, $2, $3, 'specific_user', $4, $5, $6, 24, '[]'::jsonb, $7, $8)",
                uuid4(),
                tenant,
                agent_id,
                assigned_user_id,
                hourly_rate,
                rate_currency,
                response_hours,
                execution_hours,
            )
    finally:
        await conn.close()
    return agent_id


async def _seed_assignment(
    dsn: str,
    *,
    tenant: UUID,
    project_id: UUID,
    human_agent_id: UUID,
    assigned_to_user_id: UUID | None,
    status: str,
) -> None:
    """A human task + one HumanTaskAssignment in the given status (workload)."""
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, assigned_agent_id)"
            " VALUES ($1, $2, $3, 'work', 'assigned_to_human', $4)",
            task_id,
            tenant,
            project_id,
            human_agent_id,
        )
        await conn.execute(
            "INSERT INTO human_task_assignments (id, tenant_id, task_id, human_agent_id,"
            " assigned_to_user_id, status) VALUES ($1, $2, $3, $4, $5, $6)",
            uuid4(),
            tenant,
            task_id,
            human_agent_id,
            assigned_to_user_id,
            status,
        )
    finally:
        await conn.close()


async def _seed_conversation(dsn: str, *, tenant: UUID, project_id: UUID) -> UUID:
    conv_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO conversations (id, tenant_id, project_id, title, current_mode)"
            " VALUES ($1, $2, $3, 'planning', 'planning')",
            conv_id,
            tenant,
            project_id,
        )
        await conn.execute(
            "INSERT INTO messages (id, tenant_id, conversation_id, author_kind, content, mode,"
            " attachments) VALUES ($1, $2, $3, 'system', 'hola', 'planning', '[]'::jsonb)",
            uuid4(),
            tenant,
            conv_id,
        )
    finally:
        await conn.close()
    return conv_id


async def _open_session(app_database_url: str, tenant_id: UUID):
    """An RLS-scoped (app_user) session with app.tenant_id set — as in prod."""
    engine = create_async_engine(app_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session = factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def schema_ready(alembic_config, app_database_url: str) -> None:
    command.upgrade(alembic_config, "head")
    # Retro-grant DML to app_user on the freshly-created tables (the default
    # privileges only apply to tables created AFTER they are set).
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())


# ===========================================================================
# Tests
# ===========================================================================
def test_pm_sees_tenant_human_agents_with_estimate_inputs(
    schema_ready, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """The PM sees the tenant's Human Agents (gallery) — rate + expected times +
    workload — but NOT the global templates (those need forking first)."""
    asyncio.run(_truncate_all(migrations_pg_dsn))

    async def _run() -> object:
        tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
        user = await _seed_user(migrations_pg_dsn, tenant=tenant, email="rev@acme.test")
        project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P")
        # A tenant-owned Human Agent — assignable, shows in the gallery.
        agent = await _seed_human_agent(
            migrations_pg_dsn,
            tenant=tenant,
            name="Legal Reviewer",
            assigned_user_id=user,
            hourly_rate=Decimal("80.00"),
            rate_currency="EUR",
            response_hours=2,
            execution_hours=6,
        )
        # A GLOBAL template — must NOT appear (no config, needs forking).
        await _seed_human_agent(
            migrations_pg_dsn,
            tenant=tenant,
            name="Template DBA",
            assigned_user_id=None,
            hourly_rate=None,
            rate_currency=None,
            response_hours=None,
            execution_hours=None,
            scope="global_builtin",
        )
        # One OPEN assignment -> workload count of 1 (below the overload bar).
        await _seed_assignment(
            migrations_pg_dsn,
            tenant=tenant,
            project_id=project,
            human_agent_id=agent,
            assigned_to_user_id=user,
            status="accepted",
        )
        conv = await _seed_conversation(migrations_pg_dsn, tenant=tenant, project_id=project)

        engine, session = await _open_session(app_database_url, tenant)
        try:
            ctx = await build_planning_context(session, conv)
        finally:
            await session.close()
            await engine.dispose()
        return (ctx, str(agent), str(user))

    ctx, agent_id, user_id = asyncio.run(_run())  # type: ignore[misc]

    # Only the tenant-owned agent — the global template is filtered out.
    assert len(ctx.human_agents) == 1
    opt = ctx.human_agents[0]
    assert opt.agent_id == agent_id
    assert opt.name == "Legal Reviewer"
    assert opt.assigned_user_id == user_id
    assert opt.hourly_rate == Decimal("80.00")
    assert opt.currency == "EUR"
    assert opt.expected_response_time_hours == 2
    assert opt.expected_execution_time_hours == 6
    assert opt.active_assignment_count == 1
    assert opt.overloaded is False

    # The graph payload carries the gallery so the PM prompt can reference it.
    payload = ctx.as_graph_payload()
    assert payload["human_agents"][0]["agent_id"] == agent_id
    assert payload["human_agents"][0]["hourly_rate"] == "80.00"


def test_plan_estimate_integrates_human_task_times_and_cost(
    schema_ready, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Assign a plan task to a Human Agent: the estimate integrates the agent's
    expected times (duration) and rate-based cost; AI tasks are left alone."""
    asyncio.run(_truncate_all(migrations_pg_dsn))

    async def _run() -> object:
        tenant = await _seed_tenant(migrations_pg_dsn, slug="beta")
        user = await _seed_user(migrations_pg_dsn, tenant=tenant, email="rev@beta.test")
        project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P2")
        agent = await _seed_human_agent(
            migrations_pg_dsn,
            tenant=tenant,
            name="Security Reviewer Senior",
            assigned_user_id=user,
            hourly_rate=Decimal("90.00"),
            rate_currency="EUR",
            response_hours=3,
            execution_hours=5,
        )
        conv = await _seed_conversation(migrations_pg_dsn, tenant=tenant, project_id=project)

        engine, session = await _open_session(app_database_url, tenant)
        try:
            ctx = await build_planning_context(session, conv)
        finally:
            await session.close()
            await engine.dispose()
        return (ctx, str(agent))

    ctx, agent_id = asyncio.run(_run())  # type: ignore[misc]

    # The PM assigns ONE task to the Human Agent (carries human_agent_id) and
    # leaves another as a plain AI task (no human_agent_id).
    spec = {
        "tasks": [
            {"id": "t-human", "title": "Audit de seguridad", "human_agent_id": agent_id},
            {"id": "t-ai", "title": "Implementar handler", "complexity": "m"},
        ]
    }

    estimate = compute_human_agent_plan_estimate(spec, ctx.human_agent_estimate_inputs())

    # Exactly one human-agent-assigned task is estimated; the AI task is skipped.
    assert estimate.task_count == 1
    row = estimate.tasks[0]
    assert row.task_id == "t-human"
    assert row.human_agent_id == agent_id
    assert row.response_hours == Decimal("3.000")
    assert row.execution_hours == Decimal("5.000")
    # duration = response (3) + execution (5) = 8h.
    assert row.duration_hours == Decimal("8.000")
    # cost = rate (90) * execution (5) = 450.00 (response time is wait, unpaid).
    assert row.cost == Decimal("450.00")
    assert row.currency == "EUR"
    # Totals fold the single human task.
    assert estimate.total_duration_hours == Decimal("8.000")
    assert estimate.total_cost == Decimal("450.00")
    assert estimate.currency == "EUR"


def test_overload_flag_for_busy_critical_human_agent(
    schema_ready, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """A Human Agent at/over the open-assignment threshold is flagged overloaded
    so the planning chat can warn before stacking another critical task on it."""
    asyncio.run(_truncate_all(migrations_pg_dsn))

    async def _run() -> object:
        tenant = await _seed_tenant(migrations_pg_dsn, slug="busy")
        user = await _seed_user(migrations_pg_dsn, tenant=tenant, email="busy@busy.test")
        project = await _seed_project(migrations_pg_dsn, tenant=tenant, name="P3")
        agent = await _seed_human_agent(
            migrations_pg_dsn,
            tenant=tenant,
            name="Brand Lead",
            assigned_user_id=user,
            hourly_rate=Decimal("70.00"),
            rate_currency="EUR",
            response_hours=1,
            execution_hours=4,
        )
        # THRESHOLD open assignments (mix of pending_acceptance + accepted) ->
        # overloaded. One reassigned (closed) row must NOT count.
        for _ in range(HUMAN_AGENT_OVERLOAD_THRESHOLD - 1):
            await _seed_assignment(
                migrations_pg_dsn,
                tenant=tenant,
                project_id=project,
                human_agent_id=agent,
                assigned_to_user_id=user,
                status="pending_acceptance",
            )
        await _seed_assignment(
            migrations_pg_dsn,
            tenant=tenant,
            project_id=project,
            human_agent_id=agent,
            assigned_to_user_id=user,
            status="accepted",
        )
        await _seed_assignment(
            migrations_pg_dsn,
            tenant=tenant,
            project_id=project,
            human_agent_id=agent,
            assigned_to_user_id=user,
            status="reassigned",  # closed — must not count
        )
        conv = await _seed_conversation(migrations_pg_dsn, tenant=tenant, project_id=project)

        engine, session = await _open_session(app_database_url, tenant)
        try:
            ctx = await build_planning_context(session, conv)
        finally:
            await session.close()
            await engine.dispose()
        return ctx

    ctx = asyncio.run(_run())  # type: ignore[misc]
    assert len(ctx.human_agents) == 1
    opt = ctx.human_agents[0]
    assert opt.active_assignment_count == HUMAN_AGENT_OVERLOAD_THRESHOLD
    assert opt.overloaded is True


@pytest.mark.cross_tenant
def test_human_agents_in_planning_are_tenant_scoped(
    schema_ready, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Tenant A's planning context never shows tenant B's Human Agents (RLS)."""
    asyncio.run(_truncate_all(migrations_pg_dsn))

    async def _run() -> object:
        tenant_a = await _seed_tenant(migrations_pg_dsn, slug="t-a")
        tenant_b = await _seed_tenant(migrations_pg_dsn, slug="t-b")
        user_a = await _seed_user(migrations_pg_dsn, tenant=tenant_a, email="a@a.test")
        user_b = await _seed_user(migrations_pg_dsn, tenant=tenant_b, email="b@b.test")
        project_a = await _seed_project(migrations_pg_dsn, tenant=tenant_a, name="PA")
        project_b = await _seed_project(migrations_pg_dsn, tenant=tenant_b, name="PB")
        agent_a = await _seed_human_agent(
            migrations_pg_dsn,
            tenant=tenant_a,
            name="A Reviewer",
            assigned_user_id=user_a,
            hourly_rate=Decimal("50.00"),
            rate_currency="EUR",
            response_hours=1,
            execution_hours=2,
        )
        agent_b = await _seed_human_agent(
            migrations_pg_dsn,
            tenant=tenant_b,
            name="B Reviewer",
            assigned_user_id=user_b,
            hourly_rate=Decimal("60.00"),
            rate_currency="EUR",
            response_hours=1,
            execution_hours=3,
        )
        conv_a = await _seed_conversation(migrations_pg_dsn, tenant=tenant_a, project_id=project_a)
        conv_b = await _seed_conversation(migrations_pg_dsn, tenant=tenant_b, project_id=project_b)

        engine_a, session_a = await _open_session(app_database_url, tenant_a)
        try:
            ctx_a = await build_planning_context(session_a, conv_a)
        finally:
            await session_a.close()
            await engine_a.dispose()

        engine_b, session_b = await _open_session(app_database_url, tenant_b)
        try:
            ctx_b = await build_planning_context(session_b, conv_b)
        finally:
            await session_b.close()
            await engine_b.dispose()
        return (ctx_a, ctx_b, str(agent_a), str(agent_b))

    ctx_a, ctx_b, agent_a, agent_b = asyncio.run(_run())  # type: ignore[misc]

    a_ids = {h.agent_id for h in ctx_a.human_agents}
    b_ids = {h.agent_id for h in ctx_b.human_agents}
    assert a_ids == {agent_a}
    assert b_ids == {agent_b}
    # Neither tenant's agent leaks into the other's gallery.
    assert agent_b not in a_ids
    assert agent_a not in b_ids
