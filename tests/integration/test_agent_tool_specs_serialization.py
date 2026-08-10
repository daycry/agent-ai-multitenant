"""Integration tests: the agent's assigned tools are serialised as executable
ToolSpecs and threaded into the worker run (Plan 06.18 task_06_18_05).

task_06_15_02 threaded the tool *names* (the allowlist). This task threads the
executable ToolSpec — the implementation_type + config the runtime needs to
REGISTER each tool — so the boot wires real executors instead of falling into
"unknown tool". The path under test:

  ``agent_tools`` rows
    → ``serialize_agent_tool_specs`` (orchestrator, in ``_route_ai``)
    → ``request["tool_specs"]`` in the worker run payload
    → ``ExecutionRequest`` → ``_agent_spec`` (worker resolves docker_command
       images) → ``AGENT_TASK_SPEC`` → ``__main__.run_task`` wiring.

DB tests cover the serialiser (incl. the ``None`` "no rows" sentinel,
shell_exec exclusion, docker_command runtime_template, and cross-tenant
isolation); an orchestrator-dispatch test proves the specs are threaded into
the worker payload (and omitted when there are no rows).
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.agent_tools_enforcement import serialize_agent_tool_specs
from api_server.db.domain import Agent, AgentTool, Project, Task, Tool
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py

pytestmark = pytest.mark.integration

_TRUNCATE = (
    "TRUNCATE agent_tools, executions, task_dependencies, tasks, agents,"
    " tools, projects, organizations RESTART IDENTITY CASCADE"
)


def _scripted(act_tool: str) -> dict[str, Any]:
    return {
        "kind": "scripted",
        "decisions": [
            {"kind": "act", "tool": act_tool, "tool_args": {}},
            {"kind": "finish", "output": "done"},
        ],
        "reviews": [{"passed": True}],
    }


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_agent_with_tools(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    slug: str,
    tools: list[dict[str, Any]],
) -> UUID:
    session.add(Organization(id=tenant_id, name=f"Org {slug}", slug=f"org-{slug}"))
    await session.flush()
    project_id = uuid4()
    session.add(
        Project(
            id=project_id,
            tenant_id=tenant_id,
            name=f"Project {slug}",
            status="active",
            is_template=False,
            worker_config={"assignment_policy": "load_balanced"},
        )
    )
    await session.flush()
    agent_id = uuid4()
    session.add(
        Agent(
            id=agent_id,
            tenant_id=tenant_id,
            name=f"Agent {slug}",
            role="backend-dev",
            system_prompt="x",
            agent_type="ai",
            scope="project_local",
            project_id=project_id,
            model_config=_scripted("read_file"),
        )
    )
    await session.flush()
    for spec in tools:
        tool_id = uuid4()
        session.add(
            Tool(
                id=tool_id,
                tenant_id=tenant_id,
                name=spec["name"],
                category=spec.get("category", "file"),
                implementation_type=spec["implementation_type"],
                implementation_ref=spec.get("implementation_ref"),
                security_level=spec.get("security_level", "safe"),
                is_builtin=spec.get("is_builtin", True),
            )
        )
        await session.flush()
        session.add(AgentTool(agent_id=agent_id, tool_id=tool_id))
    return agent_id


# ===========================================================================
# serialize_agent_tool_specs — shape + the None sentinel + shell_exec excl.
# ===========================================================================
@pytest.mark.asyncio
async def test_serialize_returns_none_when_no_rows(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(text(_TRUNCATE))
            agent_id = await _seed_agent_with_tools(s, tenant_id=uuid4(), slug="norows", tools=[])
        async with sm() as s:
            assert await serialize_agent_tool_specs(s, agent_id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_serialize_projects_implementation_type_and_config(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(text(_TRUNCATE))
            agent_id = await _seed_agent_with_tools(
                s,
                tenant_id=uuid4(),
                slug="specs",
                tools=[
                    {"name": "read_file", "implementation_type": "builtin"},
                    {
                        "name": "run_pytest",
                        "implementation_type": "docker_command",
                        "implementation_ref": "python-pytest",
                        "category": "runtime",
                    },
                    # shell_exec is wired per project, NOT serialised here.
                    {
                        "name": "shell_exec",
                        "implementation_type": "builtin",
                        "category": "command",
                        "security_level": "privileged",
                    },
                ],
            )
        async with sm() as s:
            specs = await serialize_agent_tool_specs(s, agent_id)
        assert specs is not None
        by_name = {spec["name"]: spec for spec in specs}
        # shell_exec excluded (project-wired).
        assert "shell_exec" not in by_name
        assert by_name["read_file"]["implementation_type"] == "builtin"
        # docker_command carries runtime_template (worker resolves the image).
        run = by_name["run_pytest"]
        assert run["implementation_type"] == "docker_command"
        assert run["config"]["runtime_template"] == "python-pytest"
        assert run["config"]["command_template"][0] == "pytest"
    finally:
        await engine.dispose()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_serialize_is_tenant_isolated_under_rls(
    _migrated: None, admin_database_url: str, app_database_url: str
) -> None:
    """Tenant A's agent has tool assignments; under tenant B's RLS session they
    are invisible — A's specs must never leak into B's serialise."""
    admin_engine = create_async_engine(admin_database_url)
    app_engine = create_async_engine(app_database_url)
    try:
        admin_sm = async_sessionmaker(admin_engine, expire_on_commit=False)
        tenant_a = uuid4()
        tenant_b = uuid4()
        async with admin_sm() as s, s.begin():
            await s.execute(text(_TRUNCATE))
            agent_a = await _seed_agent_with_tools(
                s,
                tenant_id=tenant_a,
                slug="a",
                tools=[{"name": "secret_a", "implementation_type": "builtin", "is_builtin": False}],
            )
            agent_b = await _seed_agent_with_tools(s, tenant_id=tenant_b, slug="b", tools=[])

        app_sm = async_sessionmaker(app_engine, expire_on_commit=False)
        async with app_sm() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_b)},
            )
            leaked = await serialize_agent_tool_specs(s, agent_a)
            own = await serialize_agent_tool_specs(s, agent_b)
        # A's agent is invisible to B → None (no leak of secret_a).
        assert leaked is None
        assert own is None
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()


# ===========================================================================
# Orchestrator dispatch: tool_specs threaded into the worker payload.
# ===========================================================================
def _ready_event(tenant: UUID, project: UUID, task: UUID) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(tenant),
        project_id=str(project),
        task_id=str(task),
        occurred_at="2026-06-01T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


def _dispatcher(sm: async_sessionmaker[AsyncSession]) -> TaskDispatcher:
    celery_app = build_celery_app(
        WorkerSettings(broker_url=TEST_REDIS_URL, result_backend=TEST_REDIS_URL)
    )
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


async def _seed_dispatchable(
    sm: async_sessionmaker[AsyncSession], *, tools: list[dict[str, Any]] | None
) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(text(_TRUNCATE))
        s.add(Organization(id=ids["tenant"], name="Spec tenant", slug="spec-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Spec project",
                status="active",
                is_template=False,
                worker_config={"assignment_policy": "load_balanced"},
            )
        )
        await s.flush()
        s.add(
            Agent(
                id=ids["agent"],
                tenant_id=ids["tenant"],
                name="Speccer",
                role="backend-dev",
                system_prompt="x",
                agent_type="ai",
                scope="project_local",
                project_id=ids["project"],
                model_config=_scripted("read_file"),
            )
        )
        await s.flush()
        if tools is not None:
            for spec in tools:
                tool_id = uuid4()
                s.add(
                    Tool(
                        id=tool_id,
                        tenant_id=ids["tenant"],
                        name=spec["name"],
                        category=spec.get("category", "file"),
                        implementation_type=spec["implementation_type"],
                        implementation_ref=spec.get("implementation_ref"),
                        security_level=spec.get("security_level", "safe"),
                        is_builtin=True,
                    )
                )
                await s.flush()
                s.add(AgentTool(agent_id=ids["agent"], tool_id=tool_id))
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Spec task",
                description="exercise tool_specs threading",
                status="ready",
                priority="medium",
            )
        )
    return ids


async def _drain_request(redis: Redis, queue: str) -> dict[str, Any]:
    raw = await redis.lrange(queue, 0, -1)
    await redis.delete(queue)
    assert len(raw) == 1
    message = json.loads(raw[0])
    body = json.loads(base64.b64decode(message["body"]))
    _args, kwargs, _embed = body
    return kwargs["request"]  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_dispatch_threads_tool_specs_when_agent_has_assignments(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_dispatchable(
            sm,
            tools=[
                {"name": "read_file", "implementation_type": "builtin"},
                {
                    "name": "run_pytest",
                    "implementation_type": "docker_command",
                    "implementation_ref": "python-pytest",
                    "category": "runtime",
                },
            ],
        )
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))

        request = await _drain_request(redis, "default")
        assert request["agent_id"] == str(ids["agent"])
        by_name = {spec["name"]: spec for spec in request["tool_specs"]}
        assert by_name["read_file"]["implementation_type"] == "builtin"
        assert by_name["run_pytest"]["implementation_type"] == "docker_command"
        assert by_name["run_pytest"]["config"]["runtime_template"] == "python-pytest"
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_omits_tool_specs_when_agent_has_no_assignments(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_dispatchable(sm, tools=None)
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))

        request = await _drain_request(redis, "default")
        assert request["agent_id"] == str(ids["agent"])
        assert "tool_specs" not in request
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()
