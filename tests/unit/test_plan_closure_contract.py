"""El contrato reconciler ↔ dispatch sobre el cierre de un plan (`task_wf_58`).

Al terminar una tarea hay que decidir lo mismo desde dos servicios: el
orchestrator cuando consume el evento `task.done`, y el reconciler como red de
seguridad cuando ese evento se pierde (Redis parpadea, un worker muere entre el
commit y el publish). Que las dos vías decidan IGUAL era, hasta ahora, una
promesa escrita en un comentario — y una promesa en un comentario se rompe sin
que nada falle.

Lo que este fichero fija son dos cosas distintas:

  1. **Que solo hay una decisión** (`decide_plan_closure`), y que ninguno de los
     dos módulos reconstruye la secuencia por su cuenta. Es un guard estático:
     si alguien vuelve a escribir la cadena a mano, falla.
  2. **Qué decide**, sobre una tabla de snapshots — incluidos los tres casos que
     costaron auditorías: el plan que no puede cerrarse ni atascarse, el que
     tiene una tarea bloqueada pero otra que aún avanza, y el backlog atascado
     detrás de una dependencia muerta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from api_server.plan_progress import TaskSnapshot, decide_plan_closure

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_DISPATCH = _REPO / "apps" / "orchestrator" / "src" / "orchestrator" / "dispatch.py"
_RECONCILER = _REPO / "apps" / "workers" / "src" / "workers" / "maintenance" / "reconciler.py"


def _snap(task_id: str, status: str, *deps: str) -> TaskSnapshot:
    return TaskSnapshot(id=task_id, status=status, depends_on=tuple(deps))


# ---------------------------------------------------------------------------
# 1. Una sola decisión, no dos copias
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [_DISPATCH, _RECONCILER], ids=["dispatch", "reconciler"])
def test_both_services_ask_the_same_function(path: Path) -> None:
    assert "decide_plan_closure(" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", [_DISPATCH, _RECONCILER], ids=["dispatch", "reconciler"])
def test_neither_service_rebuilds_the_sequence_by_hand(path: Path) -> None:
    """La secuencia «¿validación humana? si no, ¿bloqueado?» vive en UN sitio.

    Llamar a las piezas sueltas desde un servicio es cómo empezó la divergencia
    que esta tarea cierra: dos copias que envejecen por separado.
    """
    text = path.read_text(encoding="utf-8")
    stray = [
        name
        for name in ("transition_to_pending_human_validation", "transition_to_blocked")
        # El nombre puede aparecer en un comentario o docstring explicando la
        # decisión; lo que no puede es INVOCARSE.
        if re.search(rf"(?<!def ){name}\s*\(", text)
    ]
    assert stray == [], (
        f"{path.name} vuelve a componer la decisión de cierre a mano ({stray}). "
        "Usa `decide_plan_closure`, que es la que comparten las dos vías."
    )


# ---------------------------------------------------------------------------
# 2. Qué decide, sobre el mismo fixture para ambas vías
# ---------------------------------------------------------------------------
def test_all_tasks_done_closes_the_plan() -> None:
    result = decide_plan_closure("in_progress", [_snap("a", "done"), _snap("b", "done")])
    assert result.transitioned is True
    assert result.new_status == "pending_human_validation"


def test_a_cancelled_task_does_not_hold_the_plan_open() -> None:
    result = decide_plan_closure("in_progress", [_snap("a", "done"), _snap("b", "cancelled")])
    assert result.new_status == "pending_human_validation"


def test_closing_wins_over_blocking_when_everything_is_terminal() -> None:
    # El orden importa: un plan cuyas tareas están TODAS resueltas va a
    # validación aunque alguna estuviera bloqueada en un snapshot anterior.
    result = decide_plan_closure("in_progress", [_snap("a", "done"), _snap("b", "cancelled")])
    assert result.new_status == "pending_human_validation"


def test_a_running_task_keeps_the_plan_in_progress() -> None:
    result = decide_plan_closure("in_progress", [_snap("a", "done"), _snap("b", "in_progress")])
    assert result.transitioned is False


def test_a_plan_whose_only_open_task_is_blocked_escalates() -> None:
    # c3 (auditoría 2026-07-03): `blocked` cuenta como abierta, así que el plan
    # nunca llegaría a validación y se quedaría `in_progress` PARA SIEMPRE sin
    # ninguna señal al operador. Este es el caso que motivó el escalado.
    result = decide_plan_closure("in_progress", [_snap("a", "done"), _snap("b", "blocked")])
    assert result.transitioned is True
    assert result.new_status == "blocked"


def test_a_blocked_task_does_not_escalate_while_another_can_advance() -> None:
    # Con trabajo que aún puede progresar, bloquear el plan entero pararía a un
    # equipo que todavía tiene por dónde tirar.
    result = decide_plan_closure(
        "in_progress", [_snap("a", "blocked"), _snap("b", "ready"), _snap("c", "done")]
    )
    assert result.transitioned is False


def test_a_backlog_stuck_behind_a_blocked_dependency_escalates() -> None:
    # prod-06 A1: `c` está en backlog y parece que puede avanzar, pero depende
    # de `a`, que está bloqueada. Sin mirar el DAG, el plan se quedaba colgado.
    result = decide_plan_closure("in_progress", [_snap("a", "blocked"), _snap("c", "backlog", "a")])
    assert result.transitioned is True
    assert result.new_status == "blocked"


def test_a_backlog_stuck_behind_a_cancelled_dependency_escalates() -> None:
    result = decide_plan_closure(
        "in_progress", [_snap("a", "blocked"), _snap("b", "cancelled"), _snap("c", "backlog", "b")]
    )
    assert result.new_status == "blocked"


@pytest.mark.parametrize(
    "status", ["draft", "pending_approval", "approved", "blocked", "completed", "cancelled"]
)
def test_only_a_plan_in_progress_is_ever_closed_or_escalated(status: str) -> None:
    # La guarda atómica del UPDATE también lo exige (`WHERE status='in_progress'`),
    # pero la decisión no puede depender de que el SQL la salve.
    result = decide_plan_closure(status, [_snap("a", "done")])  # type: ignore[arg-type]
    assert result.transitioned is False


def test_a_plan_with_no_tasks_at_all_is_left_alone() -> None:
    # Un snapshot vacío satisface «todas las tareas hechas» POR VACUIDAD, así
    # que la pieza suelta cerraría un plan en el que no se hizo nada. El
    # reconciler ya lo salta antes de preguntar, pero la decisión compartida no
    # puede depender de que su llamante se acuerde.
    result = decide_plan_closure("in_progress", [])
    assert result.transitioned is False
    assert "no materialised tasks" in (result.reason or "")
