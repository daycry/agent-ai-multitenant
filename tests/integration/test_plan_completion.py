"""Integration tests: plan → completed (Plan 06 task_06_37, revisado task_wf_36).

**Había dos definiciones del estado terminal y se contradecían** (D-07):

* El camino REAL (`routers/review.py`) pasa el plan a `completed` en cuanto el
  humano da el veredicto `approved`, y **después** encola el auto-PR (ADR 0072
  fase 2). O sea: cuando un plan está `completed`, su PR puede ni existir.
* `transition_to_completed` exigía además `pr_merged=True`. Su único llamador
  era el `plan_runner` de demo, que no está cableado — así que la regla escrita
  no gobernaba nada y decía lo contrario que el código que sí corre.

Resolución (recomendación de la auditoría): **`completed` significa «validado por
el humano»**, que es lo que hace el sistema, y el estado del PR se refleja aparte
— la cabecera de `task_wf_30` ya lo muestra, incluido el motivo si falló. Exigir
el merge habría necesitado un webhook de merge que no existe, y habría dejado
todos los planes de hoy colgados de un evento que nunca llega.

`CLAUDE.md` no hacía falta corregirlo: su principio 5 ya dice «al completar el
plan se abre un PR automático» — completar primero, PR después, justo el orden
real. (Los criterios de cierre con «PR mergeado» de su protocolo de roadmap son
otra cosa: gobiernan las FASES de desarrollo de esta plataforma, no la máquina de
estados del producto.)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_human_approval_completes_the_plan() -> None:
    """La definición elegida, en una línea."""
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed("pending_human_validation", human_verdict="approved")
    assert result.transitioned is True
    assert result.new_status == "completed"


def test_completion_does_not_wait_for_a_merged_pr() -> None:
    """Regresión de D-07: el PR se abre DESPUÉS de completar, así que exigir el
    merge dejaría cada plan esperando un evento que nadie emite."""
    from api_server.plan_progress import transition_to_completed

    assert (
        transition_to_completed("pending_human_validation", human_verdict="approved").transitioned
        is True
    )


def test_not_yet_approved_blocks_completion() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed("pending_human_validation", human_verdict=None)
    assert result.transitioned is False
    assert "verdict" in (result.reason or "")


def test_rejected_blocks_completion() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed("pending_human_validation", human_verdict="rejected")
    assert result.transitioned is False


def test_wrong_starting_state_blocks() -> None:
    """El gate humano sigue siendo el único camino: solo se completa desde
    `pending_human_validation`."""
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed("in_progress", human_verdict="approved")
    assert result.transitioned is False


def test_the_real_path_and_the_rule_now_agree() -> None:
    """La guarda contra que la contradicción vuelva: el endpoint de veredicto
    hace exactamente lo que la regla dice, sin consultar el PR."""
    import inspect

    from api_server.routers import review

    source = inspect.getsource(review)
    assert "pr_merged" not in source, (
        "el camino real ha empezado a mirar el merge del PR: o se cambia la regla "
        "en transition_to_completed, o vuelven a existir dos definiciones"
    )
