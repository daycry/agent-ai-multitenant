"""prod-06 task_prod06_dag_03 — el veredicto del reviewer, por el camino de VERDAD.

Por qué existe este fichero habiendo ya dos tests del mismo mecanismo
--------------------------------------------------------------------
``test_reviewer_bridge_wiring.py`` prueba ``apply_reviewer_verdict`` llamándola a
mano, y ``test_review_execution_applies_verdict.py`` prueba
``_apply_review_verdict`` llamándola a mano. Los dos pasarían íntegros con el
mecanismo **desconectado del despacho** — que es exactamente el defecto que la
casilla nombra (*«no tiene ningún caller productivo»*) y el modo de fallo nº 1 de
esta base: mecanismo entregado, cero llamantes.

Este entra por ``conduct_execution``, el mismo punto de entrada que usa el worker
en producción, con ``review=True``. Nadie llama aquí a ``apply_reviewer_verdict``:
si el despacho dejase de invocarla, la tarea se quedaría en ``in_review`` y el
test se pondría rojo sin que ninguna aserción mencione a la función.

Sin Docker a propósito: el runner se sustituye por un doble que emite la línea
``execution.finished`` que emitiría el contenedor del reviewer. Lo que se prueba
es el cableado del worker, no que Docker sepa arrancar un contenedor — eso ya lo
prueba `test_worker_launches_container.py`, y atar este test al build de
`agent-runtime:v1` lo convertiría en un test que se salta solo.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Project, Task, TaskStatus
from api_server.db.models import Organization
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.config import Settings
from workers.container import ContainerResult
from workers.execution import ExecutionRequest, conduct_execution

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


class _ScriptedReviewerRunner:
    """Doble del `AgentContainerRunner`: emite lo que emitiría el reviewer.

    Guarda el `ContainerSpec` recibido para que el test pueda comprobar que el
    despacho llegó de verdad hasta el lanzamiento (y no se saltó la fase por un
    fail-fast silencioso, que dejaría el resto del test probando nada)."""

    def __init__(self, output: str) -> None:
        self._output = output
        self.launched: list[Any] = []

    def run_streamed(self, spec: Any, on_line: Any, *, timeout: Any = None) -> ContainerResult:
        self.launched.append(spec)
        on_line(
            json.dumps(
                {
                    "event": "execution.finished",
                    "result": {
                        "status": "done",
                        "output": self._output,
                        "iterations": 1,
                        "steps": [],
                        "usage": {},
                        "finish_status": "success",
                    },
                }
            )
        )
        return ContainerResult(
            container_id="fake-reviewer",
            exit_code=0,
            logs="",
            timed_out=False,
            host_config={},
            config_env=(),
            networks=(),
        )

    def kill_by_label(self, execution_id: str) -> int:  # pragma: no cover - no se cancela
        return 0


async def _seed(sm: async_sessionmaker, *, retry_count: int = 0) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "task": uuid4()}
    async with sm() as s, s.begin():
        await s.execute(
            text(
                "TRUNCATE task_audit_events, approval_requests, executions,"
                " task_dependencies, tasks, plans, agents, projects, organizations"
                " RESTART IDENTITY CASCADE"
            )
        )
        slug = f"rev-{ids['tenant'].hex[:8]}"
        s.add(Organization(id=ids["tenant"], name="Rev tenant", slug=slug))
        await s.flush()
        s.add(
            Project(
                id=ids["project"],
                tenant_id=ids["tenant"],
                name="Rev project",
                status="active",
                is_template=False,
            )
        )
        await s.flush()
        s.add(
            Task(
                id=ids["task"],
                tenant_id=ids["tenant"],
                project_id=ids["project"],
                title="Reviewed task",
                status=TaskStatus.IN_REVIEW.value,
                priority="medium",
                retry_count=retry_count,
                max_retries=3,
            )
        )
    return ids


def _review_request(ids: dict[str, UUID]) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(ids["tenant"]),
        task_id=str(ids["task"]),
        agent_id=None,
        task={"id": str(ids["task"]), "title": "Reviewed task", "description": ""},
        model={"kind": "scripted", "decisions": [{"kind": "finish", "output": "reviewed"}]},
        review=True,
    )


async def _run_review(
    sm: async_sessionmaker, ids: dict[str, UUID], output: str, redis_url: str
) -> _ScriptedReviewerRunner:
    runner = _ScriptedReviewerRunner(output)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await conduct_execution(
            _review_request(ids),
            settings=Settings(),
            sessionmaker=sm,
            redis=redis,
            runner=runner,  # type: ignore[arg-type]
        )
    finally:
        await redis.aclose()
    return runner


@pytest.mark.asyncio
async def test_an_approve_verdict_closes_the_task_through_the_real_dispatch(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        runner = await _run_review(
            sm, ids, "Todo correcto.\n<verdict>approve</verdict>", test_redis_url
        )

        assert runner.launched, (
            "el despacho no llegó a lanzar el contenedor de review: el resto de"
            " las aserciones no probarían el cableado"
        )
        async with sm() as s:
            task = await s.get(Task, ids["task"])
            assert task is not None
            assert task.status == TaskStatus.DONE.value, (
                "la tarea sigue en `in_review`: el flujo post-ejecución NO invoca"
                " apply_reviewer_verdict (el bucle del ADR 0027 está roto)"
            )
            assert task.completed_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_reject_verdict_sends_the_task_back_with_the_retry_counted(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    """El otro brazo del bucle. Sin él, un `apply_reviewer_verdict` que hiciera
    `status = done` incondicionalmente pasaría el test de arriba."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm)

        await _run_review(
            sm,
            ids,
            "Falta el caso límite.\n<verdict>reject</verdict>\n"
            "<failed_criterion>sin test del caso vacío</failed_criterion>",
            test_redis_url,
        )

        async with sm() as s:
            task = await s.get(Task, ids["task"])
            assert task is not None
            assert task.status == TaskStatus.BACKLOG.value
            assert task.retry_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_reject_at_the_retry_cap_escalates_to_a_human(
    _migrated: None, admin_database_url: str, test_redis_url: str
) -> None:
    """El techo del bucle: sin él, un reviewer severo re-implementa para siempre."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        ids = await _seed(sm, retry_count=2)  # max_retries = 3

        await _run_review(sm, ids, "Sigue mal.\n<verdict>reject</verdict>", test_redis_url)

        async with sm() as s:
            task = await s.get(Task, ids["task"])
            assert task is not None
            assert task.status == TaskStatus.BLOCKED.value
            assert task.retry_count == 3
    finally:
        await engine.dispose()
