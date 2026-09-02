"""El orchestrator hila qué entregaron las dependencias (`task_wf_70`).

La mitad productora del brief: leer, de las dependencias DIRECTAS ya
completadas, el resumen que su propio agente entregó en `submit_result`.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _Row:
    def __init__(self, id_: str, title: str) -> None:
        self.id = id_
        self.title = title


class _Result:
    """Resultado de `session.execute` con lo que toque según la consulta."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def scalars(self) -> Any:
        return self._payload

    def all(self) -> Any:
        return self._payload

    def scalar_one_or_none(self) -> Any:
        return self._payload


class _Session:
    """Devuelve, en orden, lo que cada consulta del método necesita."""

    def __init__(self, *payloads: Any) -> None:
        self._payloads = list(payloads)
        self.queries = 0

    async def execute(self, _stmt: Any) -> _Result:
        self.queries += 1
        return _Result(self._payloads.pop(0))


class _Task:
    id = "task-3"
    tenant_id = "t1"


def _dispatcher() -> Any:
    from orchestrator.dispatch import TaskDispatcher

    return TaskDispatcher.__new__(TaskDispatcher)


@pytest.mark.asyncio
async def test_each_completed_dependency_contributes_its_summary() -> None:
    session = _Session(
        ["dep-1", "dep-2"],  # depends_on
        [_Row("dep-1", "Definir el esquema"), _Row("dep-2", "Cliente HTTP")],
        "Creé migrations/001.sql",  # output de dep-1
        "Añadí OrdersClient.fetch",  # output de dep-2
    )
    briefs = await _dispatcher()._read_predecessor_briefs(session, _Task())
    assert briefs == [
        {"title": "Definir el esquema", "summary": "Creé migrations/001.sql"},
        {"title": "Cliente HTTP", "summary": "Añadí OrdersClient.fetch"},
    ]


@pytest.mark.asyncio
async def test_a_task_without_dependencies_costs_one_query_and_returns_nothing() -> None:
    # El caso mayoritario: no puede pagar consultas de más ni emitir la clave
    # (sin clave = comportamiento de siempre).
    session = _Session([])
    assert await _dispatcher()._read_predecessor_briefs(session, _Task()) == []
    assert session.queries == 1


@pytest.mark.asyncio
async def test_a_dependency_with_no_output_is_dropped() -> None:
    # «Hizo algo» no es algo sobre lo que construir: el hueco solo ocuparía
    # sitio en el prompt.
    session = _Session(["dep-1"], [_Row("dep-1", "Sin salida")], "   ")
    assert await _dispatcher()._read_predecessor_briefs(session, _Task()) == []


@pytest.mark.asyncio
async def test_a_long_summary_is_capped_before_it_reaches_the_prompt() -> None:
    from orchestrator.dispatch import TaskDispatcher

    session = _Session(["dep-1"], [_Row("dep-1", "T")], "x" * 9000)
    briefs = await _dispatcher()._read_predecessor_briefs(session, _Task())
    assert len(briefs[0]["summary"]) == TaskDispatcher._PREDECESSOR_SUMMARY_MAX


# ---------------------------------------------------------------------------
# Auditoría 2026-09-01 (C-03): lo que se lee de `executions` como «lo que
# entregó el implementador» NO puede ser el veredicto del reviewer, que vive en
# la misma tabla con el mismo `task_id`. El filtro existía sólo para
# `<commands-run>`; aquí se fija para las otras tres lecturas.
# ---------------------------------------------------------------------------


class _ResultConFirst(_Result):
    def first(self) -> Any:
        return self._payload


class _RecordingSession(_Session):
    """Como `_Session`, pero guarda cada statement para inspeccionar su WHERE."""

    def __init__(self, *payloads: Any) -> None:
        super().__init__(*payloads)
        self.statements: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.statements.append(stmt)
        self.queries += 1
        return _ResultConFirst(self._payloads.pop(0))


class _RowConReviewer(_Row):
    def __init__(self, id_: str, title: str, reviewer_agent_id: str | None) -> None:
        super().__init__(id_, title)
        self.reviewer_agent_id = reviewer_agent_id


def _excludes_reviewer(stmt: Any) -> bool:
    sql = str(stmt)
    return "executions.agent_id" in sql and "IS NULL" in sql


class _TaskWithReviewer(_Task):
    reviewer_agent_id = "reviewer-1"


@pytest.mark.asyncio
async def test_a_predecessor_brief_never_comes_from_the_reviewers_run() -> None:
    session = _RecordingSession(
        ["task-1"],  # ids de las dependencias directas
        [_RowConReviewer("task-1", "Parser", "reviewer-9")],  # la dependencia y SU reviewer
        "lo que entregó el implementador",
    )

    briefs = await _dispatcher()._read_predecessor_briefs(session, _TaskWithReviewer())

    assert briefs and briefs[0]["summary"] == "lo que entregó el implementador"
    assert _excludes_reviewer(session.statements[2]), (
        "la lectura de la salida de la dependencia no excluye al reviewer: la última "
        "`done` de una tarea con reviewer IA es el VEREDICTO, no el entregable"
    )


@pytest.mark.asyncio
async def test_the_prior_failure_is_the_implementers_not_the_reviewers() -> None:
    session = _RecordingSession(None)

    await _dispatcher()._read_prior_failure(session, _TaskWithReviewer())

    assert _excludes_reviewer(session.statements[0])


def test_the_implementer_outputs_query_excludes_the_reviewer() -> None:
    from orchestrator.dispatch import _implementer_outputs_query

    stmt = _implementer_outputs_query(_TaskWithReviewer(), "reviewer-1")

    assert _excludes_reviewer(stmt), (
        "`implementer_output` se lee sin excluir al reviewer: recibe su propio "
        "veredicto anterior como «lo que entregó el implementador»"
    )
