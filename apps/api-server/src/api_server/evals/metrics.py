"""Standard eval metrics (Plan 14 task_14_05) — pure functions over results.

These are the dashboard-grade roll-ups computed over a set of
:class:`~api_server.db.evals.EvalResult` rows (one run's results, or any
slice of executions): the **pass rate**, **p50 / p95 latency**, the **mean
cost (USD)** and **mean tokens** (plus the counts the means were taken over).

Everything here is a PURE function: it takes ORM rows (or plain numbers) in
and returns numbers / a dataclass out — NO database, NO LLM, NO I/O. That is
deliberate: the metrics are unit-testable with exact expected values, and the
judge engine (``run_eval``) reuses :func:`compute_run_metrics` to denormalise
the roll-up onto the :class:`~api_server.db.evals.EvalRun` when a run completes.

Percentile convention
----------------------
We use the **nearest-rank** method (NOT linear interpolation): for a sorted
sample of ``n`` values the value at percentile ``p`` is the one at 1-based rank
``ceil(p/100 * n)`` (clamped to ``[1, n]``). Nearest-rank always returns an
ACTUAL observed latency (never an interpolated number that no request hit),
which is the right semantics for a latency SLO and is stable for the small
samples a single eval run produces. ``p50`` is therefore an upper-median, not
the average-of-two-middles median. Consistent + documented, per the plan.

Empty input is well-defined everywhere: ``pass_rate`` over zero results is
``None`` (undefined, not ``0``), the percentiles / means over an empty set are
``None``, and the counts are ``0`` — never a divide-by-zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from api_server.db.evals import EvalResult

# Verdict value a result must carry to count as a pass. Hard-coded as the
# literal (not importing the enum) keeps this module import-light and pure;
# the value is the StrEnum member value of ``EvalResultVerdict.PASS``.
_PASS_VERDICT = "pass"


@dataclass(frozen=True)
class RunMetrics:
    """The standard roll-up of one run's (or any slice of) eval results.

    All fields are well-defined for an empty input: ``pass_rate`` and every
    ``*_latency_ms`` / ``mean_*`` is ``None`` when there is nothing to measure,
    and the counts are ``0``. ``pass_rate`` is a fraction in ``[0, 1]``.
    """

    total_items: int
    passed_items: int
    pass_rate: Decimal | None
    p50_latency_ms: Decimal | None
    p95_latency_ms: Decimal | None
    mean_latency_ms: Decimal | None
    mean_tokens: Decimal | None
    mean_cost_usd: Decimal | None
    # Counts the means were actually taken over (a result may have a NULL
    # latency / tokens / cost; the mean skips it). Lets a reader tell "mean is
    # None because there were no results" from "... because none reported it".
    latency_count: int
    tokens_count: int
    cost_count: int

    def to_aggregate_metrics(self) -> dict[str, Any]:
        """The JSONB shape stored in ``EvalRun.aggregate_metrics``.

        Floats (JSON has no Decimal); ``None`` stays ``null``. The
        denormalised scalar columns (``pass_rate`` / ``mean_*``) are written
        separately by :func:`apply_to_run`; this dict carries the extras the
        columns do not have (the percentiles + the per-metric counts).
        """
        return {
            "p50_latency_ms": _to_float(self.p50_latency_ms),
            "p95_latency_ms": _to_float(self.p95_latency_ms),
            "latency_count": self.latency_count,
            "tokens_count": self.tokens_count,
            "cost_count": self.cost_count,
        }


# =============================================================================
# Primitive pure functions (numbers in, number out)
# =============================================================================
def pass_rate(passed: int, total: int) -> Decimal | None:
    """Fraction of passes in ``[0, 1]``; ``None`` when ``total == 0``.

    Undefined (``None``) rather than ``0`` for an empty set so a dashboard
    distinguishes "no items" from "all failed". Quantised to 3 decimals (the
    ``Numeric(4,3)`` ``eval_runs.pass_rate`` column).
    """
    if total <= 0:
        return None
    return _quantize(Decimal(passed) / Decimal(total), "0.001")


def percentile(values: Sequence[float | int | Decimal], p: float) -> Decimal | None:
    """Nearest-rank percentile of ``values``; ``None`` for an empty sequence.

    ``p`` is in ``[0, 100]``. The result is the value at 1-based rank
    ``ceil(p/100 * n)`` (clamped to ``[1, n]``) of the sorted sample — an
    actual observed value, never interpolated. ``percentile(xs, 50)`` is the
    upper-median.
    """
    if not values:
        return None
    if not 0 <= p <= 100:
        raise ValueError(f"percentile p must be in [0, 100], got {p!r}")
    ordered = sorted(Decimal(str(v)) for v in values)
    n = len(ordered)
    rank = math.ceil((p / 100.0) * n)
    rank = max(1, min(rank, n))  # clamp (p=0 -> rank 0 -> 1; p=100 -> n)
    return ordered[rank - 1]


def mean(values: Sequence[int | float | Decimal], *, scale: int) -> Decimal | None:
    """Arithmetic mean quantised to ``scale`` decimals; ``None`` if empty."""
    if not values:
        return None
    total = sum((Decimal(str(v)) for v in values), Decimal("0"))
    avg = total / Decimal(len(values))
    return _quantize(avg, Decimal(1).scaleb(-scale))


# =============================================================================
# Roll-up over EvalResult rows
# =============================================================================
def compute_run_metrics(results: Sequence[EvalResult]) -> RunMetrics:
    """Compute the standard metrics over a set of :class:`EvalResult` rows.

    Pass rate counts ``pass`` verdicts over the total number of results; the
    latency percentiles + every mean are taken ONLY over the results that
    actually reported that metric (a ``NULL`` latency / tokens / cost is
    skipped, not treated as zero). An empty input yields the well-defined
    all-``None`` / zero-count :class:`RunMetrics` (no divide-by-zero).

    Pure: reads only the rows' attributes, touches no session.
    """
    total = len(results)
    passed = sum(1 for r in results if r.verdict == _PASS_VERDICT)

    latencies = [r.latency_ms for r in results if r.latency_ms is not None]
    tokens = [r.tokens for r in results if r.tokens is not None]
    costs = [r.cost_usd for r in results if r.cost_usd is not None]

    return RunMetrics(
        total_items=total,
        passed_items=passed,
        pass_rate=pass_rate(passed, total),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        mean_latency_ms=mean(latencies, scale=2),
        mean_tokens=mean(tokens, scale=2),
        mean_cost_usd=mean(costs, scale=6),
        latency_count=len(latencies),
        tokens_count=len(tokens),
        cost_count=len(costs),
    )


def apply_to_run(run: Any, metrics: RunMetrics) -> None:
    """Denormalise ``metrics`` onto an :class:`EvalRun` (the dashboards read it).

    Writes the scalar roll-up columns (``total_items`` / ``passed_items`` /
    ``pass_rate`` / ``mean_*``) and merges the percentile + count extras into
    the ``aggregate_metrics`` JSONB (preserving any keys already there). Pure
    apart from mutating the passed-in run object — no session, no flush; the
    caller (``run_eval``) owns the transaction.
    """
    run.total_items = metrics.total_items
    run.passed_items = metrics.passed_items
    run.pass_rate = metrics.pass_rate
    run.mean_latency_ms = metrics.mean_latency_ms
    run.mean_tokens = metrics.mean_tokens
    run.mean_cost_usd = metrics.mean_cost_usd
    merged = dict(run.aggregate_metrics or {})
    merged.update(metrics.to_aggregate_metrics())
    run.aggregate_metrics = merged


# =============================================================================
# Internals
# =============================================================================
def _quantize(value: Decimal, quant: str | Decimal) -> Decimal:
    return value.quantize(Decimal(quant), rounding=ROUND_HALF_UP)


def _to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


__all__ = [
    "RunMetrics",
    "apply_to_run",
    "compute_run_metrics",
    "mean",
    "pass_rate",
    "percentile",
]
