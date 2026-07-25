"""El estado de un plan, de un vistazo (task_wf_30 — D-01, D-02, D-04).

Tres cegueras del mismo sitio:

* **D-01**: `compute_plan_progress` lleva escrito y testeado desde el Plan 06 y
  su único consumidor es el `plan_runner` de demo, que no está cableado. No hay
  endpoint ni señal de avance en ningún tablero: el operador no sabe por dónde va
  un plan.
* **D-02**: `pr_url` / `pr_branch` / `pr_error` viajan en la respuesta del plan y
  tienen **cero** ocurrencias en el frontend. El operador aprueba un plan y no ve
  ni el PR ni, si falló, por qué.
* **D-04**: el coste ESTIMADO se calcula entero (`/cost-breakdown`, humano en EUR
  + IA en USD) y el REAL —lo que las ejecuciones gastaron de verdad— no se
  agrega en ninguna parte. Un presupuesto que nunca se contrasta no es un
  presupuesto.

Lo que se fija aquí son las funciones puras de la agregación. El endpoint
completo vive en `tests/integration/test_plan_status_endpoint.py`.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from api_server.plan_progress import TaskSnapshot, compute_plan_progress

pytestmark = pytest.mark.unit


def _execution(cost: str = "0", tokens: int = 0, status: str = "completed") -> Any:
    return SimpleNamespace(total_cost_usd=Decimal(cost), total_tokens=tokens, status=status)


# ---------------------------------------------------------------------------
# El gasto REAL
# ---------------------------------------------------------------------------
def test_actual_spend_sums_every_execution_of_the_plan() -> None:
    from api_server.routers.plans import aggregate_actual_spend

    spend = aggregate_actual_spend([_execution("1.50", 1000), _execution("2.25", 2000)])
    assert spend.cost_usd == Decimal("3.75")
    assert spend.tokens == 3000
    assert spend.runs == 2


def test_a_failed_run_still_counts_as_spend() -> None:
    """Un run que fracasó gastó tokens igual. Excluirlo maquillaría el coste
    real justo en los planes que más cuestan."""
    from api_server.routers.plans import aggregate_actual_spend

    spend = aggregate_actual_spend([_execution("1.00", 500, status="failed")])
    assert spend.cost_usd == Decimal("1.00")
    assert spend.runs == 1


def test_no_executions_is_zero_not_none() -> None:
    """Un plan aprobado y sin arrancar tiene gasto CERO, no «desconocido»: la
    cabecera debe poder pintarlo sin casos especiales."""
    from api_server.routers.plans import aggregate_actual_spend

    spend = aggregate_actual_spend([])
    assert spend.cost_usd == Decimal("0")
    assert spend.tokens == 0
    assert spend.runs == 0


def test_a_null_cost_column_does_not_break_the_sum() -> None:
    from api_server.routers.plans import aggregate_actual_spend

    rows = [SimpleNamespace(total_cost_usd=None, total_tokens=None, status="running")]
    spend = aggregate_actual_spend(rows)
    assert spend.cost_usd == Decimal("0")
    assert spend.tokens == 0
    assert spend.runs == 1


# ---------------------------------------------------------------------------
# El progreso: el que ya estaba escrito, ahora con consumidor
# ---------------------------------------------------------------------------
def test_progress_label_is_the_x_of_y_the_card_shows() -> None:
    progress = compute_plan_progress(
        "p1",
        [
            TaskSnapshot(id="a", status="done"),
            TaskSnapshot(id="b", status="in_progress"),
            TaskSnapshot(id="c", status="cancelled"),
        ],
    )
    assert progress.label == "1/2"  # la cancelada no cuenta en ninguno de los dos
    assert progress.open == 1


# ---------------------------------------------------------------------------
# La comparación estimado ↔ real
# ---------------------------------------------------------------------------
def test_ai_estimate_and_actual_share_a_currency() -> None:
    """La estimación de IA ya está en USD y el gasto real también, así que la
    comparación es directa. La estimación HUMANA está en EUR y mide otra cosa
    (horas de persona): se exponen aparte y no se restan — un número que mezcle
    las dos monedas sería inventado."""
    from api_server.routers.plans import build_plan_cost_status

    status = build_plan_cost_status(
        estimated_ai_usd_min=Decimal("1.00"),
        estimated_ai_usd_max=Decimal("4.00"),
        estimated_human_hours=Decimal("8.000"),
        estimated_human_cost=Decimal("400.00"),
        human_currency="EUR",
        actual_cost_usd=Decimal("6.50"),
        actual_tokens=120_000,
        actual_runs=7,
    )
    assert status.ai_currency == "USD"
    assert status.human_currency == "EUR"
    assert status.actual_ai_cost == Decimal("6.50")


def test_over_the_estimate_is_flagged() -> None:
    """La señal que el operador necesita: se pasó del techo estimado."""
    from api_server.routers.plans import build_plan_cost_status

    status = build_plan_cost_status(
        estimated_ai_usd_min=Decimal("1.00"),
        estimated_ai_usd_max=Decimal("4.00"),
        estimated_human_hours=Decimal("8.000"),
        estimated_human_cost=Decimal("400.00"),
        human_currency="EUR",
        actual_cost_usd=Decimal("6.50"),
        actual_tokens=1,
        actual_runs=1,
    )
    assert status.over_estimate is True


def test_within_the_estimated_range_is_not_flagged() -> None:
    from api_server.routers.plans import build_plan_cost_status

    status = build_plan_cost_status(
        estimated_ai_usd_min=Decimal("1.00"),
        estimated_ai_usd_max=Decimal("4.00"),
        estimated_human_hours=Decimal("8.000"),
        estimated_human_cost=Decimal("400.00"),
        human_currency="EUR",
        actual_cost_usd=Decimal("3.99"),
        actual_tokens=1,
        actual_runs=1,
    )
    assert status.over_estimate is False


def test_a_plan_that_never_ran_is_not_over_estimate() -> None:
    """Cero gasto no puede pintarse como desvío."""
    from api_server.routers.plans import build_plan_cost_status

    status = build_plan_cost_status(
        estimated_ai_usd_min=Decimal("0"),
        estimated_ai_usd_max=Decimal("0"),
        estimated_human_hours=Decimal("0"),
        estimated_human_cost=Decimal("0"),
        human_currency="EUR",
        actual_cost_usd=Decimal("0"),
        actual_tokens=0,
        actual_runs=0,
    )
    assert status.over_estimate is False


def test_no_estimate_cannot_be_exceeded() -> None:
    """Un plan sin estimación (spec sin `estimates`) gasta lo que gasta, pero
    marcarlo como «por encima» sería afirmar algo que no se sabe."""
    from api_server.routers.plans import build_plan_cost_status

    status = build_plan_cost_status(
        estimated_ai_usd_min=Decimal("0"),
        estimated_ai_usd_max=Decimal("0"),
        estimated_human_hours=Decimal("0"),
        estimated_human_cost=Decimal("0"),
        human_currency="EUR",
        actual_cost_usd=Decimal("12.00"),
        actual_tokens=1,
        actual_runs=3,
    )
    assert status.over_estimate is False
