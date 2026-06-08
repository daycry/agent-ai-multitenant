"""Integration tests: per-agent tool enforcement (Plan 06.15 task_06_15_02).

task_06_15_01 added the write surface (``PUT /agents/{id}/tools``) that fills
the ``agent_tools`` junction. This task makes those rows *bite*: an agent with
assignments has its resolved toolset restricted to exactly those tools, and a
tool outside the set is rejected by the runtime's ``ToolRegistry`` at call
time. An agent with NO rows keeps the current unrestricted behaviour (no
regression).

The full path under test:

  ``agent_tools`` rows
    → ``resolve_agent_tool_names`` (orchestrator, in ``_route_ai``)
    → ``combine_tool_allowlists`` (intersect with the chat-mode allowlist;
       the task-dispatch path carries none, so the per-agent set stands alone)
    → ``request["allowed_tools"]`` in the worker run payload
    → ``ExecutionRequest`` → ``_agent_spec`` → ``AGENT_TASK_SPEC``
    → ``ToolRegistry.set_allowed_tools`` (runtime) → reject at ``call`` time.

Pure-function tests cover the combination semantics; DB tests cover the
resolver (incl. the ``None`` "no rows" sentinel and cross-tenant isolation);
an orchestrator-dispatch test proves the allowlist is computed and threaded
into the worker payload (and omitted when there are no rows); a runtime test
proves a non-assigned tool is actually rejected end to end.
"""

from __future__ import annotations

import base64
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from agent_runtime.__main__ import run_task
from alembic import command
from api_server.agent_tools_enforcement import (
    combine_tool_allowlists,
    resolve_agent_tool_names,
)
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

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"


# A scripted model whose single ACT picks one tool, then finishes — enough to
# exercise the runtime allowlist at call time (mirrors the chat-modes suite).
def _scripted_model(act_tool: str) -> dict[str, Any]:
    return {
        "kind": "scripted",
        "decisions": [
            {"kind": "act", "tool": act_tool, "tool_args": {"text": "go"}},
            {"kind": "finish", "output": "done"},
        ],
        "reviews": [{"passed": True}],
    }


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


# ===========================================================================
# Pure: combine_tool_allowlists (no DB) — the intersection contract
# ===========================================================================
def test_combine_both_none_is_unrestricted() -> None:
    # No per-agent rows AND no mode allowlist → None (current behaviour).
    assert combine_tool_allowlists(None, None) is None


def test_combine_only_agent_set_yields_that_set() -> None:
    assert combine_tool_allowlists({"read_file", "write_file"}, None) == [
        "read_file",
        "write_file",
    ]


def test_combine_only_mode_set_yields_that_set() -> None:
    assert combine_tool_allowlists(None, ["echo", "noop"]) == ["echo", "noop"]


def test_combine_both_set_intersects() -> None:
    # A tool must satisfy BOTH layers.
    assert combine_tool_allowlists({"read_file", "write_file"}, ["read_file", "echo"]) == [
        "read_file"
    ]


def test_combine_disjoint_layers_block_everything() -> None:
    # Empty list (NOT None) — the runtime reads this as "block every tool".
    assert combine_tool_allowlists({"write_file"}, ["read_file"]) == []


def test_combine_resolves_aliases_so_catalog_and_mode_names_intersect() -> None:
    # ADR 0048: the agent is assigned the CATALOG name (read_file) and the mode
    # allows the legacy chat-mode name (file_read) — the same logical action.
    # Before canonicalisation this intersected to [] (the silent "unknown tool"
    # bug). Both must resolve to the canonical read_file so the tool survives.
    assert combine_tool_allowlists({"read_file"}, ["file_read"]) == ["read_file"]
    # http_request (chat-mode) expands to both verbs; an agent allowed http_get
    # keeps it (the mode does not strip it away by name mismatch).
    assert combine_tool_allowlists({"http_get"}, ["http_request"]) == ["http_get"]
    # A genuine disjoint pair (different actions) still blocks — canonicalising
    # must not collapse distinct tools together.
    assert combine_tool_allowlists({"write_file"}, ["file_read"]) == []


# ===========================================================================
# DB: resolve_agent_tool_names — the None "no rows" sentinel + isolation
# ===========================================================================
async def _seed_tenant_agent(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    slug: str,
    project_id: UUID | None = None,
) -> UUID:
    """Insert an organization + a project_local agent; return the agent id."""
    session.add(Organization(id=tenant_id, name=f"Org {slug}", slug=f"org-{slug}"))
    await session.flush()
    project_id = project_id or uuid4()
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
            model_config=_scripted_model("read_file"),
        )
    )
    await session.flush()
    return agent_id


async def _seed_tool(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    name: str,
    is_builtin: bool = True,
    deleted: bool = False,
) -> UUID:
    tool_id = uuid4()
    tool = Tool(
        id=tool_id,
        tenant_id=tenant_id,
        name=name,
        category="file",
        implementation_type="builtin",
        security_level="safe",
        is_builtin=is_builtin,
    )
    session.add(tool)
    await session.flush()
    if deleted:
        await session.execute(
            text("UPDATE tools SET deleted_at = now() WHERE id = :id"), {"id": tool_id}
        )
    return tool_id


@pytest.mark.asyncio
async def test_resolve_returns_none_when_no_rows(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE agent_tools, tasks, agents, tools, projects,"
                    " organizations RESTART IDENTITY CASCADE"
                )
            )
            agent_id = await _seed_tenant_agent(s, tenant_id=uuid4(), slug="norows")
        async with sm() as s:
            assert await resolve_agent_tool_names(s, agent_id) is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_resolve_returns_assigned_names(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE agent_tools, tasks, agents, tools, projects,"
                    " organizations RESTART IDENTITY CASCADE"
                )
            )
            tenant = uuid4()
            agent_id = await _seed_tenant_agent(s, tenant_id=tenant, slug="withrows")
            t_read = await _seed_tool(s, tenant_id=tenant, name="read_file")
            t_write = await _seed_tool(s, tenant_id=tenant, name="write_file")
            t_gone = await _seed_tool(s, tenant_id=tenant, name="ghost_tool", deleted=True)
            for tid in (t_read, t_write, t_gone):
                s.add(AgentTool(agent_id=agent_id, tool_id=tid))
        async with sm() as s:
            names = await resolve_agent_tool_names(s, agent_id)
        # Soft-deleted tool contributes no name; the two live ones do.
        assert names == frozenset({"read_file", "write_file"})

    finally:
        await engine.dispose()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_resolve_is_tenant_isolated_under_rls(
    _migrated: None, admin_database_url: str, app_database_url: str
) -> None:
    """Tenant A's agent has assignments; under tenant B's RLS-scoped session
    those rows are invisible. A's allowlist must never leak into B's resolve.

    Seeding is done BYPASSRLS (admin engine); the resolve runs through a
    NOBYPASSRLS app_user session with ``app.tenant_id`` bound to B — exactly
    how the production read is scoped via the agents/tools tables."""
    admin_engine = create_async_engine(admin_database_url)
    app_engine = create_async_engine(app_database_url)
    try:
        admin_sm = async_sessionmaker(admin_engine, expire_on_commit=False)
        tenant_a = uuid4()
        tenant_b = uuid4()
        async with admin_sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE agent_tools, tasks, agents, tools, projects,"
                    " organizations RESTART IDENTITY CASCADE"
                )
            )
            agent_a = await _seed_tenant_agent(s, tenant_id=tenant_a, slug="a")
            agent_b = await _seed_tenant_agent(s, tenant_id=tenant_b, slug="b")
            tool_a = await _seed_tool(s, tenant_id=tenant_a, name="secret_a", is_builtin=False)
            s.add(AgentTool(agent_id=agent_a, tool_id=tool_a))

        app_sm = async_sessionmaker(app_engine, expire_on_commit=False)
        # B's RLS session cannot see A's agent's assignments at all.
        async with app_sm() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(tenant_b)},
            )
            leaked = await resolve_agent_tool_names(s, agent_a)
            own = await resolve_agent_tool_names(s, agent_b)
        # A's agent is invisible to B → resolves to None (no leak of secret_a).
        assert leaked is None
        # B's own agent has no rows → None.
        assert own is None
    finally:
        await app_engine.dispose()
        await admin_engine.dispose()


# ===========================================================================
# Orchestrator dispatch: the allowlist is computed + threaded (or omitted)
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
    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


async def _seed_dispatchable(
    sm: async_sessionmaker[AsyncSession], *, assign_tools: list[str] | None
) -> dict[str, UUID]:
    """Seed tenant/project/agent/task ready to dispatch; optionally wire
    `assign_tools` (tool names) to the agent via agent_tools."""
    ids = {"tenant": uuid4(), "project": uuid4(), "agent": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE agent_tools, executions, task_dependencies, tasks, agents,"
                " tools, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        await _seed_tenant_agent_with_ids(s, ids)
        if assign_tools is not None:
            for name in assign_tools:
                tool_id = await _seed_tool(s, tenant_id=ids["tenant"], name=name)
                s.add(AgentTool(agent_id=ids["agent"], tool_id=tool_id))
    return ids


async def _seed_tenant_agent_with_ids(s: AsyncSession, ids: dict[str, UUID]) -> None:
    s.add(Organization(id=ids["tenant"], name="Enf tenant", slug="enf-tenant"))
    await s.flush()
    s.add(
        Project(
            id=ids["project"],
            tenant_id=ids["tenant"],
            name="Enf project",
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
            name="Enforcer",
            role="backend-dev",
            system_prompt="x",
            agent_type="ai",
            scope="project_local",
            project_id=ids["project"],
            model_config=_scripted_model("read_file"),
        )
    )
    await s.flush()
    s.add(
        Task(
            id=ids["task"],
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            title="Enforce tools",
            description="exercise per-agent enforcement",
            status="ready",
            priority="medium",
        )
    )


async def _drain_request(redis: Redis, queue: str) -> dict[str, Any]:
    """Pop the single enqueued Celery message and return its `request` kwarg."""
    raw = await redis.lrange(queue, 0, -1)
    await redis.delete(queue)
    assert len(raw) == 1
    message = json.loads(raw[0])
    body = json.loads(base64.b64decode(message["body"]))
    _args, kwargs, _embed = body
    return kwargs["request"]  # type: ignore[no-any-return]


@pytest.mark.asyncio
async def test_dispatch_threads_allowlist_when_agent_has_assignments(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_dispatchable(sm, assign_tools=["read_file", "list_files"])
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))

        request = await _drain_request(redis, "default")
        assert request["agent_id"] == str(ids["agent"])
        # Restricted to exactly the assigned names (sorted, deterministic).
        assert request["allowed_tools"] == ["list_files", "read_file"]
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dispatch_omits_allowlist_when_agent_has_no_assignments(
    _migrated: None, admin_database_url: str
) -> None:
    """No agent_tools rows → no `allowed_tools` key → current unrestricted
    behaviour (no regression for existing agents)."""
    engine = create_async_engine(admin_database_url)
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed_dispatchable(sm, assign_tools=None)
        await redis.delete("default")

        await _dispatcher(sm).handle(_ready_event(ids["tenant"], ids["project"], ids["task"]))

        request = await _drain_request(redis, "default")
        assert request["agent_id"] == str(ids["agent"])
        assert "allowed_tools" not in request
    finally:
        await redis.delete("default")
        await redis.aclose()
        await engine.dispose()


# ===========================================================================
# Runtime: the resolved allowlist is enforced at call time
# ===========================================================================
def _run_and_collect(spec: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> list[dict]:
    rc = run_task(spec)
    assert rc == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _act_steps(events: list[dict]) -> list[dict]:
    return [
        e["step"] for e in events if e.get("event") == "step" and e["step"].get("node") == "act"
    ]


def test_runtime_rejects_non_assigned_tool(capsys: pytest.CaptureFixture[str]) -> None:
    """An agent assigned only 'noop' (its agent_tools-derived allowlist) must
    have 'echo' rejected by the ToolRegistry — proving the threaded allowlist
    bites at call time."""
    allowlist = combine_tool_allowlists({"noop"}, None)
    assert allowlist == ["noop"]
    spec = {
        "task": {"id": "t-1", "title": "enforce", "description": ""},
        "model": _scripted_model("echo"),
        "allowed_tools": allowlist,
    }
    acts = _act_steps(_run_and_collect(spec, capsys))
    assert len(acts) == 1
    assert acts[0]["result"]["ok"] is False
    assert acts[0]["result"]["error"] == "tool 'echo' not allowed in this mode"


def test_runtime_runs_assigned_tool(capsys: pytest.CaptureFixture[str]) -> None:
    allowlist = combine_tool_allowlists({"noop"}, None)
    spec = {
        "task": {"id": "t-1", "title": "enforce", "description": ""},
        "model": _scripted_model("noop"),
        "allowed_tools": allowlist,
    }
    acts = _act_steps(_run_and_collect(spec, capsys))
    assert acts[0]["result"]["ok"] is True


def test_runtime_no_assignments_is_unrestricted(capsys: pytest.CaptureFixture[str]) -> None:
    """No assignments → combine yields None → no `allowed_tools` key → every
    registered tool callable (the pre-06.15 behaviour)."""
    allowlist = combine_tool_allowlists(None, None)
    assert allowlist is None
    spec: dict[str, Any] = {
        "task": {"id": "t-1", "title": "enforce", "description": ""},
        "model": _scripted_model("echo"),
    }
    # combine returned None → mirror _agent_spec: omit the key entirely.
    acts = _act_steps(_run_and_collect(spec, capsys))
    assert acts[0]["result"]["ok"] is True
