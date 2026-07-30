"""Integration tests: approval + safeguards on the live run (task_02_33).

The fifth task of Plan 02 Fase G wires two Fase C/F components into the
worker-conducted run:

  * the **approval engine** — a sensitive tool call is gated *before it
    runs*: the loop stops with `awaiting_human_approval`, the worker
    persists an `ApprovalRequest` and parks the `executions` row;
  * the **safeguards** — a breached budget aborts the run and the abort
    code lands on the `executions` row.

These drive the real pipeline — Docker + Postgres + Redis — through
`conduct_execution`, the same entry point the worker uses in production.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import ApprovalRequest, ExecutionStatus, Project, Task, TaskStatus
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.config import Settings
from workers.execution import ExecutionRequest, conduct_execution

import docker

from ._docker_helpers import docker_client, requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

_IMAGE = "agent-runtime:v1"
TEST_REDIS_URL = "redis://localhost:6379/15"


@pytest.fixture(scope="module", autouse=True)
def _agent_runtime_image() -> None:
    """Skip cleanly if agent-runtime:v1 has not been built on this host."""
    client = docker_client()
    try:
        client.images.get(_IMAGE)
    except docker.errors.ImageNotFound:  # pragma: no cover - env-dependent
        pytest.skip(f"{_IMAGE} not built — run: docker build -t {_IMAGE} ...")
    finally:
        client.close()


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(sm: async_sessionmaker, *, approval_policy: dict | None = None) -> dict[str, UUID]:
    """Insert a tenant / project (with an approval policy) / task."""
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Approval tenant", slug="approval-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Approval project",
                status="active",
                is_template=False,
                human_approval_policy=approval_policy,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Run a command",
                status="in_progress",
                priority="medium",
            )
        )
    return ids


def _request(ids: dict[str, UUID], *, model: dict, budgets: dict | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(ids["tenant"]),
        task_id=str(ids["task"]),
        agent_id=None,
        task={"id": str(ids["task"]), "title": "Run a command", "description": ""},
        model=model,
        budgets=budgets,
    )


# ---------------------------------------------------------------------------
# Approval — a sensitive action is gated before it runs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_sensitive_action_parks_the_execution_for_approval(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, approval_policy={"categories": {"code_changes": "human_required"}})

        # shell_exec maps to the canonical `code_changes` category — gated (g6).
        model = {
            "kind": "scripted",
            "decisions": [{"kind": "act", "tool": "shell_exec", "tool_args": {"cmd": "ls"}}],
        }
        outcome = await conduct_execution(
            _request(ids, model=model), settings=Settings(), sessionmaker=sm, redis=redis
        )

        assert outcome.status == ExecutionStatus.AWAITING_HUMAN_APPROVAL

        async with sm() as s:
            execution = (await list_executions_for_task(s, ids["task"]))[0]
            task = await s.get(Task, ids["task"])
            requests = (
                (
                    await s.execute(
                        select(ApprovalRequest).where(ApprovalRequest.execution_id == execution.id)
                    )
                )
                .scalars()
                .all()
            )
        assert execution.status == ExecutionStatus.AWAITING_HUMAN_APPROVAL
        assert execution.completed_at is None  # parked, not finished
        # ADR 0020 — la tarea también se aparca y el agente queda libre.
        assert task is not None
        assert task.status == TaskStatus.AWAITING_HUMAN_APPROVAL
        assert task.assigned_agent_id is None
        assert len(requests) == 1
        request = requests[0]
        assert request.status == "pending"
        assert request.category == "code_changes"
        assert request.action["tool"] == "shell_exec"
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_non_sensitive_tool_is_never_gated(
    _migrated: None, admin_database_url: str
) -> None:
    """A human_required policy gates only the categories it names — a
    plain `echo` is not a sensitive action and runs straight through."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, approval_policy={"categories": {"code_changes": "human_required"}})

        model = {
            "kind": "scripted",
            "decisions": [
                {"kind": "act", "tool": "echo", "tool_args": {"text": "hi"}},
                {"kind": "finish", "output": "done"},
            ],
        }
        outcome = await conduct_execution(
            _request(ids, model=model), settings=Settings(), sessionmaker=sm, redis=redis
        )

        assert outcome.status == ExecutionStatus.DONE
        async with sm() as s:
            execution = (await list_executions_for_task(s, ids["task"]))[0]
            requests = (await s.execute(select(ApprovalRequest))).scalars().all()
        assert execution.status == ExecutionStatus.DONE
        assert len(requests) == 0
    finally:
        await redis.aclose()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Safeguards — a breached budget aborts the live run
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_max_iterations_safeguard_aborts_the_live_run(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        model = {
            "kind": "scripted",
            "decisions": [
                {"kind": "act", "tool": "echo", "tool_args": {"text": "a"}},
                {"kind": "act", "tool": "echo", "tool_args": {"text": "b"}},
            ],
        }
        outcome = await conduct_execution(
            _request(ids, model=model, budgets={"max_iterations": 2}),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        assert outcome.status == ExecutionStatus.ABORTED
        assert outcome.abort_code == "max_iterations_exceeded"
        async with sm() as s:
            execution = (await list_executions_for_task(s, ids["task"]))[0]
        assert execution.status == ExecutionStatus.ABORTED
        assert execution.abort_code == "max_iterations_exceeded"
    finally:
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_max_cost_safeguard_aborts_the_live_run(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        # First decision spends 0.5 USD; the next safeguard check trips
        # against a 0.1 USD ceiling.
        model = {
            "kind": "scripted",
            "decisions": [
                {"kind": "act", "tool": "echo", "tool_args": {"text": "x"}, "cost_usd": 0.5},
                {"kind": "act", "tool": "noop", "tool_args": {}},
            ],
        }
        outcome = await conduct_execution(
            _request(ids, model=model, budgets={"max_cost_usd": 0.1}),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
        )

        assert outcome.status == ExecutionStatus.ABORTED
        assert outcome.abort_code == "max_cost_exceeded"
    finally:
        await redis.aclose()
        await engine.dispose()
