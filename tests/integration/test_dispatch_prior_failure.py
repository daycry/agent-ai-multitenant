"""P0-7 (investigación 2026-07-11): el dispatch realimenta fracasos no-review.

Un run que muere por bug/loop/budget (failed/aborted) no dejaba NINGÚN rastro
en el prompt del siguiente intento — solo los rechazos del reviewer viajaban
(prior_review_feedback). Ahora `_read_prior_failure` lee la ejecución MÁS
RECIENTE de la task y, si murió sin terminar, threadea
`prior_failure={status, abort_code, output_tail}` para que el runtime avise al
implementador del callejón sin salida anterior. Un run posterior con éxito
(done) apaga la señal — un fracaso viejo no persigue al agente para siempre.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from api_server.db.domain import Execution, Project, Task
from api_server.db.models import Organization
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.integration.test_dispatch_prior_review_feedback import (
    _dispatcher,
    _migrated,
    sessionmaker,
)

from ._partitions import ensure_partition_for

pytestmark = pytest.mark.integration

_fixtures = (_migrated, sessionmaker)  # fixtures reexportadas (pytest las resuelve por nombre)


async def _seed_task(sm: async_sessionmaker) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE task_audit_events, executions, task_dependencies, tasks,"
                " agents, projects, organizations RESTART IDENTITY CASCADE"
            )
        )
        s.add(Organization(id=ids["tenant"], name="T", slug="t-prior-fail"))
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
                description="x",
                status="ready",
                priority="medium",
            )
        )
    return ids


async def _add_execution(
    sm: async_sessionmaker,
    ids: dict[str, UUID],
    *,
    dsn: str,
    status: str,
    abort_code: str | None = None,
    output: str | None = None,
    age_minutes: int = 0,
) -> None:
    creado = datetime.now(UTC) - timedelta(minutes=age_minutes)
    # `executions` está particionada por mes y SIN DEFAULT (ADR 0151): `age_minutes`
    # retrofecha, y con el test corriendo en los primeros minutos del mes esa fila
    # cae en un mes sin partición. Ver
    # docs/03-guides/gotchas/sembrar-filas-retrofechadas-en-tabla-particionada.md
    await ensure_partition_for(dsn, "executions", creado)
    async with sm() as s, s.begin():
        s.add(
            Execution(
                id=uuid4(),
                tenant_id=ids["tenant"],
                task_id=ids["task"],
                status=status,
                abort_code=abort_code,
                output=output,
                created_at=creado,
            )
        )


async def _read(sm: async_sessionmaker, ids: dict[str, UUID]):
    dispatcher = _dispatcher(sm)
    async with sm() as s:
        task = (await s.execute(select(Task).where(Task.id == ids["task"]))).scalar_one()
        return await dispatcher._read_prior_failure(s, task)


@pytest.mark.asyncio
async def test_latest_failed_execution_is_fed_back(
    _migrated: None, sessionmaker: async_sessionmaker, migrations_pg_dsn: str
) -> None:
    ids = await _seed_task(sessionmaker)
    await _add_execution(
        sessionmaker,
        ids,
        dsn=migrations_pg_dsn,
        status="aborted",
        abort_code="loop_detected",
        output="last words of the dying run",
    )
    failure = await _read(sessionmaker, ids)
    assert failure is not None
    assert failure["status"] == "aborted"
    assert failure["abort_code"] == "loop_detected"
    assert "last words" in failure["output_tail"]


@pytest.mark.asyncio
async def test_stale_failure_superseded_by_done_run_is_silent(
    _migrated: None, sessionmaker: async_sessionmaker, migrations_pg_dsn: str
) -> None:
    ids = await _seed_task(sessionmaker)
    await _add_execution(
        sessionmaker,
        ids,
        dsn=migrations_pg_dsn,
        status="failed",
        abort_code="boom",
        age_minutes=10,
    )
    await _add_execution(sessionmaker, ids, dsn=migrations_pg_dsn, status="done", age_minutes=1)
    assert await _read(sessionmaker, ids) is None


@pytest.mark.asyncio
async def test_no_executions_reads_none(_migrated: None, sessionmaker: async_sessionmaker) -> None:
    ids = await _seed_task(sessionmaker)
    assert await _read(sessionmaker, ids) is None


@pytest.mark.asyncio
async def test_output_tail_is_capped(
    _migrated: None, sessionmaker: async_sessionmaker, migrations_pg_dsn: str
) -> None:
    ids = await _seed_task(sessionmaker)
    await _add_execution(
        sessionmaker, ids, dsn=migrations_pg_dsn, status="failed", output="x" * 9000
    )
    failure = await _read(sessionmaker, ids)
    assert failure is not None
    assert len(failure["output_tail"]) <= 1500
