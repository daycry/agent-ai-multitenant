"""Un replan no puede convertir en prosa el criterio ejecutable que escribió un humano.

El defecto, en una línea: `_apply_spec_to_task` hacía
`task.acceptance_criteria = acceptance` y con eso un replan pisaba los criterios
de la tarea con los del spec del plan.

Por qué eso es destructivo y no una simple sobrescritura: el spec **no puede**
llevar un criterio ejecutable. El planner tiene prohibido por prompt emitirlo y su
normalizador está tipado `-> list[str]`, así que lo que baja del spec es siempre
prosa. Encadenando: el operador añade `{runtime: php-phpunit, command: ...}` desde
la ficha de tarea, alguien replanifica, y el único dato que hacía que esa tarea se
verificase de verdad desaparece **sin un aviso**.

La regla que fija este fichero, y que vale más allá del caso: una
resincronización no puede destruir información que el spec es estructuralmente
incapaz de transportar.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _TareaFalsa:
    """Lo justo que `_apply_spec_to_task` toca de una Task."""

    def __init__(self, criterios: Any) -> None:
        self.title = "titulo previo"
        self.description = "descripcion previa"
        self.acceptance_criteria = criterios
        self.estimated_complexity: str | None = None
        self.assigned_agent_id: Any = None
        self.reviewer_agent_id: Any = None


def _merge(actuales: Any, del_spec: list[Any]) -> list[Any]:
    """Ejercita el CAMINO REAL del replan, no la función auxiliar.

    Este helper llamaba antes directamente a `_merge_acceptance`, y por eso los
    siete tests seguían en verde con la línea de producción revertida: medían la
    función nueva, no que alguien la usara. Anclado en `_apply_spec_to_task`, que
    es el único sitio desde el que un replan escribe sobre la tarea.
    """
    from typing import cast

    from api_server.chat.sync_to_kanban import _apply_spec_to_task
    from api_server.db.domain.plans_tasks import Task

    tarea = _TareaFalsa(actuales)
    _apply_spec_to_task(cast(Task, tarea), {"acceptance_criteria": del_spec}, None)
    return list(tarea.acceptance_criteria)


_EJECUTABLE: dict[str, Any] = {
    "description": "los tests de Home pasan",
    "runtime": "php-phpunit",
    "command": "vendor/bin/phpunit --filter HomeTest",
    "check_type": "automated",
}


def test_a_replan_keeps_the_executable_criterion() -> None:
    """El caso que motivó todo: el spec trae prosa donde había un comando."""
    salida = _merge([_EJECUTABLE], ["los tests de Home siguen pasando"])

    assert isinstance(salida[0], dict), (
        "el replan convirtió en cadena un criterio ejecutable: el comando se ha "
        "perdido y la tarea vuelve a no verificarse de verdad"
    )
    assert salida[0]["runtime"] == "php-phpunit"
    assert salida[0]["command"] == "vendor/bin/phpunit --filter HomeTest"


def test_the_replan_can_still_rewrite_the_prose() -> None:
    """Conservar la estructura no puede significar congelar el texto: reescribir la
    descripción es justo para lo que sirve un replan."""
    salida = _merge([_EJECUTABLE], ["los tests de Home siguen pasando"])

    assert salida[0]["description"] == "los tests de Home siguen pasando"


def test_prose_criteria_behave_exactly_as_before() -> None:
    """No-regresión, y es el test que más importa: la inmensa mayoría de las tareas
    tiene criterios de prosa y el replan tiene que seguir pisándolos sin más."""
    assert _merge(["viejo A", "viejo B"], ["nuevo A", "nuevo B"]) == ["nuevo A", "nuevo B"]
    assert _merge(None, ["nuevo"]) == ["nuevo"]
    assert _merge([], ["nuevo"]) == ["nuevo"]


def test_the_spec_can_add_and_remove_criteria() -> None:
    """El replan sigue mandando sobre CUÁNTOS criterios hay."""
    assert _merge([_EJECUTABLE], []) == []
    assert len(_merge([_EJECUTABLE], ["a", "b", "c"])) == 3


def test_a_structured_criterion_from_the_spec_wins() -> None:
    """Si el spec trae estructura (hoy no puede, mañana quizá), manda él: la guarda
    protege contra la PÉRDIDA de información, no contra su actualización."""
    nuevo = {"description": "otra cosa", "runtime": "node-jest", "command": "npm test"}
    assert _merge([_EJECUTABLE], [nuevo]) == [nuevo]


def test_a_half_structured_criterion_is_not_protected() -> None:
    """Sin `runtime` Y `command` no hay nada ejecutable que preservar — el worker
    exige los dos (`execution.py`), así que proteger a medias sería fijar un dato
    que no sirve para nada."""
    a_medias = {"description": "algo", "runtime": "php-phpunit"}
    assert _merge([a_medias], ["texto nuevo"]) == ["texto nuevo"]


def test_an_empty_prose_line_does_not_erase_the_description() -> None:
    """Un hueco del spec no debe vaciar lo que había: preferimos el texto anterior
    a una descripción en blanco colgando de un comando."""
    salida = _merge([_EJECUTABLE], ["   "])
    assert salida[0]["description"] == _EJECUTABLE["description"]
