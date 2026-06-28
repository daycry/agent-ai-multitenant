"""Integration: the convergence reconciler auto-starts the review-runtime.

Closes the convergence GAP: when the live ``task.status_changed → done`` event is
lost (a Redis blip), ONLY ``workers.maintenance._reconcile_complete_plans`` moves the
plan to ``pending_human_validation``. Until the fix it stopped at the transition,
leaving the plan stalled with NO review_session (the reviewer URLs 404, human
validation never arms). Now the reconciler fires the SAME shared autostart the
orchestrator's live path uses.

Two scenarios against real Postgres (the worker's Celery ``app`` is monkeypatched to
a recording fake so no broker is needed):

  * an ``in_progress`` plan whose tasks are ALL ``done`` ⇒ transitions AND enqueues
    ``compose_review_runtime`` with the project's resolved ``main_image``;
  * IDEMPOTENT: the same plan WITH an active (``running``) review session ⇒ still
    transitions, but the builder returns ``None`` so NO second runtime is enqueued.

NOT RUN by the implementing agent (needs a live Postgres at TEST_PG_*).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import workers.maintenance as m
from alembic import command
from api_server.db.domain import Plan, Project, Task
from api_server.db.models import Organization
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


class _RecordingCelery:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def send_task(self, name: str, **kwargs: object) -> None:
        self.calls.append({"name": name, **kwargs})


async def _seed(sm: async_sessionmaker, *, with_session: bool = False) -> dict:
    ids: dict = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "tasks": [uuid4(), uuid4()],
    }
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE review_sessions, executions, task_dependencies, tasks, plans,"
                " agents, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="Recon tenant", slug="recon-tenant"))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Backend",
                slug="backend",
                status="active",
                is_template=False,
                repository_config={"review_image": "backend:plan-1"},
            )
        )
        await s.flush()
        s.add(
            Plan(
                id=ids["plan"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Completion plan",
                slug="completion-plan",
                status="in_progress",
                specification={"tests_humans": [{"id": "h1", "description": "login works"}]},
            )
        )
        await s.flush()
        for task_id in ids["tasks"]:
            s.add(
                Task(
                    id=task_id,
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    plan_id=ids["plan"],
                    title="t",
                    status="done",
                    priority="medium",
                )
            )
        if with_session:
            await s.flush()
            await s.execute(
                text(
                    "INSERT INTO review_sessions"
                    " (id, tenant_id, plan_id, spec, status, container_ids, expires_at)"
                    " VALUES (:id, :tenant, :plan, '{}'::jsonb, 'running', '[]'::jsonb, :expires)"
                ),
                {
                    "id": uuid4(),
                    "tenant": ids["tenant"],
                    "plan": ids["plan"],
                    "expires": datetime.now(UTC) + timedelta(hours=48),
                },
            )
    return ids


async def _plan_status(sm: async_sessionmaker, plan_id) -> str:  # type: ignore[no-untyped-def]
    async with sm() as s:
        plan = (await s.execute(select(Plan).where(Plan.id == plan_id))).scalar_one()
        return plan.status


@pytest.mark.asyncio
async def test_reconciler_autostarts_review_for_completed_plan(
    _migrated: None, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(admin_database_url)
    celery = _RecordingCelery()
    monkeypatch.setattr(m, "app", celery)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        transitioned = await m._reconcile_complete_plans(sm)

        assert transitioned == 1
        assert await _plan_status(sm, ids["plan"]) == "pending_human_validation"
        assert len(celery.calls) == 1
        call = celery.calls[0]
        assert call["name"] == "workers.compose_review_runtime"
        assert call["queue"] == "review"
        assert call["kwargs"]["request"]["plan_id"] == str(ids["plan"])
        assert call["kwargs"]["request"]["main_image"] == "backend:plan-1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciler_does_not_autostart_when_session_active(
    _migrated: None, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(admin_database_url)
    celery = _RecordingCelery()
    monkeypatch.setattr(m, "app", celery)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, with_session=True)

        transitioned = await m._reconcile_complete_plans(sm)

        # The plan still transitions, but an active session ⇒ no second runtime.
        assert transitioned == 1
        assert await _plan_status(sm, ids["plan"]) == "pending_human_validation"
        assert celery.calls == []
    finally:
        await engine.dispose()
