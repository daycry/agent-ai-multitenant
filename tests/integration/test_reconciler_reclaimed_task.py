"""El reconciler no decide una reclamación con el run de la reclamación anterior.

Auditoría 2026-09-01 (A-05), `task_cv_10`. `_reconcile_stuck_tasks` tomaba la
ÚLTIMA ejecución de la tarea por `created_at` sin compararla con
`task.started_at`. Una tarea re-reclamada (rechazo → backlog → ready →
in_progress) conserva las ejecuciones de la vuelta anterior; si el run nuevo aún
no ha creado su fila, la «última» es la vieja —terminal y asentada— y la tarea se
transicionaba con el veredicto de un run que no es el suyo.

Dos tareas, la misma ejecución vieja `done` de hace 10 minutos:

  * reclamada hace 6 minutos → NO se transiciona (la reclamación es reciente: el
    run nuevo puede estar aún en cola) y sigue `in_progress`;
  * reclamada hace 2 horas  → se trata como reclamación huérfana (a2) y vuelve a
    `ready`, en vez de pasar a `in_review` con un `done` que no es de esta vuelta.

Exige PostgreSQL con migraciones (stack de pruebas).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _FakeRedis:
    """Captura los `xadd` (directos y por pipeline, como publica `api_server.events`)."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def xadd(self, stream: str, fields: dict[str, Any], **_kw: Any) -> str:
        self.events.append({"stream": stream, **fields})
        return "0-1"

    def pipeline(self, *_a: Any, **_k: Any) -> _FakeRedis:
        return self

    async def execute(self) -> list[Any]:
        return []

    async def __aenter__(self) -> _FakeRedis:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task_recent_claim": uuid4(),
        "task_old_claim": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, plans, agents, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T rec', 't-reclaimed')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        for key, claimed in (
            ("task_recent_claim", "6 minutes"),
            ("task_old_claim", "2 hours"),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
                " started_at) VALUES ($1, $2, $3, 'task', 'in_progress', 'medium',"
                f" now() - interval '{claimed}')",
                ids[key],
                ids["tenant"],
                ids["project"],
            )
            # La ejecución de la VUELTA ANTERIOR: `done`, asentada, y creada ANTES
            # de la reclamación actual (para la vieja, mucho antes).
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, created_at,"
                " started_at, completed_at) VALUES ($1, $2, $3, 'done',"
                " now() - interval '10 minutes' - $4::interval,"
                " now() - interval '10 minutes' - $4::interval,"
                " now() - interval '10 minutes' - $4::interval)",
                uuid4(),
                ids["tenant"],
                ids[key],
                timedelta(0) if key == "task_recent_claim" else timedelta(hours=3),
            )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_reclaimed_task_is_not_decided_by_the_run_of_its_previous_claim(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _reconcile_pipeline_state_async

    ids = await _seed(migrations_pg_dsn)
    redis = _FakeRedis()

    result = await _reconcile_pipeline_state_async(
        workers_settings,  # type: ignore[arg-type]
        redis=redis,
        now=datetime.now(UTC),
        stuck_task_min_age=timedelta(minutes=5),
        review_min_age=timedelta(minutes=5),
    )

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            recent = await session.get(Task, ids["task_recent_claim"])
            old = await session.get(Task, ids["task_old_claim"])
    finally:
        await engine.dispose()

    assert recent is not None and recent.status == TaskStatus.IN_PROGRESS.value, (
        "la reclamación de hace 6 minutos se decidió con el `done` de hace 10:"
        " ese run es de la vuelta anterior"
    )
    assert old is not None and old.status == TaskStatus.READY.value, (
        "la reclamación de hace 2 horas sin run propio es huérfana: vuelve a `ready`,"
        " no a `in_review` con un `done` ajeno"
    )
    assert old.started_at is None and old.assigned_agent_id is None
    assert result["stuck_tasks"] == 1

    new_statuses = [
        json.loads(e["payload"]).get("new_status")
        for e in redis.events
        if e.get("type") == "task.status_changed"
    ]
    assert TaskStatus.IN_REVIEW.value not in new_statuses
    assert new_statuses.count(TaskStatus.READY.value) == 1
