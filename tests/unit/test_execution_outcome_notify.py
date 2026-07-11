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
