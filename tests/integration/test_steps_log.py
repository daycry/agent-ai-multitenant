"""Integration tests for execution persistence — steps_log JSONB (task_02_11).

The `executions` table (migration 0010) stores one row per agent loop
run, with the step-by-step `steps_log` as JSONB. These tests drive real
Postgres: the JSONB round-trips intact, `execution_repo` persists an
`agent_runtime` result, and the tenant-isolation RLS policy holds.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from agent_runtime.graph import ExecutionResult
from alembic import command
from api_server.db.domain import Execution, Project, Task
from api_server.db.execution_repo import get_execution, record_execution
from api_server.db.models import Organization
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    """Bring the throwaway DB to head so migration 0010 is applied."""
    command.upgrade(alembic_config, "head")


def _steps_sample() -> list[dict[str, object]]:
    """A small but representative steps_log — one of each kind."""
    return [
        {"index": 0, "kind": "node", "node": "perceive", "status": "ok", "summary": "perceived"},
        {
            "index": 1,
            "kind": "model_call",
            "node": "plan",
            "model": "scripted",
            "tokens_in": 100,
            "tokens_out": 30,
            "total_tokens": 130,
            "cost_usd": 0.002,
            "status": "ok",
            "summary": "decided",
        },
        {
            "index": 2,
            "kind": "tool_call",
            "node": "act",
            "tool": "echo",
            "args": {"text": "hola"},
            "result": {"ok": True, "output": "hola"},
            "status": "ok",
            "summary": "ran echo",
        },
    ]


async def _seed_task(session: async_sessionmaker, tenant: UUID, slug: str) -> dict[str, UUID]:
    """Insert an organization → project → task chain; return their ids."""
    ids = {"tenant": tenant, "project": uuid4(), "task": uuid4()}
    async with session() as s, s.begin():
        s.add(Organization(id=tenant, name=f"Tenant {slug}", slug=slug))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=tenant,
                name="Steps project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=tenant,
                project_id=ids["project"],
                title="A task",
                status="backlog",
                priority="medium",
            )
        )
    return ids


async def _truncate(session: async_sessionmaker) -> None:
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )


# ---------------------------------------------------------------------------
# steps_log JSONB round-trip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_executions_table_round_trips_steps_log(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await _truncate(sm)
        ids = await _seed_task(sm, uuid4(), "steps-roundtrip")

        steps = _steps_sample()
        execution_id = uuid4()
        async with sm() as s, s.begin():
            s.add(
                Execution(
                    id=execution_id,
                    tenant_id=ids["tenant"],
                    task_id=ids["task"],
                    status="done",
                    output="the result",
                    steps_log=steps,
                    iterations=2,
                )
            )

        # Fresh session — a genuine read back from Postgres, not the
        # identity map.
        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        assert loaded.steps_log == steps
        assert loaded.steps_log[1]["kind"] == "model_call"
        assert loaded.steps_log[2]["args"] == {"text": "hola"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_steps_log_defaults_to_an_empty_list(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await _truncate(sm)
        ids = await _seed_task(sm, uuid4(), "steps-default")

        execution_id = uuid4()
        async with sm() as s, s.begin():
            s.add(Execution(id=execution_id, tenant_id=ids["tenant"], task_id=ids["task"]))

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        assert loaded.steps_log == []
        assert loaded.status == "running"
        assert loaded.iterations == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# execution_repo.record_execution
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_execution_persists_an_agent_result(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await _truncate(sm)
        ids = await _seed_task(sm, uuid4(), "steps-record")

        result = ExecutionResult(
            status="done",
            abort_code=None,
            output="a poem about the sea",
            iterations=2,
            steps=_steps_sample(),
            usage={
                "total_tokens": 130,
                "cost_usd": 0.002,
                "tool_calls": 1,
                "model_calls": 1,
            },
        )
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        assert loaded.status == "done"
        assert loaded.output == "a poem about the sea"
        assert loaded.iterations == 2
        assert loaded.total_tokens == 130
        assert float(loaded.total_cost_usd) == pytest.approx(0.002)
        assert loaded.tool_call_count == 1
        assert loaded.model_call_count == 1
        assert loaded.completed_at is not None
        assert len(loaded.steps_log) == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_record_execution_keeps_abort_code_for_aborted_runs(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await _truncate(sm)
        ids = await _seed_task(sm, uuid4(), "steps-abort")

        result = ExecutionResult(
            status="aborted",
            abort_code="max_iterations_exceeded",
            output="Execution aborted (max_iterations_exceeded).",
            iterations=25,
            steps=[],
            usage={"total_tokens": 0, "cost_usd": 0.0, "tool_calls": 0, "model_calls": 0},
        )
        async with sm() as s, s.begin():
            execution = await record_execution(
                s, tenant_id=ids["tenant"], task_id=ids["task"], result=result
            )
            execution_id = execution.id

        async with sm() as s:
            loaded = await get_execution(s, execution_id)
        assert loaded is not None
        assert loaded.status == "aborted"
        assert loaded.abort_code == "max_iterations_exceeded"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# RLS — tenant isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_executions_are_tenant_isolated(
    _migrated: None, admin_database_url: str, app_database_url: str
) -> None:
    tenant_a, tenant_b = uuid4(), uuid4()

    admin_engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(admin_engine, expire_on_commit=False)
        await _truncate(sm)
        ids_a = await _seed_task(sm, tenant_a, "steps-rls-a")
        ids_b = await _seed_task(sm, tenant_b, "steps-rls-b")
        async with sm() as s, s.begin():
            s.add(Execution(id=uuid4(), tenant_id=tenant_a, task_id=ids_a["task"]))
            s.add(Execution(id=uuid4(), tenant_id=tenant_b, task_id=ids_b["task"]))
    finally:
        await admin_engine.dispose()

    # As app_user (NOBYPASSRLS) the executions_tenant_isolation policy
    # filters rows by the app.tenant_id GUC.
    app_engine = create_async_engine(app_database_url)
    try:
        sm = async_sessionmaker(app_engine, expire_on_commit=False)
        async with sm() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_a)},
            )
            rows = (await s.execute(select(Execution))).scalars().all()
        assert [r.tenant_id for r in rows] == [tenant_a]

        async with sm() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_b)},
            )
            rows = (await s.execute(select(Execution))).scalars().all()
        assert [r.tenant_id for r in rows] == [tenant_b]
    finally:
        await app_engine.dispose()


# ---------------------------------------------------------------------------
# Migration reversibility
# ---------------------------------------------------------------------------
def test_migration_0010_is_reversible(alembic_config: object) -> None:
    """downgrade to 0009 then back up to head must both succeed.

    Sync on purpose — alembic's async env runs its own asyncio loop.
    """
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0009_fn_compute_task_ready")
    command.upgrade(alembic_config, "head")
