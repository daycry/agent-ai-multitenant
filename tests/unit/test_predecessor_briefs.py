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
