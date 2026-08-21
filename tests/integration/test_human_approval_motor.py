"""Integration tests for the human-approval engine (task_02_24).

The engine evaluates a sensitive action against a project's
human_approval_policy: an `auto` category proceeds, a `human_required`
one parks the execution and persists an ApprovalRequest a reviewer then
resolves.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.approval_repo import (
    list_pending_approvals,
    request_approval_if_needed,
    requires_human_approval,
    resolve_approval,
)
from api_server.db.domain import ApprovalRequest, Execution, Project, Task
from api_server.db.models import Organization, User
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_POLICY = {"categories": {"production_deploy": "human_required", "code_changes": "auto"}}


# ---------------------------------------------------------------------------
# requires_human_approval — pure policy evaluation
# ---------------------------------------------------------------------------
def test_human_required_category_needs_approval() -> None:
    assert requires_human_approval(_POLICY, "production_deploy") is True


def test_auto_category_does_not_need_approval() -> None:
    assert requires_human_approval(_POLICY, "code_changes") is False


def test_unlisted_category_no_longer_defaults_to_auto() -> None:
    """ADR 0153 (C): lo que la política no lista ya NO corre solo.

    Este test afirmaba lo contrario —`is False`— y era la codificación del
    defecto: `_POLICY` no declara `unlisted_category` ni un `preset`
    reconocible, así que una categoría que no nombra es una política que no se
    sabe interpretar, y ahí se para. La tabla completa de ramas (clave
    explícita, derivación por preset, preset desconocido) vive en
    `tests/unit/test_unlisted_approval_category.py`, que además compara los DOS
    espejos.
    """
    assert requires_human_approval(_POLICY, "send_email") is True
    # Y con la clave escrita, manda la política y no el fail-closed.
    assert requires_human_approval({**_POLICY, "unlisted_category": "auto"}, "send_email") is False


def test_absent_policy_needs_no_approval() -> None:
    assert requires_human_approval(None, "production_deploy") is False
    assert requires_human_approval({}, "production_deploy") is False


def test_bare_category_map_is_accepted() -> None:
    assert requires_human_approval({"production_deploy": "human_required"}, "production_deploy")


# ---------------------------------------------------------------------------
# Persistence — the engine against real Postgres
# ---------------------------------------------------------------------------
@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(session: async_sessionmaker) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task": uuid4(),
        "execution": uuid4(),
        "reviewer": uuid4(),
    }
    async with session() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Approval tenant", slug="approval-tenant"))
        s.add(
            User(
                id=ids["reviewer"],
                email=f"reviewer-{ids['reviewer']}@example.test",
                password_hash="x",
                is_system_admin=True,
            )
        )
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Approval project",
                status="active",
                is_template=False,
                human_approval_policy=_POLICY,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Deploy task",
                status="in_progress",
                priority="high",
            )
        )
        await s.flush()
        s.add(
            Execution(
                id=ids["execution"],
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status="running",
            )
        )
    return ids


@pytest.mark.asyncio
async def test_human_required_action_parks_the_execution(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        async with sm() as s, s.begin():
            execution = await s.get(Execution, ids["execution"])
            project = await s.get(Project, ids["project"])
            assert execution is not None and project is not None
            request = await request_approval_if_needed(
                s,
                execution=execution,
                project=project,
                category="production_deploy",
                action={"tool": "shell_exec", "args": {"command": "deploy.sh"}},
            )
            assert request is not None
            request_id = request.id

        async with sm() as s:
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
            assert execution is not None and task is not None
            assert execution.status == "awaiting_human_approval"
            # ADR 0020 — la tarea también se aparca y el agente queda libre.
            assert task.status == "awaiting_human_approval"
            assert task.assigned_agent_id is None
            pending = await list_pending_approvals(s)
        assert [r.id for r in pending] == [request_id]
        assert pending[0].category == "production_deploy"
        assert pending[0].status == "pending"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_auto_action_does_not_create_a_request(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        async with sm() as s, s.begin():
            execution = await s.get(Execution, ids["execution"])
            project = await s.get(Project, ids["project"])
            assert execution is not None and project is not None
            request = await request_approval_if_needed(
                s, execution=execution, project=project, category="code_changes", action={}
            )
        assert request is None

        async with sm() as s:
            execution = await s.get(Execution, ids["execution"])
            assert execution is not None
            assert execution.status == "running"  # untouched
            assert await list_pending_approvals(s) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approving_a_request_sends_the_task_back_to_backlog(
    _migrated: None, admin_database_url: str
) -> None:
    """ADR 0020 — aprobar cierra la ejecución y devuelve la tarea al
    backlog con su agente liberado, para que el dispatcher la re-asigne."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        async with sm() as s, s.begin():
            execution = await s.get(Execution, ids["execution"])
            project = await s.get(Project, ids["project"])
            assert execution is not None and project is not None
            request = await request_approval_if_needed(
                s, execution=execution, project=project, category="production_deploy", action={}
            )
            assert request is not None
            request_id = request.id

        async with sm() as s, s.begin():
            request = await s.get(ApprovalRequest, request_id)
            assert request is not None
            await resolve_approval(s, request, approved=True, resolver_id=ids["reviewer"])

        async with sm() as s:
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
            assert execution is not None and task is not None
            assert execution.status == "done"
            assert execution.completed_at is not None
            assert task.status == "backlog"
            assert task.assigned_agent_id is None
            request = await s.get(ApprovalRequest, request_id)
        assert request is not None
        assert request.status == "approved"
        assert request.resolved_by == ids["reviewer"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rejecting_a_request_records_the_reason(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        async with sm() as s, s.begin():
            execution = await s.get(Execution, ids["execution"])
            project = await s.get(Project, ids["project"])
            assert execution is not None and project is not None
            request = await request_approval_if_needed(
                s, execution=execution, project=project, category="production_deploy", action={}
            )
            assert request is not None
            request_id = request.id

        async with sm() as s, s.begin():
            request = await s.get(ApprovalRequest, request_id)
            assert request is not None
            await resolve_approval(
                s,
                request,
                approved=False,
                resolver_id=ids["reviewer"],
                reason="deploy window closed",
            )

        async with sm() as s:
            execution = await s.get(Execution, ids["execution"])
            task = await s.get(Task, ids["task"])
            assert execution is not None and task is not None
            # ADR 0020 — rechazar cierra la ejecución y bloquea la tarea.
            assert execution.status == "aborted"
            assert execution.abort_code == "approval_rejected"
            assert task.status == "blocked"
            request = await s.get(ApprovalRequest, request_id)
        assert request is not None
        assert request.status == "rejected"
        assert request.reason == "deploy window closed"
        assert request.resolved_at is not None
    finally:
        await engine.dispose()
