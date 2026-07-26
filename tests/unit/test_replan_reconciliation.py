"""Replanificación en caliente — ADR 0132, opción A2 (`task_wf_45`).

`sync_to_kanban` era estrictamente aditivo (`if spec_id in existing: continue`).
Con eso, **editar o borrar una tarea del spec no llegaba nunca al tablero**: el
operador creía haber replanificado y el equipo seguía ejecutando el plan
anterior, sin un solo aviso. Un fallo silencioso, que es el peor tipo.

Las seis afirmaciones que el propio ADR dejó escritas como criterio de
verificación son las de este fichero.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.chat.sync_to_kanban import (
    PLAN_TASK_SPEC_ID_KEY,
    ReplanInFlightError,
    SyncResult,
    _reconcile_existing,
)
from api_server.db.domain import Task, TaskStatus

pytestmark = pytest.mark.unit


class _Session:
    """Sesión mínima: la reconciliación solo necesita poder hacer flush."""

    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


def _task(spec_id: str, status: str, *, title: str = "Original") -> Task:
    return Task(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        title=title,
        status=status,
        priority="medium",
        acceptance_criteria=["el criterio original"],
        inputs={PLAN_TASK_SPEC_ID_KEY: spec_id},
    )


def _spec(spec_id: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": spec_id,
        "title": "Original",
        "acceptance_criteria": ["el criterio original"],
    }
    base.update(over)
    return base


async def _run(
    rows: dict[str, Task], spec: dict[str, dict[str, Any]], *, scope: str = "all"
) -> SyncResult:
    result = SyncResult()
    await _reconcile_existing(
        _Session(),  # type: ignore[arg-type]
        result,
        selected_ids=list(spec),
        scope=scope,
        spec_by_id=spec,
        existing_rows=rows,
        role_agents=None,
    )
    return result


# ---------------------------------------------------------------------------
# (1) Editar una tarea NO empezada llega al tablero
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_editing_a_backlog_task_updates_the_board() -> None:
    row = _task("t1", TaskStatus.BACKLOG.value)
    result = await _run({"t1": row}, {"t1": _spec("t1", title="Título corregido")})
    assert result.updated_task_ids == {"t1": row.id}
    assert row.title == "Título corregido"


@pytest.mark.asyncio
async def test_the_acceptance_criteria_travel_too() -> None:
    # Son la definición de HECHO contra la que certifica el reviewer: corregir
    # el título y dejar los criterios viejos sería replanificar a medias.
    row = _task("t1", TaskStatus.READY.value)
    await _run({"t1": row}, {"t1": _spec("t1", acceptance_criteria=["otro criterio"])})
    assert row.acceptance_criteria == ["otro criterio"]


@pytest.mark.asyncio
async def test_a_task_the_spec_did_not_change_is_left_alone() -> None:
    row = _task("t1", TaskStatus.BACKLOG.value)
    result = await _run({"t1": row}, {"t1": _spec("t1")})
    assert result.updated_task_ids == {}


# ---------------------------------------------------------------------------
# (2) Borrar del spec una tarea NO empezada la cancela
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_task_dropped_from_the_spec_is_cancelled() -> None:
    row = _task("huerfana", TaskStatus.READY.value)
    result = await _run({"huerfana": row}, {})
    assert result.cancelled_task_ids == {"huerfana": row.id}
    assert row.status == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_a_partial_scope_never_cancels() -> None:
    # Con un scope parcial, «no está en la selección» NO significa «se ha
    # borrado del plan». Cancelar ahí sería destruir trabajo por una ambigüedad.
    row = _task("fuera-del-scope", TaskStatus.READY.value)
    result = await _run({"fuera-del-scope": row}, {}, scope="selection")
    assert result.cancelled_task_ids == {}
    assert row.status == TaskStatus.READY.value


# ---------------------------------------------------------------------------
# (3) Tocar una tarea EN VUELO se rechaza — y no se modifica NADA
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_editing_a_running_task_is_rejected() -> None:
    row = _task("t1", TaskStatus.IN_PROGRESS.value)
    with pytest.raises(ReplanInFlightError) as excinfo:
        await _run({"t1": row}, {"t1": _spec("t1", title="Otro")})
    assert str(row.id) in excinfo.value.task_ids


@pytest.mark.asyncio
async def test_a_task_in_review_is_in_flight_too() -> None:
    row = _task("t1", TaskStatus.IN_REVIEW.value)
    with pytest.raises(ReplanInFlightError):
        await _run({"t1": row}, {"t1": _spec("t1", title="Otro")})


@pytest.mark.asyncio
async def test_the_rejection_leaves_the_other_tasks_untouched() -> None:
    # O se aplica el replan ENTERO o no se aplica ninguno: un replan a medias
    # deja el tablero en un estado que no es ni el plan viejo ni el nuevo, y
    # nadie sabría cuál está mirando.
    editable = _task("t1", TaskStatus.BACKLOG.value)
    running = _task("t2", TaskStatus.IN_PROGRESS.value)
    with pytest.raises(ReplanInFlightError):
        await _run(
            {"t1": editable, "t2": running},
            {"t1": _spec("t1", title="Nuevo 1"), "t2": _spec("t2", title="Nuevo 2")},
        )
    assert editable.title == "Original"


@pytest.mark.asyncio
async def test_a_running_task_the_spec_did_not_change_does_not_block() -> None:
    # Solo bloquea lo que el replan TOCARÍA. Si no cambió, no hay conflicto:
    # bloquear por una tarea que corre y no se altera pararía cualquier replan
    # mientras el plan esté vivo, que es siempre.
    running = _task("t2", TaskStatus.IN_PROGRESS.value)
    editable = _task("t1", TaskStatus.BACKLOG.value)
    result = await _run(
        {"t1": editable, "t2": running},
        {"t1": _spec("t1", title="Nuevo"), "t2": _spec("t2")},
    )
    assert result.updated_task_ids == {"t1": editable.id}


# ---------------------------------------------------------------------------
# (4) Lo que ya se hizo es historia
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_done_task_is_frozen_and_reported() -> None:
    # No se reescribe: se avisa. Cambiar el criterio de una tarea ya certificada
    # invalidaría retroactivamente un veredicto que un reviewer emitió.
    row = _task("t1", TaskStatus.DONE.value)
    result = await _run({"t1": row}, {"t1": _spec("t1", title="Otro")})
    assert result.frozen_task_ids == {"t1": row.id}
    assert row.title == "Original"


@pytest.mark.asyncio
async def test_a_done_task_dropped_from_the_spec_stays_done() -> None:
    row = _task("t1", TaskStatus.DONE.value)
    result = await _run({"t1": row}, {})
    assert result.frozen_task_ids == {"t1": row.id}
    assert row.status == TaskStatus.DONE.value


# ---------------------------------------------------------------------------
# (6) Idempotencia
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_running_it_twice_changes_nothing_the_second_time() -> None:
    row = _task("t1", TaskStatus.BACKLOG.value)
    spec = {"t1": _spec("t1", title="Nuevo")}
    first = await _run({"t1": row}, spec)
    second = await _run({"t1": row}, spec)
    assert first.updated_task_ids and second.updated_task_ids == {}


# ---------------------------------------------------------------------------
# (5) Quitar una dependencia del spec la quita del tablero
# ---------------------------------------------------------------------------
class _DeleteRecordingSession(_Session):
    def __init__(self) -> None:
        super().__init__()
        self.deletes = 0

    async def execute(self, _stmt: Any) -> None:
        self.deletes += 1


@pytest.mark.asyncio
async def test_an_edge_the_spec_no_longer_declares_is_removed() -> None:
    # Las aristas solo se añadían: soltar una dependencia en el spec no la
    # soltaba en el tablero, y la tarea seguía esperando a algo de lo que el
    # plan ya no dice que dependa — sin forma de verlo desde la UI.
    from api_server.chat.sync_to_kanban import _prune_stale_edges

    t1, t2 = uuid4(), uuid4()
    session = _DeleteRecordingSession()
    removed = await _prune_stale_edges(
        session,  # type: ignore[arg-type]
        {"a": t1, "b": t2},
        {"a": {"id": "a", "depends_on": []}, "b": {"id": "b"}},  # ya NO depende de "a"
        {(t1, t2)},
    )
    assert removed == 1
    assert session.deletes == 1


@pytest.mark.asyncio
async def test_an_edge_the_spec_still_declares_survives() -> None:
    from api_server.chat.sync_to_kanban import _prune_stale_edges

    t1, t2 = uuid4(), uuid4()
    session = _DeleteRecordingSession()
    removed = await _prune_stale_edges(
        session,  # type: ignore[arg-type]
        {"a": t1, "b": t2},
        {"a": {"id": "a", "depends_on": ["b"]}, "b": {"id": "b"}},
        {(t1, t2)},
    )
    assert removed == 0
    assert session.deletes == 0
