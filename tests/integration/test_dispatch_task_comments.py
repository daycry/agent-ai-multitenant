"""Integration test — Feature C (human comments → agent prompt, read side).

The orchestrator surfaces human comments to the agent run by reusing ``PlanComment``:
the comments that apply to a task are its task-scoped ones (``target_kind='task'`` with
``target_ref`` = the task's plan-spec id) plus the plan-level ones (``target_kind=
'plan'``). Phase comments and comments for OTHER tasks/plans are excluded.

Marked ``integration`` — needs the migrated Postgres schema (``plan_comments``).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from api_server.chat.sync_to_kanban import PLAN_TASK_SPEC_ID_KEY
from api_server.db.domain import Plan, Project, Task
from api_server.db.models import Organization
from api_server.db.plan_comment import PlanComment
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.dispatch import TaskDispatcher
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings

pytestmark = pytest.mark.integration

TEST_REDIS_URL = "redis://localhost:6379/15"


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest_asyncio.fixture()
async def sessionmaker(admin_database_url: str):  # type: ignore[no-untyped-def]
    engine = create_async_engine(admin_database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _dispatcher(sm: async_sessionmaker) -> TaskDispatcher:
    return TaskDispatcher(
        sessionmaker=sm,
        celery_app=build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL)),
        settings=OrchestratorSettings(redis_url=TEST_REDIS_URL, dispatch_queue="default"),
    )


async def _seed(sm: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4(), "task": uuid4()}
    spec_id = "t1"
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE plan_comments, task_audit_events, executions, task_dependencies,"
                " tasks, plans, agents, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug="t-comments"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="P",
                status="active",
                is_template=False,
                worker_config={},
            )
        )
        await s.flush()
        s.add(Plan(id=ids["plan"], tenant_id=ids["tenant"], project_id=ids["project"], title="Pl"))
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                plan_id=ids["plan"],
                title="Auditar",
                status="ready",
                priority="medium",
                inputs={PLAN_TASK_SPEC_ID_KEY: spec_id},
            )
        )

        def _c(kind: str, ref: str | None, content: str) -> PlanComment:
            return PlanComment(
                tenant_id=ids["tenant"],
                plan_id=ids["plan"],
                target_kind=kind,
                target_ref=ref,
                author_user_id=None,
                content=content,
            )

        s.add_all(
            [
                _c("task", spec_id, "comentario de TAREA"),
                _c("plan", None, "comentario de PLAN"),
                _c("phase", "0", "comentario de FASE"),  # excluido
                _c("task", "otra", "comentario de OTRA tarea"),  # excluido
            ]
        )
    return ids


@pytest.mark.asyncio
async def test_reads_task_and_plan_comments_excludes_others(
    _migrated: None, sessionmaker: async_sessionmaker
) -> None:
    ids = await _seed(sessionmaker)
    dispatcher = _dispatcher(sessionmaker)
    async with sessionmaker() as s:
        task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        comments = await dispatcher._read_relevant_comments(s, task)
    contents = {c["content"] for c in comments}
    assert contents == {"comentario de TAREA", "comentario de PLAN"}
    assert {c["scope"] for c in comments} == {"task", "plan"}


@pytest.mark.asyncio
async def test_no_comments_reads_empty(_migrated: None, sessionmaker: async_sessionmaker) -> None:
    ids = await _seed(sessionmaker)
    async with sessionmaker() as s, s.begin():
        await s.execute(text("TRUNCATE plan_comments RESTART IDENTITY CASCADE"))
    dispatcher = _dispatcher(sessionmaker)
    async with sessionmaker() as s:
        task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert await dispatcher._read_relevant_comments(s, task) == []
