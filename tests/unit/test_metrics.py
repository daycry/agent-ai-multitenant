"""Unit tests for the standard eval metrics (Plan 14 task_14_05).

In-process only — NO DB, NO LLM. The metrics are pure functions over plain
numbers / :class:`~api_server.db.evals.EvalResult` rows, so we pin EXACT
expected values:

  * ``pass_rate`` over a mixed pass/fail set (and the empty-set ``None``);
  * ``percentile`` p50/p95 with the nearest-rank convention on KNOWN latency
    sets (hand-computed expected values);
  * ``mean`` cost / tokens (exact, including rounding) and the empty-set
    ``None``;
  * ``compute_run_metrics`` over a set of results — including a result that
    reports no latency/tokens/cost (skipped, not zeroed) — and the empty set
    (all-``None`` / zero-count, no divide-by-zero);
  * ``apply_to_run`` denormalises the roll-up (scalar columns + the p50/p95 +
    counts in ``aggregate_metrics`` JSONB) onto an EvalRun.

``domain`` is imported so the eval ORM's cross-module FK targets are
registered with the mapper registry before we instantiate EvalRun/EvalResult.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

# Import for FK-target mapper registration (agents / tasks / executions).
from api_server.db import domain as _domain  # noqa: F401
from api_server.db.evals import EvalResult, EvalResultVerdict, EvalRun
from api_server.evals.metrics import (
    RunMetrics,
    apply_to_run,
    compute_run_metrics,
    mean,
    pass_rate,
    percentile,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# pass_rate
# ---------------------------------------------------------------------------
def test_pass_rate_mixed() -> None:
    # 3 of 4 passed -> 0.75.
    assert pass_rate(3, 4) == Decimal("0.750")


def test_pass_rate_all_and_none() -> None:
    assert pass_rate(5, 5) == Decimal("1.000")
    assert pass_rate(0, 5) == Decimal("0.000")


def test_pass_rate_empty_is_none_not_zero() -> None:
    # Undefined (None) over an empty set — distinguishes "no items" from
    # "all failed". No divide-by-zero.
    assert pass_rate(0, 0) is None


def test_pass_rate_rounds_to_three_decimals() -> None:
    # 1/3 = 0.333... -> 0.333 (ROUND_HALF_UP at 3 dp).
    assert pass_rate(1, 3) == Decimal("0.333")
    # 2/3 = 0.666... -> 0.667.
    assert pass_rate(2, 3) == Decimal("0.667")


# ---------------------------------------------------------------------------
# percentile — nearest-rank, exact expected values
# ---------------------------------------------------------------------------
def test_percentile_p50_p95_ten_values() -> None:
    # Sorted: [100,200,...,1000], n=10.
    latencies = [1000, 100, 500, 300, 900, 200, 800, 400, 700, 600]
    # p50: ceil(0.50*10)=5 -> ordered[4] = 500.
    assert percentile(latencies, 50) == Decimal("500")
    # p95: ceil(0.95*10)=10 -> ordered[9] = 1000.
    assert percentile(latencies, 95) == Decimal("1000")


def test_percentile_p50_p95_five_values() -> None:
    # Sorted: [10,20,30,40,50], n=5.
    latencies = [50, 10, 40, 20, 30]
    # p50: ceil(0.50*5)=ceil(2.5)=3 -> ordered[2] = 30 (upper-median).
    assert percentile(latencies, 50) == Decimal("30")
    # p95: ceil(0.95*5)=ceil(4.75)=5 -> ordered[4] = 50.
    assert percentile(latencies, 95) == Decimal("50")


def test_percentile_single_value_is_that_value() -> None:
    assert percentile([42], 50) == Decimal("42")
    assert percentile([42], 95) == Decimal("42")
    # p0 clamps the rank up to 1.
    assert percentile([42], 0) == Decimal("42")
    assert percentile([42], 100) == Decimal("42")


def test_percentile_empty_is_none() -> None:
    assert percentile([], 50) is None
    assert percentile([], 95) is None


def test_percentile_out_of_range_raises() -> None:
    with pytest.raises(ValueError):
        percentile([1, 2, 3], 150)
    with pytest.raises(ValueError):
        percentile([1, 2, 3], -1)


# ---------------------------------------------------------------------------
# mean — cost / tokens
# ---------------------------------------------------------------------------
def test_mean_tokens_exact() -> None:
    # (10 + 20 + 30) / 3 = 20.
    assert mean([10, 20, 30], scale=2) == Decimal("20.00")


def test_mean_cost_usd_exact_with_rounding() -> None:
    # (0.000100 + 0.000200 + 0.000300) / 3 = 0.0002 -> 0.000200 @ 6 dp.
    costs = [Decimal("0.0001"), Decimal("0.0002"), Decimal("0.0003")]
    assert mean(costs, scale=6) == Decimal("0.000200")


def test_mean_rounds_half_up() -> None:
    # (1 + 2) / 2 = 1.5 -> 1.50; (1+1+2)/3 = 1.333... -> 1.33.
    assert mean([1, 2], scale=2) == Decimal("1.50")
    assert mean([1, 1, 2], scale=2) == Decimal("1.33")


def test_mean_empty_is_none() -> None:
    assert mean([], scale=2) is None
    assert mean([], scale=6) is None


# ---------------------------------------------------------------------------
# compute_run_metrics — over EvalResult rows (pure, no DB)
# ---------------------------------------------------------------------------
def _result(
    verdict: str, *, latency_ms: int | None, tokens: int | None, cost: str | None
) -> EvalResult:
    """Build an unsaved EvalResult carrying only the metric fields we read."""
    return EvalResult(
        verdict=verdict,
        latency_ms=latency_ms,
        tokens=tokens,
        cost_usd=Decimal(cost) if cost is not None else None,
    )


def test_compute_run_metrics_mixed() -> None:
    results = [
        _result(EvalResultVerdict.PASS.value, latency_ms=100, tokens=10, cost="0.0001"),
        _result(EvalResultVerdict.PASS.value, latency_ms=300, tokens=30, cost="0.0003"),
        _result(EvalResultVerdict.FAIL.value, latency_ms=200, tokens=20, cost="0.0002"),
        _result(EvalResultVerdict.ERROR.value, latency_ms=400, tokens=40, cost="0.0004"),
    ]
    m = compute_run_metrics(results)

    assert m.total_items == 4
    assert m.passed_items == 2
    assert m.pass_rate == Decimal("0.500")
    # Latencies sorted [100,200,300,400], n=4:
    #   p50: ceil(0.5*4)=2 -> ordered[1] = 200.
    #   p95: ceil(0.95*4)=ceil(3.8)=4 -> ordered[3] = 400.
    assert m.p50_latency_ms == Decimal("200")
    assert m.p95_latency_ms == Decimal("400")
    # Mean latency (100+200+300+400)/4 = 250.
    assert m.mean_latency_ms == Decimal("250.00")
    # Mean tokens (10+20+30+40)/4 = 25.
    assert m.mean_tokens == Decimal("25.00")
    # Mean cost (0.0001+0.0002+0.0003+0.0004)/4 = 0.00025.
    assert m.mean_cost_usd == Decimal("0.000250")
    assert m.latency_count == 4
    assert m.tokens_count == 4
    assert m.cost_count == 4


def test_compute_run_metrics_skips_null_usage_not_zeroed() -> None:
    # A result with NULL latency/tokens/cost is SKIPPED from the means /
    # percentiles (not counted as zero), but still counts toward total/pass.
    results = [
        _result(EvalResultVerdict.PASS.value, latency_ms=100, tokens=10, cost="0.0001"),
        _result(EvalResultVerdict.PASS.value, latency_ms=None, tokens=None, cost=None),
    ]
    m = compute_run_metrics(results)

    assert m.total_items == 2
    assert m.passed_items == 2
    assert m.pass_rate == Decimal("1.000")
    # Only the first result reported usage.
    assert m.latency_count == 1
    assert m.tokens_count == 1
    assert m.cost_count == 1
    assert m.p50_latency_ms == Decimal("100")
    assert m.p95_latency_ms == Decimal("100")
    assert m.mean_latency_ms == Decimal("100.00")
    assert m.mean_tokens == Decimal("10.00")
    assert m.mean_cost_usd == Decimal("0.000100")


def test_compute_run_metrics_empty_is_well_defined() -> None:
    m = compute_run_metrics([])
    assert m == RunMetrics(
        total_items=0,
        passed_items=0,
        pass_rate=None,
        p50_latency_ms=None,
        p95_latency_ms=None,
        mean_latency_ms=None,
        mean_tokens=None,
        mean_cost_usd=None,
        latency_count=0,
        tokens_count=0,
        cost_count=0,
    )


# ---------------------------------------------------------------------------
# apply_to_run — denormalise the roll-up onto an EvalRun
# ---------------------------------------------------------------------------
def test_apply_to_run_populates_aggregate_from_results() -> None:
    results = [
        _result(EvalResultVerdict.PASS.value, latency_ms=100, tokens=10, cost="0.0001"),
        _result(EvalResultVerdict.PASS.value, latency_ms=300, tokens=30, cost="0.0003"),
        _result(EvalResultVerdict.FAIL.value, latency_ms=200, tokens=20, cost="0.0002"),
        _result(EvalResultVerdict.ERROR.value, latency_ms=400, tokens=40, cost="0.0004"),
    ]
    run = EvalRun(aggregate_metrics={"preexisting": "kept"})

    apply_to_run(run, compute_run_metrics(results))

    # Scalar columns populated.
    assert run.total_items == 4
    assert run.passed_items == 2
    assert run.pass_rate == Decimal("0.500")
    assert run.mean_latency_ms == Decimal("250.00")
    assert run.mean_tokens == Decimal("25.00")
    assert run.mean_cost_usd == Decimal("0.000250")
    # JSONB carries p50/p95 + counts; a pre-existing key is preserved.
    assert run.aggregate_metrics["preexisting"] == "kept"
    assert run.aggregate_metrics["p50_latency_ms"] == 200.0
    assert run.aggregate_metrics["p95_latency_ms"] == 400.0
    assert run.aggregate_metrics["latency_count"] == 4
    assert run.aggregate_metrics["tokens_count"] == 4
    assert run.aggregate_metrics["cost_count"] == 4


def test_apply_to_run_empty_results_well_defined() -> None:
    run = EvalRun()
    apply_to_run(run, compute_run_metrics([]))

    assert run.total_items == 0
    assert run.passed_items == 0
    assert run.pass_rate is None
    assert run.mean_latency_ms is None
    assert run.mean_tokens is None
    assert run.mean_cost_usd is None
    assert run.aggregate_metrics["p50_latency_ms"] is None
    assert run.aggregate_metrics["p95_latency_ms"] is None
    assert run.aggregate_metrics["latency_count"] == 0
