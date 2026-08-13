"""Eval-run diff (Plan 14 task_14_06) — pure comparison of two runs.

A diff compares two :class:`~api_server.db.evals.EvalRun` of the **same
dataset** (the canonical use: an OLD prompt version vs a NEW one) and answers
the only question the Phase C merge-gate cares about — *did this change make
the agent better, worse, or no different?*

It produces three things:

  * **per-metric deltas** — ``candidate - base`` for pass rate, mean latency,
    mean cost and mean tokens (each ``None`` when either side is undefined);
  * **per-item changes** — matched by ``item_id``: the items that PASSED in
    base but FAIL in candidate (*regressions*) and the reverse (*improvements*);
  * an overall **verdict** — ``REGRESSED`` / ``IMPROVED`` / ``UNCHANGED`` that
    feeds the merge-gate (task_14_08).

Everything here is a PURE function: it takes ORM rows (or the already-computed
:class:`~api_server.evals.metrics.RunMetrics`) in and returns a dataclass out —
NO database, NO LLM, NO I/O. The endpoint (``GET .../eval-runs/diff``) loads the
two runs + their results under the caller's tenant RLS scope and hands them
here; the function never reaches across tenants because it never touches a
session at all.

Verdict semantics
-----------------
The verdict is driven by the **pass-rate delta** (quality is the metric the
merge-gate guards; latency/cost are reported but do not by themselves block a
merge). With a configurable ``pass_rate_regression_threshold`` (default
``0.0`` — any drop is a regression):

  * pass-rate drop ``>= threshold``  -> ``REGRESSED``
  * pass-rate rise ``> 0``           -> ``IMPROVED``
  * otherwise (equal, or a drop strictly smaller than the threshold when the
    threshold is positive) -> ``UNCHANGED``

Comparing a run to ITSELF (identical metrics) is therefore ``UNCHANGED``, and a
pass-rate drop beyond the threshold is ``REGRESSED`` — the two anchors the
merge-gate relies on. When either run's pass rate is undefined (an empty run),
the pass-rate delta is ``None`` and the verdict falls back to ``UNCHANGED``
(there is no quality signal to act on).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from api_server.evals.metrics import RunMetrics, compute_run_metrics

if TYPE_CHECKING:
    from api_server.db.evals import EvalResult, EvalRun

_PASS_VERDICT = "pass"


# =============================================================================
# Errors
# =============================================================================
class DatasetMismatchError(ValueError):
    """The two runs being diffed belong to DIFFERENT datasets.

    A diff is only meaningful between runs of the SAME golden dataset (e.g.
    old vs new prompt version against the same benchmark). Raised before any
    comparison so a meaningless cross-dataset diff is never produced.
    """


# =============================================================================
# Result shapes
# =============================================================================
class DiffVerdict(enum.StrEnum):
    """Overall direction of a run-to-run diff (feeds the Phase C merge-gate)."""

    REGRESSED = "regressed"
    IMPROVED = "improved"
    UNCHANGED = "unchanged"


@dataclass(frozen=True)
class MetricDelta:
    """A single metric's ``base`` / ``candidate`` values and their delta.

    ``delta`` is ``candidate - base``; it is ``None`` when EITHER side is
    ``None`` (the metric is undefined for an empty run / a run that reported
    nothing for it — a delta against an undefined value is itself undefined).
    """

    base: Decimal | None
    candidate: Decimal | None
    delta: Decimal | None


@dataclass(frozen=True)
class ItemChange:
    """One golden item whose verdict flipped between the two runs."""

    item_id: UUID | None
    base_verdict: str
    candidate_verdict: str


@dataclass(frozen=True)
class RunDiff:
    """The full comparison of a ``base`` run vs a ``candidate`` run.

    ``pass_rate`` / ``mean_latency_ms`` / ``mean_cost_usd`` / ``mean_tokens``
    are the per-metric deltas; ``regressions`` / ``improvements`` are the items
    that flipped (pass->fail / fail->pass); ``verdict`` is the overall
    direction the merge-gate reads.
    """

    verdict: DiffVerdict
    pass_rate: MetricDelta
    mean_latency_ms: MetricDelta
    mean_cost_usd: MetricDelta
    mean_tokens: MetricDelta
    regressions: tuple[ItemChange, ...]
    improvements: tuple[ItemChange, ...]
    # The pass-rate drop threshold the verdict was computed with (echoed so a
    # reader / the merge-gate sees the policy that produced the verdict).
    pass_rate_regression_threshold: Decimal

    def to_json(self) -> dict[str, Any]:
        """The JSON-serialisable shape the diff endpoint returns."""
        return {
            "verdict": self.verdict.value,
            "pass_rate": _delta_to_json(self.pass_rate),
            "mean_latency_ms": _delta_to_json(self.mean_latency_ms),
            "mean_cost_usd": _delta_to_json(self.mean_cost_usd),
            "mean_tokens": _delta_to_json(self.mean_tokens),
            "regressions": [_change_to_json(c) for c in self.regressions],
            "improvements": [_change_to_json(c) for c in self.improvements],
            "pass_rate_regression_threshold": float(self.pass_rate_regression_threshold),
        }


# =============================================================================
# Pure diff over RunMetrics + per-item results
# =============================================================================
def diff_metrics(
    base: RunMetrics,
    candidate: RunMetrics,
    base_results: Sequence[EvalResult],
    candidate_results: Sequence[EvalResult],
    *,
    pass_rate_regression_threshold: Decimal = Decimal("0"),
) -> RunDiff:
    """Diff two runs given their metrics + their per-item results (PURE).

    The per-metric deltas come from the two :class:`RunMetrics`; the per-item
    regressions/improvements are matched by ``item_id`` over the two result
    sets (an item only present in one run is not a flip and is ignored). The
    verdict is driven by the pass-rate delta against
    ``pass_rate_regression_threshold`` (see the module docstring).

    Touches no session — callers must pass rows already loaded under the
    correct tenant scope.
    """
    if pass_rate_regression_threshold < 0:
        raise ValueError(
            f"pass_rate_regression_threshold must be >= 0, got {pass_rate_regression_threshold!r}"
        )

    pass_rate_delta = _delta(base.pass_rate, candidate.pass_rate)
    regressions, improvements = _item_changes(base_results, candidate_results)
    verdict = _verdict(pass_rate_delta.delta, pass_rate_regression_threshold)

    return RunDiff(
        verdict=verdict,
        pass_rate=pass_rate_delta,
        mean_latency_ms=_delta(base.mean_latency_ms, candidate.mean_latency_ms),
        mean_cost_usd=_delta(base.mean_cost_usd, candidate.mean_cost_usd),
        mean_tokens=_delta(base.mean_tokens, candidate.mean_tokens),
        regressions=regressions,
        improvements=improvements,
        pass_rate_regression_threshold=pass_rate_regression_threshold,
    )


def diff_runs(
    base_run: EvalRun,
    candidate_run: EvalRun,
    base_results: Sequence[EvalResult],
    candidate_results: Sequence[EvalResult],
    *,
    pass_rate_regression_threshold: Decimal = Decimal("0"),
) -> RunDiff:
    """Diff two :class:`EvalRun` of the SAME dataset over their result rows.

    Rejects a cross-dataset pair with :class:`DatasetMismatchError` (a diff is
    only meaningful within one golden dataset). Metrics are recomputed from the
    result rows via :func:`~api_server.evals.metrics.compute_run_metrics` so the
    diff is consistent regardless of whether the run's denormalised roll-up has
    been written yet. PURE — touches no session.
    """
    if base_run.dataset_id != candidate_run.dataset_id:
        raise DatasetMismatchError(
            f"cannot diff runs of different datasets: base dataset "
            f"{base_run.dataset_id!r} != candidate dataset {candidate_run.dataset_id!r}"
        )
    return diff_metrics(
        compute_run_metrics(base_results),
        compute_run_metrics(candidate_results),
        base_results,
        candidate_results,
        pass_rate_regression_threshold=pass_rate_regression_threshold,
    )


# =============================================================================
# Internals
# =============================================================================
def _delta(base: Decimal | None, candidate: Decimal | None) -> MetricDelta:
    """A :class:`MetricDelta`; ``delta`` is ``None`` if either side is ``None``."""
    delta = candidate - base if base is not None and candidate is not None else None
    return MetricDelta(base=base, candidate=candidate, delta=delta)


def _item_changes(
    base_results: Sequence[EvalResult],
    candidate_results: Sequence[EvalResult],
) -> tuple[tuple[ItemChange, ...], tuple[ItemChange, ...]]:
    """Match results by ``item_id`` and classify each flip.

    An item that PASSED in base and now FAILS (any non-pass verdict) in
    candidate is a regression; the reverse is an improvement. Only items
    present in BOTH runs (a real, comparable pair) are considered — an item
    with a NULL ``item_id`` or present in only one run cannot be matched and is
    skipped. Output is ordered by the candidate run's result order for
    deterministic, reproducible diffs.
    """
    base_by_item: dict[UUID, EvalResult] = {
        r.item_id: r for r in base_results if r.item_id is not None
    }
    regressions: list[ItemChange] = []
    improvements: list[ItemChange] = []
    for cand in candidate_results:
        if cand.item_id is None:
            continue
        base = base_by_item.get(cand.item_id)
        if base is None:
            continue
        base_pass = base.verdict == _PASS_VERDICT
        cand_pass = cand.verdict == _PASS_VERDICT
        if base_pass and not cand_pass:
            regressions.append(
                ItemChange(
                    item_id=cand.item_id,
                    base_verdict=base.verdict,
                    candidate_verdict=cand.verdict,
                )
            )
        elif not base_pass and cand_pass:
            improvements.append(
                ItemChange(
                    item_id=cand.item_id,
                    base_verdict=base.verdict,
                    candidate_verdict=cand.verdict,
                )
            )
    return tuple(regressions), tuple(improvements)


def _verdict(pass_rate_delta: Decimal | None, threshold: Decimal) -> DiffVerdict:
    """Map the pass-rate delta to an overall verdict (see module docstring).

    A drop (negative delta) whose magnitude is ``>= threshold`` is a
    regression; any rise is an improvement; everything else (equal, undefined,
    or a sub-threshold drop) is unchanged.
    """
    if pass_rate_delta is None:
        return DiffVerdict.UNCHANGED
    if pass_rate_delta < 0 and -pass_rate_delta >= threshold:
        return DiffVerdict.REGRESSED
    if pass_rate_delta > 0:
        return DiffVerdict.IMPROVED
    return DiffVerdict.UNCHANGED


def _delta_to_json(d: MetricDelta) -> dict[str, float | None]:
    return {
        "base": _to_float(d.base),
        "candidate": _to_float(d.candidate),
        "delta": _to_float(d.delta),
    }


def _change_to_json(c: ItemChange) -> dict[str, str | None]:
    return {
        "item_id": str(c.item_id) if c.item_id is not None else None,
        "base_verdict": c.base_verdict,
        "candidate_verdict": c.candidate_verdict,
    }


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


__all__ = [
    "DatasetMismatchError",
    "DiffVerdict",
    "ItemChange",
    "MetricDelta",
    "RunDiff",
    "diff_metrics",
    "diff_runs",
]
