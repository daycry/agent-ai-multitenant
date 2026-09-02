"""El sweeper finaliza en la PRIMERA pasada una fila `running` cuyo contenedor ya
terminó, con el resultado real de sus logs (`task_cv_12`, A-04).

Dos filas `running` de hace 10 minutos (más que la gracia de huérfanos, mucho
menos que las 7 horas del umbral de edad), las dos con contenedor `exited`:

  * la primera dejó `execution.finished` (done) en los logs → la fila acaba `done`
    con ese output y la tarea pasa a `in_review`; nada de «worker loss»;
  * la segunda murió sin línea terminal → se sella YA como `failed`
    (`stale_after_worker_loss`) y la tarea a `blocked`, sin esperar 7 horas.

Los contenedores de las dos, ya con fila terminal, los retira el reaper de
`exited` de la misma pasada.

Exige PostgreSQL con migraciones (stack de pruebas).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.domain import Execution, Task, TaskStatus
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


class _ExitedRunner:
    """Docker falso: dos contenedores gestionados en `exited`, con sus logs."""

    def __init__(self, exited: dict[str, tuple[str, str, int]]) -> None:
        # container_id -> (execution_id, logs, exit_code)
        self._exited = exited
        self.killed: list[str] = []
        self.removed: list[str] = []

    def kill_by_label(self, execution_id: str) -> int:
        self.killed.append(execution_id)
        return 0

    def list_exited_managed(self) -> list[tuple[str, str]]:
        return [(cid, exec_id) for cid, (exec_id, _logs, _code) in self._exited.items()]

    def list_managed_execution_ids(self) -> set[str] | None:
        return {exec_id for exec_id, _logs, _code in self._exited.values()}

    def read_exited_container(self, container_id: str) -> tuple[str, int | None] | None:
        entry = self._exited.get(container_id)
        return None if entry is None else (entry[1], entry[2])

    def remove_container(self, container_id: str) -> bool:
        self.removed.append(container_id)
        return True


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):  # type: ignore[no-untyped-def]
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
        "task_finished": uuid4(),
        "exec_finished": uuid4(),
        "task_silent": uuid4(),
        "exec_silent": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE task_audit_events, executions, tasks, projects, organizations"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-exited')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        for task_key, exec_key, celery_id in (
            ("task_finished", "exec_finished", "celery-finished"),
            ("task_silent", "exec_silent", "celery-silent"),
        ):
            await conn.execute(
                "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority,"
                " started_at) VALUES ($1, $2, $3, 'task', 'in_progress', 'medium',"
                " now() - interval '10 minutes')",
                ids[task_key],
                ids["tenant"],
                ids["project"],
            )
            await conn.execute(
                "INSERT INTO executions (id, tenant_id, task_id, status, started_at,"
                " container_launched_at, celery_task_id) VALUES ($1, $2, $3, 'running',"
                " now() - interval '10 minutes', now() - interval '10 minutes', $4)",
                ids[exec_key],
                ids["tenant"],
                ids[task_key],
                celery_id,
            )
        return ids
    finally:
        await conn.close()


def _finished_logs() -> str:
    return "\n".join(
        [
            json.dumps({"event": "execution.step", "index": 0, "kind": "node", "node": "plan"}),
            json.dumps(
                {
                    "event": "execution.finished",
                    "result": {
                        "status": "done",
                        "output": "login implementado",
                        "iterations": 3,
                        "usage": {"total_tokens": 900, "model_calls": 3},
                    },
                }
            ),
        ]
    )


@pytest.mark.asyncio
async def test_the_first_sweep_finalizes_with_the_containers_real_result(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    from workers.maintenance import _sweep_stale_executions_async

    ids = await _seed(migrations_pg_dsn)
    runner = _ExitedRunner(
        {
            "c-finished": (str(ids["exec_finished"]), _finished_logs(), 0),
            "c-silent": (str(ids["exec_silent"]), "arrancando...\n", 137),
        }
    )

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
            exec_finished = await session.get(Execution, ids["exec_finished"])
            exec_silent = await session.get(Execution, ids["exec_silent"])
            task_finished = await session.get(Task, ids["task_finished"])
            task_silent = await session.get(Task, ids["task_silent"])
    finally:
        await engine.dispose()

    assert exec_finished is not None
    assert exec_finished.status == "done", "el resultado real del contenedor se perdió"
    assert exec_finished.output == "login implementado"
    assert exec_finished.iterations == 3 and exec_finished.total_tokens == 900
    assert exec_finished.completed_at is not None
    assert task_finished is not None and task_finished.status == TaskStatus.IN_REVIEW.value

    assert exec_silent is not None and exec_silent.status == "failed"
    assert exec_silent.abort_code == "stale_after_worker_loss"
    assert exec_silent.completed_at is not None
    assert task_silent is not None and task_silent.status == TaskStatus.BLOCKED.value

    assert result["recovered"] == 1
    assert result["swept"] == 1
    assert sorted(runner.removed) == ["c-finished", "c-silent"]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        events = await conn.fetch(
            "SELECT task_id, kind, payload::text AS payload FROM task_audit_events"
            " WHERE task_id = ANY($1::uuid[])",
            [ids["task_finished"], ids["task_silent"]],
        )
    finally:
        await conn.close()
    by_task = {str(e["task_id"]): (e["kind"], e["payload"]) for e in events}
    assert by_task[str(ids["task_finished"])][0] == "execution_recovered_by_sweeper"
    assert "container_exited_without_terminal" in by_task[str(ids["task_silent"])][1]
