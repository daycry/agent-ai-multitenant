"""Unit — el detector PURO de outliers de agentes (Plan 14 Fase D, hallazgo #8).

``detect_outliers`` es una función sin I/O sobre filas ``AgentMetric`` + config de
regla; el path de evaluación+alerta (DB) lo cubre ``tests/integration``. Aquí se
clava el veredicto EXACTO de las dos ramas (floor de success_rate y desviación
estadística cost/latency), los filtros de significancia y los guards de ValueError,
subiendo la cobertura del dominio (ratchet #8).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from api_server.db.outlier_alert_rule import OutlierMetric
from api_server.stats.outliers import AgentMetric, _mean_stddev, _sqrt, detect_outliers

pytestmark = pytest.mark.unit


def _metric(
    *,
    run_count: int = 10,
    success_rate: str | None = None,
    mean_cost: str | None = None,
    mean_latency_ms: str | None = None,
    name: str = "a",
) -> AgentMetric:
    return AgentMetric(
        agent_id=uuid4(),
        agent_name=name,
        agent_role="backend",
        run_count=run_count,
        success_rate=Decimal(success_rate) if success_rate is not None else None,
        mean_cost=Decimal(mean_cost) if mean_cost is not None else None,
        mean_latency_ms=Decimal(mean_latency_ms) if mean_latency_ms is not None else None,
    )


# --- rama success_rate: floor (LOWER bound) -----------------------------------
def test_success_rate_below_floor_is_flagged() -> None:
    metrics = [
        _metric(success_rate="0.60", name="bad"),
        _metric(success_rate="0.95", name="good"),
    ]
    d = detect_outliers(
        metrics, metric=OutlierMetric.SUCCESS_RATE, min_runs=5, success_rate_floor=Decimal("0.70")
    )
    assert d.considered == 2
    assert [f.agent_name for f in d.flagged] == ["bad"]
    assert d.flagged[0].bound == Decimal("0.70")
    assert d.population_mean is None and d.population_stddev is None


def test_success_rate_exactly_at_floor_is_not_flagged() -> None:
    d = detect_outliers(
        [_metric(success_rate="0.70")],
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=1,
        success_rate_floor=Decimal("0.70"),
    )
    assert d.flagged == ()


def test_min_runs_excludes_small_samples() -> None:
    """Un agente por debajo de min_runs no se considera aunque flaquee."""
    d = detect_outliers(
        [_metric(run_count=2, success_rate="0.10")],
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=5,
        success_rate_floor=Decimal("0.70"),
    )
    assert d.considered == 0 and d.flagged == ()


def test_metricless_agent_is_not_considered() -> None:
    """success_rate None (agente sin runs terminadas) no cuenta."""
    d = detect_outliers(
        [_metric(success_rate=None)],
        metric=OutlierMetric.SUCCESS_RATE,
        min_runs=1,
        success_rate_floor=Decimal("0.70"),
    )
    assert d.considered == 0


# --- rama estadística: cost/latency (UPPER bound mean + k·stddev) -------------
def test_cost_deviation_above_bound_is_flagged() -> None:
    metrics = [
        _metric(mean_cost="1.00", name="cheap1"),
        _metric(mean_cost="1.00", name="cheap2"),
        _metric(mean_cost="1.00", name="cheap3"),
        _metric(mean_cost="10.00", name="expensive"),
    ]
    d = detect_outliers(metrics, metric=OutlierMetric.COST, min_runs=1, stddev_k=Decimal("1.5"))
    assert [f.agent_name for f in d.flagged] == ["expensive"]
    assert d.population_mean is not None and d.population_stddev is not None
    # el bound es mean + 1.5·stddev y el caro lo supera
    assert d.flagged[0].value == Decimal("10.00")
    assert d.flagged[0].bound == d.population_mean + Decimal("1.5") * d.population_stddev


def test_single_agent_has_no_spread() -> None:
    d = detect_outliers(
        [_metric(mean_latency_ms="500")],
        metric=OutlierMetric.LATENCY,
        min_runs=1,
        stddev_k=Decimal("2"),
    )
    assert d.flagged == ()
    assert d.population_mean == Decimal("500") and d.population_stddev is None


def test_uniform_population_flags_nobody() -> None:
    """stddev=0 → bound == mean; nadie lo supera (no hay '>')."""
    metrics = [_metric(mean_cost="2.00", name=f"a{i}") for i in range(3)]
    d = detect_outliers(metrics, metric=OutlierMetric.COST, min_runs=1, stddev_k=Decimal("1"))
    assert d.flagged == () and d.population_stddev == Decimal("0")


# --- guards de ValueError -----------------------------------------------------
def test_min_runs_below_one_raises() -> None:
    with pytest.raises(ValueError, match="min_runs"):
        detect_outliers([], metric=OutlierMetric.COST, min_runs=0, stddev_k=Decimal("1"))


def test_success_rate_without_floor_raises() -> None:
    with pytest.raises(ValueError, match="success_rate_floor"):
        detect_outliers(
            [_metric(success_rate="0.5")], metric=OutlierMetric.SUCCESS_RATE, min_runs=1
        )


def test_cost_without_stddev_k_raises() -> None:
    with pytest.raises(ValueError, match="stddev_k"):
        detect_outliers([_metric(mean_cost="1")], metric=OutlierMetric.COST, min_runs=1)


# --- _mean_stddev / _sqrt: aritmética Decimal poblacional ---------------------
def test_mean_stddev_population() -> None:
    mean, stddev = _mean_stddev(
        [
            Decimal("2"),
            Decimal("4"),
            Decimal("4"),
            Decimal("4"),
            Decimal("5"),
            Decimal("5"),
            Decimal("7"),
            Decimal("9"),
        ]
    )
    assert mean == Decimal("5")
    assert stddev == Decimal("2")  # población: sqrt(32/8)=2


def test_sqrt_of_zero_and_negative_is_zero() -> None:
    assert _sqrt(Decimal("0")) == Decimal("0")
    assert _sqrt(Decimal("-1")) == Decimal("0")
