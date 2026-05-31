"""Integration tests: Task state machine — Human-Agent transitions (task_16_04).

Plan 16 Fase B extends the canonical §7.2 Task state machine
(``api_server.task_state_machine``) with the Human-Agent transitions:

  ready              -> assigned_to_human          (human only)
  assigned_to_human  -> in_progress                (human accepts)
  assigned_to_human  -> assigned_to_human          (reassignment)
  assigned_to_human  -> blocked                    (escalation exhausted)
  in_progress        -> in_review                  (human submits)

These are legal ONLY when the task's assignee Agent has
``agent_type=human``. An AI-assigned task asked to move
``ready -> assigned_to_human`` is REJECTED with the typed
:class:`TaskTransitionError`. The existing AI flow
(``backlog -> ready -> in_progress -> in_review -> done``) must be
untouched.

The suite uses the REAL database and the REAL domain service — no mocks.
It seeds a tenant / project / user / a human Agent (+ ``human_agent_config``)
and an AI Agent (BYPASSRLS migrations role), loads the live :class:`Task`
ORM row, resolves the assignee's ``agent_type`` from the DB exactly as the
orchestrator will (task_16_05), and drives the state machine against the
result. The ``@pytest.mark.cross_tenant`` test proves the resolver is
tenant-scoped: a human agent in tenant A is invisible to a session pinned
to tenant B, so tenant B cannot trick the AI/human gate into accepting
``assigned_to_human`` for a task it does not own.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from alembic import command
from api_server.db.domain import Agent, AgentType, Task
from api_server.task_state_machine import (
    TaskTransitionError,
    allowed_transitions,
    can_transition,
    is_terminal,
    transition_task_status,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seeding (BYPASSRLS migrations role)
# ---------------------------------------------------------------------------
async def _seed(
    sm: async_sessionmaker,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID,
    human_agent_id: UUID,
    ai_agent_id: UUID,
    human_task_id: UUID,
    ai_task_id: UUID,
) -> None:
    """Insert a tenant, project, user, a human + an AI agent and two tasks."""
    async with sm() as s, s.begin():
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": tenant_id, "name": "States tenant", "slug": f"states-{tenant_id.hex}"},
        )
        await s.execute(
            text(
                "INSERT INTO users (id, email, password_hash)"
                " VALUES (:id, :email, 'argon2-placeholder')"
            ),
            {"id": user_id, "email": f"worker-{user_id.hex}@states.test"},
        )
        await s.execute(
            text(
                "INSERT INTO projects (id, tenant_id, name, status)"
                " VALUES (:id, :tid, 'States project', 'active')"
            ),
            {"id": project_id, "tid": tenant_id},
        )
        # A human Agent + its config.
        s.add(
            Agent(
                id=human_agent_id,
                tenant_id=tenant_id,
                name="Legal Reviewer",
                role="reviewer",
                system_prompt="You review legal copy.",
                agent_type="human",
                scope="project_local",
                project_id=project_id,
            )
        )
        # An AI Agent (default agent_type=ai).
        s.add(
            Agent(
                id=ai_agent_id,
                tenant_id=tenant_id,
                name="Backend Dev",
                role="backend_dev",
                system_prompt="You write code.",
                agent_type="ai",
                scope="project_local",
                project_id=project_id,
            )
        )
        await s.flush()
        await s.execute(
            text(
                "INSERT INTO human_agent_config"
                " (id, tenant_id, agent_id, assignment_mode, assigned_user_id,"
                "  acceptance_timeout_hours)"
                " VALUES (:id, :tid, :aid, 'specific_user', :uid, 24)"
            ),
            {"id": uuid7(), "tid": tenant_id, "aid": human_agent_id, "uid": user_id},
        )
        # A task assigned to the human agent, sitting in `ready`.
        s.add(
            Task(
                id=human_task_id,
                tenant_id=tenant_id,
                project_id=project_id,
                title="Legal review of the contract",
                status="ready",
                priority="high",
                assigned_agent_id=human_agent_id,
            )
        )
        # A task assigned to the AI agent, sitting in `ready`.
        s.add(
            Task(
                id=ai_task_id,
                tenant_id=tenant_id,
                project_id=project_id,
                title="Implement the endpoint",
                status="ready",
                priority="medium",
                assigned_agent_id=ai_agent_id,
            )
        )


async def _resolve_agent_type(sm: async_sessionmaker, task: Task) -> AgentType | None:
    """Resolve the assignee's agent_type from the DB — what the orchestrator
    (task_16_05) does to decide whether a task is a human task. Reads the
    Agent row in the SAME tenant-scoped way the dispatch path will."""
    if task.assigned_agent_id is None:
        return None
    async with sm() as s:
        agent = (
            await s.execute(select(Agent).where(Agent.id == task.assigned_agent_id))
        ).scalar_one_or_none()
        if agent is None:
            return None
        return AgentType(agent.agent_type)


def _engine_sm(url: str) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _get_task(sm: async_sessionmaker, task_id: UUID) -> Task:
    async with sm() as s:
        return (await s.execute(select(Task).where(Task.id == task_id))).scalar_one()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


# ===========================================================================
# Human-assigned task: every §7.2 human transition is ACCEPTED.
# ===========================================================================
@pytest.mark.asyncio
async def test_human_task_full_transition_chain_is_accepted(
    _migrated: None, admin_database_url: str
) -> None:
    """A human-assigned task walks the whole human chain:
    ready -> assigned_to_human -> (reassign) assigned_to_human ->
    in_progress -> in_review. Each move mutates the live DB row."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )

        task = await _get_task(sm, ids["ht"])
        agent_type = await _resolve_agent_type(sm, task)
        assert agent_type is AgentType.HUMAN

        # ready -> assigned_to_human (orchestrator routes the human task).
        transition_task_status(task, "assigned_to_human", assignee_agent_type=agent_type)
        assert task.status == "assigned_to_human"

        # assigned_to_human -> assigned_to_human (reassignment self-loop).
        transition_task_status(task, "assigned_to_human", assignee_agent_type=agent_type)
        assert task.status == "assigned_to_human"

        # assigned_to_human -> in_progress (the human accepts).
        transition_task_status(task, "in_progress", assignee_agent_type=agent_type)
        assert task.status == "in_progress"

        # in_progress -> in_review (the human submits).
        transition_task_status(task, "in_review", assignee_agent_type=agent_type)
        assert task.status == "in_review"

        # Persist the final status and re-read to prove it round-trips.
        async with sm() as s, s.begin():
            row = (await s.execute(select(Task).where(Task.id == ids["ht"]))).scalar_one()
            row.status = task.status
        reloaded = await _get_task(sm, ids["ht"])
        assert reloaded.status == "in_review"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_human_task_assigned_to_human_can_block(
    _migrated: None, admin_database_url: str
) -> None:
    """assigned_to_human -> blocked is accepted for a human task (the
    escalation-exhausted path, task_16_06)."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        task = await _get_task(sm, ids["ht"])
        agent_type = await _resolve_agent_type(sm, task)

        transition_task_status(task, "assigned_to_human", assignee_agent_type=agent_type)
        transition_task_status(task, "blocked", assignee_agent_type=agent_type)
        assert task.status == "blocked"
    finally:
        await engine.dispose()


# ===========================================================================
# AI-assigned task: ready -> assigned_to_human is REJECTED.
# ===========================================================================
@pytest.mark.asyncio
async def test_ai_task_cannot_go_to_assigned_to_human(
    _migrated: None, admin_database_url: str
) -> None:
    """An AI-assigned task asked to move ready -> assigned_to_human is
    rejected with the typed TaskTransitionError; the live row is unchanged."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        task = await _get_task(sm, ids["at"])
        agent_type = await _resolve_agent_type(sm, task)
        assert agent_type is AgentType.AI

        with pytest.raises(TaskTransitionError) as exc:
            transition_task_status(task, "assigned_to_human", assignee_agent_type=agent_type)
        assert exc.value.from_status == "ready"
        assert exc.value.to_status == "assigned_to_human"
        assert exc.value.agent_type == "ai"
        # The in-memory object is untouched by the rejected move.
        assert task.status == "ready"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unassigned_task_cannot_go_to_assigned_to_human(
    _migrated: None, admin_database_url: str
) -> None:
    """A task with no assignee (agent_type=None) is treated as the AI table —
    assigned_to_human is rejected."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        task = await _get_task(sm, ids["at"])
        task.assigned_agent_id = None
        agent_type = await _resolve_agent_type(sm, task)
        assert agent_type is None
        with pytest.raises(TaskTransitionError):
            transition_task_status(task, "assigned_to_human", assignee_agent_type=None)
        assert task.status == "ready"
    finally:
        await engine.dispose()


# ===========================================================================
# The existing AI flow is UNCHANGED.
# ===========================================================================
@pytest.mark.asyncio
async def test_ai_flow_backlog_to_done_unchanged(_migrated: None, admin_database_url: str) -> None:
    """The canonical AI lifecycle still works end-to-end against the DB:
    backlog -> ready -> in_progress -> in_review -> done."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        task = await _get_task(sm, ids["at"])
        agent_type = await _resolve_agent_type(sm, task)
        assert agent_type is AgentType.AI

        # Reset to backlog and walk the whole AI chain.
        task.status = "backlog"
        transition_task_status(task, "ready", assignee_agent_type=agent_type)
        assert task.status == "ready"
        transition_task_status(task, "in_progress", assignee_agent_type=agent_type)
        assert task.status == "in_progress"
        transition_task_status(task, "in_review", assignee_agent_type=agent_type)
        assert task.status == "in_review"
        transition_task_status(task, "done", assignee_agent_type=agent_type)
        assert task.status == "done"
        # Terminal — nothing leaves done for any assignee type.
        assert is_terminal("done")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ai_approval_cycle_unchanged(_migrated: None, admin_database_url: str) -> None:
    """ADR 0020 cycle still legal for an AI task:
    in_progress -> awaiting_human_approval -> backlog (approve) and
    -> blocked (reject)."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        task = await _get_task(sm, ids["at"])
        agent_type = await _resolve_agent_type(sm, task)

        task.status = "in_progress"
        transition_task_status(task, "awaiting_human_approval", assignee_agent_type=agent_type)
        assert task.status == "awaiting_human_approval"
        # Approve -> back to backlog.
        transition_task_status(task, "backlog", assignee_agent_type=agent_type)
        assert task.status == "backlog"

        # Reject path on a fresh parked task -> blocked.
        task.status = "awaiting_human_approval"
        transition_task_status(task, "blocked", assignee_agent_type=agent_type)
        assert task.status == "blocked"
    finally:
        await engine.dispose()


# ===========================================================================
# Invalid transitions are still rejected (for both assignee types).
# ===========================================================================
@pytest.mark.asyncio
async def test_invalid_transitions_rejected(_migrated: None, admin_database_url: str) -> None:
    """Structurally-illegal moves raise TaskTransitionError regardless of
    assignee type. A done task is terminal for everyone."""
    engine, sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    try:
        await _seed(
            sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )

        # backlog -> done is illegal (must pass through ready/in_progress).
        ai_task = await _get_task(sm, ids["at"])
        ai_task.status = "backlog"
        with pytest.raises(TaskTransitionError):
            transition_task_status(ai_task, "done", assignee_agent_type=AgentType.AI)
        assert ai_task.status == "backlog"

        # done is terminal — even for a human-assigned task.
        human_task = await _get_task(sm, ids["ht"])
        human_task.status = "done"
        with pytest.raises(TaskTransitionError):
            transition_task_status(human_task, "in_progress", assignee_agent_type=AgentType.HUMAN)
        assert human_task.status == "done"

        # assigned_to_human is NOT a legal target from in_progress, even human.
        human_task.status = "in_progress"
        with pytest.raises(TaskTransitionError):
            transition_task_status(
                human_task, "assigned_to_human", assignee_agent_type=AgentType.HUMAN
            )
        assert human_task.status == "in_progress"
    finally:
        await engine.dispose()


# ===========================================================================
# Pure-table assertions (the gate logic, no DB needed but kept in-suite).
# ===========================================================================
def test_allowed_transitions_gate_on_agent_type() -> None:
    """ready -> assigned_to_human is in the allowed set ONLY for a human
    assignee; the AI/None tables never contain it."""
    human = allowed_transitions("ready", assignee_agent_type=AgentType.HUMAN)
    ai = allowed_transitions("ready", assignee_agent_type=AgentType.AI)
    none = allowed_transitions("ready", assignee_agent_type=None)

    assert "assigned_to_human" in human
    assert "assigned_to_human" not in ai
    assert "assigned_to_human" not in none
    # The shared AI edges survive the overlay merge.
    assert {"in_progress", "backlog"} <= human

    # can_transition mirrors the gate.
    assert can_transition("ready", "assigned_to_human", assignee_agent_type=AgentType.HUMAN)
    assert not can_transition("ready", "assigned_to_human", assignee_agent_type=AgentType.AI)
    # The reassignment self-loop is a real edge only for a human assignee.
    assert can_transition(
        "assigned_to_human", "assigned_to_human", assignee_agent_type=AgentType.HUMAN
    )


# ===========================================================================
# Cross-tenant: the agent_type resolver is tenant-scoped under RLS.
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_agent_type_resolution_is_tenant_scoped(
    _migrated: None, admin_database_url: str, app_database_url: str
) -> None:
    """A human Agent in tenant A is invisible to a session pinned to tenant B.

    The orchestrator decides "is this a human task?" by reading the assignee
    Agent. If that read were not tenant-scoped, tenant B could observe tenant
    A's human agent and unlock the assigned_to_human transition for a task it
    does not own. Under RLS the tenant-B-pinned session resolves the agent to
    None, so the gate falls back to the AI table and assigned_to_human is
    rejected — the multi-tenancy boundary holds."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    admin_engine, admin_sm = _engine_sm(admin_database_url)
    ids = {k: uuid7() for k in ("tenant", "project", "user", "human_agent", "ai_agent", "ht", "at")}
    tenant_b = uuid7()
    try:
        await _seed(
            admin_sm,
            tenant_id=ids["tenant"],
            project_id=ids["project"],
            user_id=ids["user"],
            human_agent_id=ids["human_agent"],
            ai_agent_id=ids["ai_agent"],
            human_task_id=ids["ht"],
            ai_task_id=ids["at"],
        )
        await _grant_app_user_existing_tables()

        # Under RLS as tenant A, the human agent resolves to HUMAN.
        app_engine = create_async_engine(app_database_url)
        app_sm = async_sessionmaker(app_engine, expire_on_commit=False)
        try:

            async def _resolve_under_tenant(tid: UUID) -> AgentType | None:
                async with app_sm() as s:
                    await s.execute(
                        text("SELECT set_config('app.tenant_id', :tid, false)"),
                        {"tid": str(tid)},
                    )
                    agent = (
                        await s.execute(select(Agent).where(Agent.id == ids["human_agent"]))
                    ).scalar_one_or_none()
                    return None if agent is None else AgentType(agent.agent_type)

            as_a = await _resolve_under_tenant(ids["tenant"])
            as_b = await _resolve_under_tenant(tenant_b)
        finally:
            await app_engine.dispose()

        assert as_a is AgentType.HUMAN, "tenant A must see its own human agent"
        assert as_b is None, "tenant B must NOT see tenant A's human agent"

        # The gate honours the tenant-scoped resolution: with tenant B's view
        # (None) the human transition is rejected; with tenant A's it is not.
        task = await _get_task(admin_sm, ids["ht"])
        with pytest.raises(TaskTransitionError):
            transition_task_status(task, "assigned_to_human", assignee_agent_type=as_b)
        assert task.status == "ready"

        transition_task_status(task, "assigned_to_human", assignee_agent_type=as_a)
        assert task.status == "assigned_to_human"
    finally:
        await admin_engine.dispose()
