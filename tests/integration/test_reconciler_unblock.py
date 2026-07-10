"""Integration — red del reconciler para el hallazgo #2 (``_reconcile_unblocked_plans``).

Un plan ``blocked`` cuyo snapshot de tareas YA no justifica el bloqueo (una vía lo
desatascó sin re-evaluar el plan, o se perdió el evento) NO puede quedar varado para
siempre: la pasada ``_reconcile_unblocked_plans`` lo revierte a ``in_progress`` con la
transición inversa exacta ``transition_from_blocked`` — sin ping-pong: un plan
genuinamente atascado se queda ``blocked``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Plan
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _FakeRedis:
    async def xadd(self, *_a: Any, **_k: Any) -> None: ...

    async def aclose(self) -> None:  # pragma: no cover - injected, never closed here
        ...


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _seed_blocked_plan(dsn: str, *, task_status: str) -> dict[str, UUID]:
    """Un plan ``blocked`` con UNA tarea en ``task_status``. Con ``ready`` el
    snapshot ya no justifica el bloqueo (hay avance) → debe revertir; con
    ``blocked`` sigue atascado → debe permanecer."""
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4(), "task": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_dependencies, executions, tasks, plans, agents, projects,"
            " organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T ub', 't-unblock')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'Blocked plan', 'blocked')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, plan_id, title, status, priority)"
            " VALUES ($1, $2, $3, $4, 'T', $5, 'medium')",
            ids["task"],
            ids["tenant"],
            ids["project"],
            ids["plan"],
            task_status,
        )
        return ids
    finally:
        await conn.close()


async def _plan_status(settings: Any, plan_id: UUID) -> str:
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            plan = await session.get(Plan, plan_id)
        assert plan is not None
        return str(plan.status)
    finally:
        await engine.dispose()


async def _run_reconcile(settings: Any) -> None:
    from workers.maintenance import _reconcile_pipeline_state_async

    await _reconcile_pipeline_state_async(
        settings,
        redis=_FakeRedis(),
        now=datetime.now(UTC),
        stuck_task_min_age=timedelta(minutes=5),
        review_min_age=timedelta(minutes=5),
    )


@pytest.mark.asyncio
async def test_reconciler_reverts_blocked_plan_that_can_advance(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """La tarea que atascaba el plan quedó ``ready`` por una vía que no re-evaluó
    el plan → el reconciler lo revierte ``blocked → in_progress`` (nadie queda
    varado esperando un segundo click humano)."""
    ids = await _seed_blocked_plan(migrations_pg_dsn, task_status="ready")
    await _run_reconcile(workers_settings)
    assert await _plan_status(workers_settings, ids["plan"]) == "in_progress"


@pytest.mark.asyncio
async def test_reconciler_leaves_genuinely_blocked_plan_alone(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Anti ping-pong: un plan cuya única tarea sigue ``blocked`` (nada avanza)
    permanece ``blocked`` — ``transition_from_blocked`` es la negación EXACTA de
    ``transition_to_blocked``, así que no flapea cada 90 s."""
    ids = await _seed_blocked_plan(migrations_pg_dsn, task_status="blocked")
    await _run_reconcile(workers_settings)
    assert await _plan_status(workers_settings, ids["plan"]) == "blocked"


@pytest.mark.asyncio
async def test_reconciler_leaves_all_done_blocked_plan_alone(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Anti ping-pong con C8 F40 (auditoría 2026-07-10, C-1): un plan ``blocked``
    con TODAS las tareas terminales no viene del escalado por snapshot (ese exige
    ≥1 tarea blocked) sino de la escalación de review expirada
    (``pending_human_validation → blocked``). Revertirlo re-promociona el plan y
    re-arma ``_autostart_review_runtime`` → bucle infinito de runtimes cada 48 h.
    La red NO lo toca; ese bloqueo lo levanta el humano (o el delete síncrono de
    la última tarea blocked, que ya re-evalúa en el router)."""
    ids = await _seed_blocked_plan(migrations_pg_dsn, task_status="done")
    await _run_reconcile(workers_settings)
    assert await _plan_status(workers_settings, ids["plan"]) == "blocked"
