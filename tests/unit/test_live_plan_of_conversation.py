"""Un plan BORRADO no retiene su conversación (auditoría adversarial 2026-07-25).

`task_wf_04` hizo «Generar Plan» idempotente: si la conversación ya generó un plan
y ese plan sigue VIVO, se devuelve el existente en vez de crear un gemelo. La
definición de «vivo» era `status not in {cancelled, rejected}` — y se olvidó del
borrado lógico.

`_load_plan` sí filtra `deleted_at` (`plans.py:461-463`) y `list_plans` también,
pero `_live_plan_of_conversation` usaba un `session.get` pelado. Y `delete_plan`
solo estampa `deleted_at`: no toca `plan.status` ni `conversation.related_plan_id`,
y no hay filtro global de soft-delete en el repositorio.

Resultado: borras el plan, vuelves al chat, pulsas «Generar Plan» y el endpoint
devuelve 200 con el plan borrado — cuyo `GET /plans/{id}` da 404. La conversación
queda incapaz de generar un plan nuevo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _plan(*, status: str = "draft", deleted_at: Any = None) -> Any:
    return SimpleNamespace(id="p1", status=status, deleted_at=deleted_at)


def test_a_soft_deleted_plan_is_not_live() -> None:
    """El hallazgo, en una línea."""
    from api_server.routers.plans import plan_is_live

    assert plan_is_live(_plan(deleted_at=datetime.now(UTC))) is False


def test_a_draft_plan_is_live() -> None:
    """Y la idempotencia que `task_wf_04` vino a dar sigue intacta: un plan vivo
    retiene su conversación para que el segundo clic no cree un gemelo."""
    from api_server.routers.plans import plan_is_live

    assert plan_is_live(_plan()) is True


@pytest.mark.parametrize("status", ["cancelled", "rejected"])
def test_a_superseded_plan_is_not_live(status: str) -> None:
    """Lo que ya decidía `task_wf_04`: sobre un plan cancelado o rechazado,
    generar otro es el comportamiento correcto, no un duplicado."""
    from api_server.routers.plans import plan_is_live

    assert plan_is_live(_plan(status=status)) is False


def test_a_deleted_plan_is_not_live_even_if_its_status_looks_alive() -> None:
    """`delete_plan` NO toca el status: solo estampa `deleted_at`. Mirar solo el
    status es exactamente por lo que el bug existía."""
    from api_server.routers.plans import plan_is_live

    assert plan_is_live(_plan(status="in_progress", deleted_at=datetime.now(UTC))) is False


def test_no_plan_is_not_live() -> None:
    from api_server.routers.plans import plan_is_live

    assert plan_is_live(None) is False
