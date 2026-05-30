"""Unit tests for the eval-run diff (Plan 14 task_14_06).

In-process only — NO DB, NO LLM. The diff is a PURE comparison of two
:class:`~api_server.db.evals.EvalRun` of the SAME dataset over their per-item
:class:`~api_server.db.evals.EvalResult` rows, so we pin EXACT expected values:

  * per-metric deltas (pass_rate / latency / cost / tokens) are
    ``candidate - base`` (exact), and ``None`` when either side is undefined;
  * items that regressed (pass->fail) and improved (fail->pass) are matched by
    ``item_id`` and listed; an item present in only one run is ignored;
  * a run pair over DIFFERENT datasets is rejected (``DatasetMismatchError``);
  * IDENTICAL runs (same metrics) diff to ``UNCHANGED``;
  * a pass-rate drop beyond the regression threshold diffs to ``REGRESSED``;
  * a pass-rate rise diffs to ``IMPROVED``.

``domain`` is imported so the eval ORM's cross-module FK targets are
registered with the mapper registry before we instantiate EvalRun/EvalResult.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

# Import for FK-target mapper registration (agents / tasks / executions).
from api_server.db import domain as _domain  # noqa: F401
from api_server.db.evals import EvalResult, EvalResultVerdict, EvalRun
from api_server.evals.diff import (
    DatasetMismatchError,
    DiffVerdict,
    diff_runs,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Builders — unsaved ORM rows carrying only the fields the diff reads
# ---------------------------------------------------------------------------
def _result(
    *,
    item_id: UUID | None,
    verdict: str,
    latency_ms: int | None = None,
    tokens: int | None = None,
    cost: str | None = None,
) -> EvalResult:
    return EvalResult(
        item_id=item_id,
        verdict=verdict,
        latency_ms=latency_ms,
        tokens=tokens,
        cost_usd=Decimal(cost) if cost is not None else None,
    )


def _run(dataset_id: UUID) -> EvalRun:
    return EvalRun(id=uuid4(), dataset_id=dataset_id)


_PASS = EvalResultVerdict.PASS.value
_FAIL = EvalResultVerdict.FAIL.value
_ERROR = EvalResultVerdict.ERROR.value


# ---------------------------------------------------------------------------
# Per-metric deltas computed correctly
# ---------------------------------------------------------------------------
def test_metric_deltas_are_candidate_minus_base() -> None:
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)

    # base: 1/2 pass, latencies [100,300] -> mean 200; tokens [10,30] mean 20;
    #       cost [0.0001,0.0003] mean 0.0002.
    base_results = [
        _result(item_id=i1, verdict=_PASS, latency_ms=100, tokens=10, cost="0.0001"),
        _result(item_id=i2, verdict=_FAIL, latency_ms=300, tokens=30, cost="0.0003"),
    ]
    # candidate: 2/2 pass, latencies [120,180] -> mean 150; tokens [20,40] mean 30;
    #            cost [0.0002,0.0004] mean 0.0003.
    candidate_results = [
        _result(item_id=i1, verdict=_PASS, latency_ms=120, tokens=20, cost="0.0002"),
        _result(item_id=i2, verdict=_PASS, latency_ms=180, tokens=40, cost="0.0004"),
    ]

    diff = diff_runs(base, candidate, base_results, candidate_results)

    # pass_rate: base 0.5 -> candidate 1.0, delta +0.5.
    assert diff.pass_rate.base == Decimal("0.500")
    assert diff.pass_rate.candidate == Decimal("1.000")
    assert diff.pass_rate.delta == Decimal("0.500")
    # mean latency: 200 -> 150, delta -50.
    assert diff.mean_latency_ms.base == Decimal("200.00")
    assert diff.mean_latency_ms.candidate == Decimal("150.00")
    assert diff.mean_latency_ms.delta == Decimal("-50.00")
    # mean tokens: 20 -> 30, delta +10.
    assert diff.mean_tokens.delta == Decimal("10.00")
    # mean cost: 0.0002 -> 0.0003, delta +0.0001.
    assert diff.mean_cost_usd.base == Decimal("0.000200")
    assert diff.mean_cost_usd.candidate == Decimal("0.000300")
    assert diff.mean_cost_usd.delta == Decimal("0.000100")
    # Quality went up -> improved.
    assert diff.verdict is DiffVerdict.IMPROVED


def test_metric_delta_none_when_either_side_undefined() -> None:
    dataset = uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    # base has results (a defined pass_rate); candidate is empty (undefined).
    base_results = [_result(item_id=uuid4(), verdict=_PASS, latency_ms=100)]
    candidate_results: list[EvalResult] = []

    diff = diff_runs(base, candidate, base_results, candidate_results)

    assert diff.pass_rate.base == Decimal("1.000")
    assert diff.pass_rate.candidate is None
    assert diff.pass_rate.delta is None
    # Latency: candidate reported none -> candidate None -> delta None.
    assert diff.mean_latency_ms.delta is None
    # Undefined pass-rate delta -> no signal -> unchanged.
    assert diff.verdict is DiffVerdict.UNCHANGED


# ---------------------------------------------------------------------------
# Per-item regressions + improvements are listed
# ---------------------------------------------------------------------------
def test_regressions_and_improvements_listed() -> None:
    dataset = uuid4()
    regressed, improved, stable_pass, only_base = uuid4(), uuid4(), uuid4(), uuid4()
    only_candidate = uuid4()
    base = _run(dataset)
    candidate = _run(dataset)

    base_results = [
        _result(item_id=regressed, verdict=_PASS),  # pass -> fail
        _result(item_id=improved, verdict=_FAIL),  # fail -> pass
        _result(item_id=stable_pass, verdict=_PASS),  # pass -> pass (no flip)
        _result(item_id=only_base, verdict=_PASS),  # not in candidate (ignored)
    ]
    candidate_results = [
        _result(item_id=regressed, verdict=_FAIL),
        _result(item_id=improved, verdict=_PASS),
        _result(item_id=stable_pass, verdict=_PASS),
        _result(item_id=only_candidate, verdict=_PASS),  # not in base (ignored)
    ]

    diff = diff_runs(base, candidate, base_results, candidate_results)

    assert [c.item_id for c in diff.regressions] == [regressed]
    assert diff.regressions[0].base_verdict == _PASS
    assert diff.regressions[0].candidate_verdict == _FAIL

    assert [c.item_id for c in diff.improvements] == [improved]
    assert diff.improvements[0].base_verdict == _FAIL
    assert diff.improvements[0].candidate_verdict == _PASS


def test_error_verdict_counts_as_a_regression_from_pass() -> None:
    # A non-pass candidate verdict (error) when base passed is a regression.
    dataset = uuid4()
    item = uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    diff = diff_runs(
        base,
        candidate,
        [_result(item_id=item, verdict=_PASS)],
        [_result(item_id=item, verdict=_ERROR)],
    )
    assert [c.item_id for c in diff.regressions] == [item]
    assert diff.improvements == ()


# ---------------------------------------------------------------------------
# A run pair over different datasets is rejected
# ---------------------------------------------------------------------------
def test_diff_across_different_datasets_is_rejected() -> None:
    base = _run(uuid4())
    candidate = _run(uuid4())  # different dataset
    with pytest.raises(DatasetMismatchError):
        diff_runs(base, candidate, [], [])


# ---------------------------------------------------------------------------
# Identical runs -> UNCHANGED
# ---------------------------------------------------------------------------
def test_identical_runs_are_unchanged() -> None:
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    rows_base = [
        _result(item_id=i1, verdict=_PASS, latency_ms=100, tokens=10, cost="0.0001"),
        _result(item_id=i2, verdict=_FAIL, latency_ms=200, tokens=20, cost="0.0002"),
    ]
    rows_candidate = [
        _result(item_id=i1, verdict=_PASS, latency_ms=100, tokens=10, cost="0.0001"),
        _result(item_id=i2, verdict=_FAIL, latency_ms=200, tokens=20, cost="0.0002"),
    ]

    diff = diff_runs(base, candidate, rows_base, rows_candidate)

    assert diff.verdict is DiffVerdict.UNCHANGED
    assert diff.pass_rate.delta == Decimal("0.000")
    assert diff.regressions == ()
    assert diff.improvements == ()


def test_run_diffed_against_itself_is_unchanged() -> None:
    # Comparing a run's own result set to itself: zero delta, no flips.
    dataset = uuid4()
    rows = [
        _result(item_id=uuid4(), verdict=_PASS, latency_ms=100),
        _result(item_id=uuid4(), verdict=_PASS, latency_ms=100),
    ]
    run = _run(dataset)
    diff = diff_runs(run, run, rows, rows)
    assert diff.verdict is DiffVerdict.UNCHANGED
    assert diff.pass_rate.delta == Decimal("0.000")


# ---------------------------------------------------------------------------
# A pass-rate drop beyond the threshold -> REGRESSED
# ---------------------------------------------------------------------------
def test_pass_rate_drop_beyond_threshold_is_regressed() -> None:
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    # base 2/2 pass (1.0); candidate 1/2 pass (0.5): drop of 0.5.
    base_results = [
        _result(item_id=i1, verdict=_PASS),
        _result(item_id=i2, verdict=_PASS),
    ]
    candidate_results = [
        _result(item_id=i1, verdict=_PASS),
        _result(item_id=i2, verdict=_FAIL),
    ]

    # Default threshold 0.0: any drop regresses.
    diff = diff_runs(base, candidate, base_results, candidate_results)
    assert diff.pass_rate.delta == Decimal("-0.500")
    assert diff.verdict is DiffVerdict.REGRESSED
    assert [c.item_id for c in diff.regressions] == [i2]


def test_pass_rate_drop_below_threshold_is_unchanged() -> None:
    # A 0.5 drop with a 0.6 tolerance is NOT a regression (sub-threshold).
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    base_results = [
        _result(item_id=i1, verdict=_PASS),
        _result(item_id=i2, verdict=_PASS),
    ]
    candidate_results = [
        _result(item_id=i1, verdict=_PASS),
        _result(item_id=i2, verdict=_FAIL),
    ]

    diff = diff_runs(
        base,
        candidate,
        base_results,
        candidate_results,
        pass_rate_regression_threshold=Decimal("0.6"),
    )
    assert diff.pass_rate.delta == Decimal("-0.500")
    # Drop magnitude 0.5 < threshold 0.6 -> not a regression. No quality rise
    # either -> unchanged.
    assert diff.verdict is DiffVerdict.UNCHANGED


def test_negative_threshold_rejected() -> None:
    dataset = uuid4()
    with pytest.raises(ValueError):
        diff_runs(
            _run(dataset),
            _run(dataset),
            [],
            [],
            pass_rate_regression_threshold=Decimal("-0.1"),
        )
