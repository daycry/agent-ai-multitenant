"""PROJ-04/PROJ-05 (auditoría proyecto 2026-07-17): el dispatch respeta el
equipo del proyecto y se auto-repara ante presets muertos.

- `_candidates` restringe el pool a los `team_members` de `project.team_id`
  cuando el proyecto tiene equipo (los agentes globales del tenant que no son
  del equipo ya no reciben sus tareas). Sin equipo → pool actual (project_local
  + globales).
- Un `assigned_agent_id` preset que apunta a un agente soft-borrado dejaba la
  tarea `ready` para siempre (el preset gana SIEMPRE y el reload devolvía
  None). Ahora se limpia el preset (audit event testigo) y el siguiente
  dispatch cae a la política del proyecto.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, Project, Task, Team, TeamMember
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from orchestrator.events import EVENT_TASK_STATUS_CHANGED, TaskEvent
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"
_SCRIPTED_FINISH = {
    "kind": "scripted",
    "decisions": [{"kind": "finish", "output": "done"}],
}


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


def _agent(ids: dict[str, UUID], key: str, *, scope: str, project_id: UUID | None) -> Agent:
    return Agent(
        id=ids[key],
        tenant_id=ids["tenant"],
        name=key,
        role="backend-dev",
        system_prompt="x",
        agent_type="ai",
        scope=scope,
        project_id=project_id,
        model_config=_SCRIPTED_FINISH,
    )


async def _seed(
    sm: async_sessionmaker,
    *,
    with_team: bool,
    preset_deleted_agent: bool = False,
    member_busy: bool = False,
) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "team": uuid4(),
        "member": uuid4(),
        "outsider": uuid4(),
        "task": uuid4(),
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE executions, task_dependencies, tasks, team_members, teams, agents,"
                " projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug=f"ts-{ids['tenant'].hex[:8]}"))
        await s.flush()
        s.add(Team(id=ids["team"], tenant_id=ids["tenant"], name="Equipo"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="P",
                status="active",
                is_template=False,
                team_id=ids["team"] if with_team else None,
                worker_config={"assignment_policy": "load_balanced"},
            )
        )
        await s.flush()
        # `member` pertenece al equipo; `outsider` es global del tenant pero
        # NO es miembro.
        member = _agent(ids, "member", scope="global_tenant_template", project_id=None)
        outsider = _agent(ids, "outsider", scope="global_tenant_template", project_id=None)
        if preset_deleted_agent:
            member.deleted_at = datetime.now(UTC)
        s.add_all([member, outsider])
        await s.flush()
        s.add(TeamMember(team_id=ids["team"], agent_id=ids["member"]))
        await s.flush()
        if member_busy:
            # Carga a `member` con una tarea activa: load_balanced preferiría a
            # `outsider` — solo la restricción de equipo elige a `member`.
            s.add(
                Task(
                    id=uuid4(),
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    title="busy",
                    description="d",
                    status="in_progress",
                    priority="medium",
                    assigned_agent_id=ids["member"],
                )
            )
            await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="t",
                description="d",
                status="ready",
                priority="medium",
                assigned_agent_id=ids["member"] if preset_deleted_agent else None,
            )
        )
    return ids


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=celery_app,
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL),
    )


def _ready_event(ids: dict[str, UUID]) -> TaskEvent:
    return TaskEvent(
        stream_id="1-0",
        type=EVENT_TASK_STATUS_CHANGED,
        tenant_id=str(ids["tenant"]),
        project_id=str(ids["project"]),
        task_id=str(ids["task"]),
        occurred_at="2026-07-17T00:00:00+00:00",
        payload={"old_status": "backlog", "new_status": "ready"},
    )


@pytest.mark.asyncio
async def test_team_project_only_dispatches_to_team_members(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_team=True, member_busy=True)

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["member"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_project_without_team_keeps_global_pool(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_team=False)

        await _dispatcher(sm).handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        # Sin equipo: cualquiera de los dos globales puede tomarla.
        assert task.status == "in_progress"
        assert task.assigned_agent_id in (ids["member"], ids["outsider"])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_dead_preset_is_cleared_and_next_dispatch_recovers(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        # preset → `member` (soft-borrado). `outsider` sigue vivo y global.
        ids = await _seed(sm, with_team=False, preset_deleted_agent=True)

        dispatcher = _dispatcher(sm)
        await dispatcher.handle(_ready_event(ids))

        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
            audit_kinds = list(
                (
                    await s.execute(
                        text("SELECT kind FROM task_audit_events WHERE task_id = :t"),
                        {"t": ids["task"]},
                    )
                ).scalars()
            )
        # El preset muerto se limpió (auto-reparación) con testigo de audit.
        assert task.assigned_agent_id != ids["member"]
        assert "assignment_preset_cleared" in audit_kinds

        # El siguiente dispatch (o el mismo) recae en la política y despacha.
        await dispatcher.handle(_ready_event(ids))
        async with sm() as s:
            task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert task.status == "in_progress"
        assert task.assigned_agent_id == ids["outsider"]
    finally:
        await engine.dispose()
