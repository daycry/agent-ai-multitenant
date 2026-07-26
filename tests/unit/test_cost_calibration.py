"""Estimaciones de tokens calibradas con el histórico real (`task_wf_33`).

El sistema guardaba tokens reales en `executions` desde hace tiempo y la
estimación seguía usando un mapa estático cuyos propios comentarios decían que
«NO son empíricos para este proyecto». Se medía todo y no se aprendía nada.

La decisión que estos tests fijan, y que es la razón por la que la tarea estaba
bloqueada: **solo se calibran los TOKENS**. Las horas humanas son
horas-PERSONA en EUR y lo único que hay en el histórico es wall-clock de
MÁQUINA — calibrar una con la otra daría un número que parece medido y no lo es.
"""

from __future__ import annotations

import pytest
from api_server.chat.cost import DEFAULT_COMPLEXITY_ESTIMATES
from api_server.chat.cost_calibration import (
    MIN_SAMPLES_PER_LEVEL,
    CalibrationResult,
    _estimate_from_samples,
)

pytestmark = pytest.mark.unit


def test_the_estimate_is_the_MEDIAN_not_the_mean() -> None:
    # Un run que se fue a 300k tokens por un bucle no puede arrastrar la
    # estimación de todas las tareas «m» del proyecto.
    samples = [(1000, 100), (1100, 110), (1200, 120), (1300, 130), (300_000, 30_000)]
    est = _estimate_from_samples("m", samples)
    assert est.base_input_tokens == 1200
    assert est.base_output_tokens == 120


def test_a_handful_of_samples_is_not_enough_to_call_it_calibrated() -> None:
    # Con dos muestras la mediana es el punto medio de dos números
    # cualesquiera: presentarlo como «calibrado» vende una precisión que no hay.
    assert MIN_SAMPLES_PER_LEVEL >= 5


def test_levels_are_calibrated_INDEPENDENTLY() -> None:
    # Un proyecto puede tener histórico de sobra en tareas «m» y ninguna «xl».
    # Cada nivel usa el mejor dato que hay, y `sources` lo dice.
    result = CalibrationResult(
        estimates={**DEFAULT_COMPLEXITY_ESTIMATES},
        sources={"xs": "default", "s": "default", "m": "project", "l": "tenant", "xl": "default"},
    )
    assert result.calibrated is True
    context = result.as_context()
    assert context["sources"]["m"] == "project"
    assert context["sources"]["xl"] == "default"


def test_with_no_history_at_all_it_says_it_is_not_calibrated() -> None:
    # La UI tiene que poder distinguir una estimación medida de un placeholder;
    # presentarlas igual es cómo un número inventado acaba pareciendo un dato.
    result = CalibrationResult(
        estimates={**DEFAULT_COMPLEXITY_ESTIMATES},
        sources=dict.fromkeys(DEFAULT_COMPLEXITY_ESTIMATES, "default"),
    )
    assert result.calibrated is False


def test_the_context_payload_carries_the_numbers_and_their_origin() -> None:
    # `pm_plan_draft` corre en un hilo SIN sesión de BD: el mapa se calcula
    # fuera y se le inyecta ya hecho, no puede ir a buscarlo.
    result = CalibrationResult(
        estimates={**DEFAULT_COMPLEXITY_ESTIMATES},
        sources=dict.fromkeys(DEFAULT_COMPLEXITY_ESTIMATES, "project"),
    )
    context = result.as_context()
    assert context["tokens"]["m"]["input"] == DEFAULT_COMPLEXITY_ESTIMATES["m"].base_input_tokens
    assert set(context) == {"calibrated", "sources", "tokens"}


def test_only_completed_runs_are_sampled() -> None:
    # Un run abortado a la mitad mide cuánto se gastó antes de romperse, no
    # cuánto cuesta hacer la tarea: sesgaría la estimación hacia abajo
    # justamente en las tareas que más fallan.
    import inspect

    from api_server.chat.cost_calibration import _samples_by_complexity

    source = inspect.getsource(_samples_by_complexity)
    assert 'Execution.status == "done"' in source


def test_human_hours_are_never_calibrated() -> None:
    """LA decisión de esta tarea, en forma de test.

    Las horas humanas son horas-PERSONA en EUR; el histórico es wall-clock de
    MÁQUINA. Calibrar una con la otra repetiría la mezcla de magnitudes que ya
    se rechazó en el coste del plan y daría un número que PARECE medido.

    Si algún día alguien mete las horas en el calibrador, esto lo para.
    """
    import inspect

    from api_server.chat import cost_calibration

    source = inspect.getsource(cost_calibration)
    # Nada de horas humanas ni del cálculo de coste humano en este módulo.
    assert "compute_human_cost" not in source
    assert "estimated_hours" not in source
    assert "hourly_rate" not in source


def test_the_calibrator_only_produces_token_estimates() -> None:
    # El tipo lo dice: solo tokens de entrada y salida. No hay ningún hueco por
    # el que colar una hora.
    from api_server.chat.cost import ComplexityTokenEstimate

    fields = set(ComplexityTokenEstimate.__dataclass_fields__)
    assert fields == {
        "complexity",
        "base_input_tokens",
        "base_output_tokens",
        "low_factor",
        "high_factor",
    }
