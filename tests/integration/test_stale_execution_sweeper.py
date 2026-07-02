"""Integration test — prod-06 task_prod06_zombi_01 (``workers.sweep_stale_executions``).

A ``running`` execution older than the stale threshold (its Celery child was
SIGKILLed by OOM/hard-limit, leaving the row dangling) must be closed ``failed``
with ``abort_code=stale_after_worker_loss``, its task moved off ``in_progress``
(dag_01 policy → ``blocked``), and its container reaped by label. A fresh
``running`` execution is left untouched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Execution, Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _FakeRunner:
    """Records ``kill_by_label`` / reaper calls instead of touching Docker."""

    def __init__(self, exited: list[tuple[str, str]] | None = None) -> None:
        self.killed: list[str] = []
        self.removed: list[str] = []
        self._exited = list(exited or [])

    def kill_by_label(self, execution_id: str) -> int:
        self.killed.append(execution_id)
        return 1

    def list_exited_managed(self) -> list[tuple[str, str]]:
        return list(self._exited)

    def remove_container(self, container_id: str) -> bool:
        self.removed.append(container_id)
        return True


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


async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "task_stale": uuid4(),
        "task_fresh": uuid4(),
        "exec_stale": uuid4(),
        "exec_fresh": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE executions, tasks, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T zombi', 't-zombi01')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        for tid in (ids["task_stale"], ids["task_fresh"]):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority)"
                " VALUES ($1, $2, $3, 'task', 'in_progress', 'medium')",
                tid,
                ids["tenant"],
                ids["project"],
            )
        # stale execution: started 8h ago (> the 7h threshold). fresh: just now.
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at)"
            " VALUES ($1, $2, $3, 'running', now() - interval '8 hours')",
            ids["exec_stale"],
            ids["tenant"],
            ids["task_stale"],
        )
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, started_at)"
            " VALUES ($1, $2, $3, 'running', now())",
            ids["exec_fresh"],
            ids["tenant"],
            ids["task_fresh"],
        )
        return ids
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_sweep_stale_executions(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    runner = _FakeRunner()

    result = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=runner,
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )

    engine = create_async_engine(workers_settings.database_url)  # type: ignore[attr-defined]
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            exec_stale = await session.get(Execution, ids["exec_stale"])
            exec_fresh = await session.get(Execution, ids["exec_fresh"])
            task_stale = await session.get(Task, ids["task_stale"])
            task_fresh = await session.get(Task, ids["task_fresh"])

        # Stale execution closed failed with the worker-loss code; its task blocked.
        assert exec_stale is not None and exec_stale.status == "failed"
        assert exec_stale.abort_code == "stale_after_worker_loss"
        assert exec_stale.completed_at is not None
        assert task_stale is not None and task_stale.status == TaskStatus.BLOCKED.value
        # Fresh execution + its task untouched.
        assert exec_fresh is not None and exec_fresh.status == "running"
        assert task_fresh is not None and task_fresh.status == TaskStatus.IN_PROGRESS.value
        # The stale container was reaped by label.
        assert runner.killed == [str(ids["exec_stale"])]
        assert result["swept"] == 1
        assert result["reaped"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sweep_removes_exited_containers_of_terminal_executions(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """F0.6 (auditoría 2026-07-02): los contenedores agent-runtime `exited` de
    runs superseded/crasheados no los limpiaba nadie (run_streamed solo limpia
    si el proceso worker sigue vivo; kill_by_label solo mata running) — en un
    host que duerme a diario se acumulan. El sweep los elimina cuando su
    execution ya es terminal (o su fila no existe); NUNCA los de un run vivo."""
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    # Cierra la execution "stale" como terminal ANTES del sweep para simular un
    # run superseded con contenedor exited abandonado.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE executions SET status='failed', abort_code='superseded',"
            " completed_at=now() WHERE id=$1",
            ids["exec_stale"],
        )
    finally:
        await conn.close()

    runner = _FakeRunner(
        exited=[
            ("c-terminal", str(ids["exec_stale"])),  # row terminal → remove
            ("c-live", str(ids["exec_fresh"])),  # row running → conservar
            ("c-orphan", str(uuid4())),  # fila inexistente → remove (basura)
        ]
    )

    result = await _sweep_stale_executions_async(
        workers_settings,  # type: ignore[arg-type]
        runner=runner,
        stale_after=timedelta(hours=7),
        now=datetime.now(UTC),
    )

    assert sorted(runner.removed) == ["c-orphan", "c-terminal"]
    assert result["containers_removed"] == 2
