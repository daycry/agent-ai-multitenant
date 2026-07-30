"""AUD16-02 (auditoría 2026-07-16): fin del éxito falso de las tools de
orquestación.

Las cuatro tools emitían un *effect* a un ``OrchestrationSink`` «que el worker
drena» — pero el drain worker-side nunca aterrizó (quedó para la «Fase E» del
plan 02): el efecto moría dentro del contenedor y el agente recibía ``ok=true``
por un comentario/movimiento de Kanban que jamás ocurrió (peor que el silencio
del hallazgo H3 original de 2026-06-24).

Contrato nuevo:

  * ``task_comment`` — la única con consumidor real (el rail PlanComment→prompt
    ya existe) — sigue emitiendo su efecto y el worker lo APLICA al terminar el
    run (drain post-run sobre steps_log).
  * ``kanban_update`` / ``agent_invoke`` / ``notify_user`` — sin consumidor —
    devuelven un error HONESTO (ok=False, "not wired") y NO emiten efecto,
    hasta que exista su drain.
"""

from __future__ import annotations

from agent_runtime.orchestration_tools import OrchestrationSink, OrchestrationTools


def _tools() -> tuple[OrchestrationSink, OrchestrationTools]:
    sink = OrchestrationSink()
    return sink, OrchestrationTools(sink)


def test_task_comment_still_emits_its_effect() -> None:
    sink, tools = _tools()
    result = tools.task_comment({"task_id": "t-1", "body": "ojo: falta el índice X"})
    assert result.ok is True
    assert sink.effects == [
        {"effect": "task_comment", "task_id": "t-1", "body": "ojo: falta el índice X"}
    ]


def test_kanban_update_fails_honestly_and_emits_nothing() -> None:
    sink, tools = _tools()
    result = tools.kanban_update({"task_id": "t-1", "status": "done"})
    assert result.ok is False
    assert "not wired" in (result.error or "").lower()
    assert sink.effects == []


def test_agent_invoke_fails_honestly_and_emits_nothing() -> None:
    sink, tools = _tools()
    result = tools.agent_invoke({"agent_id": "a-1", "prompt": "haz X"})
    assert result.ok is False
    assert "not wired" in (result.error or "").lower()
    assert sink.effects == []


def test_notify_user_fails_honestly_and_emits_nothing() -> None:
    sink, tools = _tools()
    result = tools.notify_user({"user_id": "u-1", "message": "hola"})
    assert result.ok is False
    assert "not wired" in (result.error or "").lower()
    assert sink.effects == []


def test_task_comment_still_validates_its_arguments() -> None:
    sink, tools = _tools()
    assert tools.task_comment({"task_id": "", "body": "x"}).ok is False
    assert tools.task_comment({"task_id": "t-1", "body": " "}).ok is False
    assert sink.effects == []
