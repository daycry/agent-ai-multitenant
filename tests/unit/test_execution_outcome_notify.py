"""NOTIF-3: el worker emite execution_failed / execution_finished al terminar.

Ambos eventos estaban registrados (+plantillas ES/EN) pero NADIE los emitía —
un run que moría solo dejaba una línea de log. `_notify_execution_outcome`
mapea el estado terminal del run al evento correcto y es best-effort (un broker
caído jamás rompe el run ya terminado).
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.execution import _notify_execution_outcome

pytestmark = pytest.mark.unit


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _capture(event: dict[str, Any], **_: Any) -> bool:
        events.append(event)
        return True

    from api_server import celery_client

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", _capture)
    return events


async def _notify(status: str, abort_code: str | None = None) -> None:
    await _notify_execution_outcome(
        tenant_id="t-1",
        task_id="task-1",
        task_title="Implement X",
        status=status,
        abort_code=abort_code,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "aborted"])
async def test_dead_run_emits_execution_failed(captured, status: str) -> None:
    await _notify(status, abort_code="loop_detected")
    assert len(captured) == 1
    assert captured[0]["event_type"] == "execution_failed"
    assert captured[0]["context"]["abort_code"] == "loop_detected"
    assert captured[0]["context"]["task_title"] == "Implement X"


@pytest.mark.asyncio
async def test_done_run_emits_execution_finished(captured) -> None:
    await _notify("done")
    assert [e["event_type"] for e in captured] == ["execution_finished"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["needs_human_review", "cancelled", "awaiting_human_approval"])
async def test_other_terminals_have_their_own_rails(captured, status: str) -> None:
    await _notify(status)
    assert captured == []


@pytest.mark.asyncio
async def test_broker_failure_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(event: dict[str, Any], **_: Any) -> bool:
        raise RuntimeError("broker caído")

    from api_server import celery_client

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", _boom)
    await _notify("failed")  # no debe propagar


# --------------------------------------------------------------- task_cv_41
# Auditoría 2026-09-01 (C-05): las tres escalaciones del bucle de review con
# reviewer IA —commit perdido, tercer rechazo, run muerto— dejaban la tarea en
# `blocked` sin emitir nada; `task_blocked` estaba registrado (plantillas ES/EN)
# pero sólo lo emitían los raíles humanos. El notificador de salida del run
# recibe ahora el estado final de la TAREA y, si quedó bloqueada, lo emite.


@pytest.mark.asyncio
async def test_a_run_that_blocks_its_task_emits_task_blocked(captured) -> None:
    await _notify_execution_outcome(
        tenant_id="t-1",
        task_id="task-1",
        task_title="Implement X",
        status="failed",
        abort_code="commit_failed",
        task_status="blocked",
    )
    types = [e["event_type"] for e in captured]
    assert types == ["execution_failed", "task_blocked"], types
    blocked = captured[-1]
    assert blocked["tenant_id"] == "t-1"
    assert blocked["context"]["task_title"] == "Implement X"
    assert blocked["context"]["task_id"] == "task-1"
    assert blocked["context"]["reason"] == "commit_failed"


@pytest.mark.asyncio
async def test_a_done_run_whose_verdict_blocks_the_task_emits_task_blocked(captured) -> None:
    """El reviewer termina `done` y su veredicto (tercer rechazo) bloquea la tarea."""
    await _notify_execution_outcome(
        tenant_id="t-1",
        task_id="task-1",
        task_title="Implement X",
        status="done",
        abort_code=None,
        task_status="blocked",
    )
    assert [e["event_type"] for e in captured] == ["execution_finished", "task_blocked"]
    assert captured[-1]["context"]["reason"] == "escalated"


@pytest.mark.asyncio
async def test_a_task_that_does_not_block_emits_no_task_blocked(captured) -> None:
    await _notify_execution_outcome(
        tenant_id="t-1",
        task_id="task-1",
        task_title="Implement X",
        status="done",
        abort_code=None,
        task_status="in_review",
    )
    assert [e["event_type"] for e in captured] == ["execution_finished"]
