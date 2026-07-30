"""Integration test — A2 (inter-run reviewer feedback read).

When a task was rejected by the AI reviewer on an earlier pass, the orchestrator
must read its ``review_comment`` audit events (newest first, capped) and project
them to the feedback shape the worker forwards to the implementer runtime. A task
with no prior rejection reads as ``[]`` (no behaviour change).

Marked ``integration`` — it needs the migrated Postgres schema for
``task_audit_events`` and is NOT part of the offline unit suite.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from api_server.db.domain import Project, Task
from api_server.db.models import Organization
from api_server.db.task_audit_repo import append_audit_event
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
    """A BYPASSRLS async sessionmaker on the test DB — the engine the dispatcher uses."""
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


async def _seed_task(sm: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE task_audit_events, executions, task_dependencies, tasks,"
                " agents, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug="t-prior-fb"))
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
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="implement X",
                description="acceptance: X must work",
                status="ready",
                priority="medium",
            )
        )
    return ids


@pytest.mark.asyncio
async def test_reads_prior_review_feedback_newest_first(
    _migrated: None, sessionmaker: async_sessionmaker
) -> None:
    ids = await _seed_task(sessionmaker)
    async with sessionmaker() as s, s.begin():
        for i in range(2):
            await append_audit_event(
                s,
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                kind="review_comment",
                actor="agent:reviewer",
                payload={
                    "failed_criterion": f"crit-{i}",
                    "what_to_fix": f"fix-{i}",
                    "testreport_evidence": f"evi-{i}",
                    "escalated": False,
                    "reason": None,
                },
            )
    dispatcher = _dispatcher(sessionmaker)
    async with sessionmaker() as s:
        task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        feedback = await dispatcher._read_prior_review_feedback(s, task)
    # Newest first (crit-1 then crit-0); only the projected fields are kept.
    assert [f["failed_criterion"] for f in feedback] == ["crit-1", "crit-0"]
    assert feedback[0] == {
        "failed_criterion": "crit-1",
        "what_to_fix": "fix-1",
        "testreport_evidence": "evi-1",
    }


@pytest.mark.asyncio
async def test_no_prior_rejection_reads_empty(
    _migrated: None, sessionmaker: async_sessionmaker
) -> None:
    ids = await _seed_task(sessionmaker)
    dispatcher = _dispatcher(sessionmaker)
    async with sessionmaker() as s:
        task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        assert await dispatcher._read_prior_review_feedback(s, task) == []
