"""Eval-on-prompt-change CI entrypoint + merge-gate decision (Plan 14 task_14_07).

When a PR/push changes an agent prompt definition, CI runs the eval harness:
it builds (or refreshes) the golden-dataset run for the NEW prompt version,
diffs it against the BASELINE run (task_14_06's :func:`~api_server.evals.diff.diff_runs`)
and asks the only question the merge-gate cares about — *did this change make
the agent worse beyond the tolerated threshold?* If so, the gate FAILS and the
merge is blocked (task_14_08).

This module is invoked as ``python -m api_server.evals.ci_run`` from the
``.github/workflows/eval-on-prompt-change.yml`` workflow. CI has no LLM keys by
default, so the *workflow* gates the live harness on provider secrets being
present; this CLI focuses on the part that is deterministic and unit-testable:

  * :func:`gate_decision` — the PURE merge-gate decision over a
    :class:`~api_server.evals.diff.RunDiff`: ``REGRESSED`` (and the verdict was
    computed against a threshold) blocks; ``IMPROVED`` / ``UNCHANGED`` pass. No
    LLM, no DB, no I/O — directly pytest-able (and reused by task_14_08).
  * :func:`build_parser` / :func:`resolve_threshold` — the arg parse and the
    config-vs-flag threshold resolution, also unit-coverable without a live run.

The threshold and other tunables are NAMED CONSTANTS in
:mod:`api_server.evals.constants` (operator-overridable), never magic numbers.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from api_server.evals.constants import (
    DEFAULT_PASS_RATE_REGRESSION_THRESHOLD,
    REGRESSION_THRESHOLD_ENV_VAR,
)
from api_server.evals.diff import DiffVerdict, RunDiff

# Process exit codes the CI step keys on: 0 = gate passed (merge may proceed),
# 1 = gate failed (regression beyond threshold — block the merge).
EXIT_GATE_PASSED = 0
EXIT_GATE_BLOCKED = 1


@dataclass(frozen=True)
class GateDecision:
    """The merge-gate's verdict over a run diff (PURE result).

    ``blocked`` is the single bit the CI step acts on (block the merge or not);
    ``verdict`` / ``threshold`` / ``reason`` are echoed so the CI log and the
    PR check explain *why* — a regression beyond the tolerated threshold, or an
    improvement / no-change that is fine to merge.
    """

    blocked: bool
    verdict: DiffVerdict
    threshold: Decimal
    reason: str

    @property
    def exit_code(self) -> int:
        """The process exit code this decision maps to (0 pass / 1 block)."""
        return EXIT_GATE_BLOCKED if self.blocked else EXIT_GATE_PASSED


def gate_decision(diff: RunDiff) -> GateDecision:
    """Decide whether ``diff`` blocks a merge — PURE, no I/O.

    The diff has ALREADY classified the change against its
    ``pass_rate_regression_threshold`` (task_14_06): a ``REGRESSED`` verdict
    means the pass rate dropped by at least that threshold, which is exactly
    the merge-gate's block condition. ``IMPROVED`` and ``UNCHANGED`` pass. The
    threshold is echoed from the diff so the gate and the diff can never
    disagree about the policy that produced the verdict.

    This is the function task_14_08's regression-block test exercises — it is
    deterministic and reached without any live eval run or LLM call.
    """
    threshold = diff.pass_rate_regression_threshold
    if diff.verdict is DiffVerdict.REGRESSED:
        delta = diff.pass_rate.delta
        drop = f"{-delta}" if delta is not None else "n/a"
        return GateDecision(
            blocked=True,
            verdict=diff.verdict,
            threshold=threshold,
            reason=(
                f"pass-rate regression (drop {drop}) at or beyond the "
                f"configured threshold {threshold} — merge blocked"
            ),
        )
    return GateDecision(
        blocked=False,
        verdict=diff.verdict,
        threshold=threshold,
        reason=f"verdict {diff.verdict.value!r} within threshold {threshold} — merge allowed",
    )


def resolve_threshold(
    cli_value: str | None,
    *,
    env: dict[str, str] | None = None,
) -> Decimal:
    """Resolve the regression threshold: CLI flag > env var > constant default.

    ``cli_value`` is the raw ``--regression-threshold`` string (``None`` if not
    passed); the :data:`~api_server.evals.constants.REGRESSION_THRESHOLD_ENV_VAR`
    env var is the next fallback, then the
    :data:`~api_server.evals.constants.DEFAULT_PASS_RATE_REGRESSION_THRESHOLD`
    constant. A non-numeric or negative value is a :class:`ValueError` (the
    threshold is a fraction in ``[0, 1]``; the diff layer also rejects ``< 0``).
    """
    source = env if env is not None else dict(os.environ)
    raw = cli_value if cli_value is not None else source.get(REGRESSION_THRESHOLD_ENV_VAR)
    if raw is None or raw == "":
        return DEFAULT_PASS_RATE_REGRESSION_THRESHOLD
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"regression threshold must be numeric, got {raw!r}") from exc
    if value < 0:
        raise ValueError(f"regression threshold must be >= 0, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Build the ``python -m api_server.evals.ci_run`` argument parser.

    Kept tiny + side-effect-free so a unit test can construct it and assert the
    parsed namespace without running the harness. The workflow passes the agent
    whose prompt changed, the dataset to grade against and the baseline run to
    diff the candidate against; ``--regression-threshold`` overrides the
    configured default (otherwise the env var / constant wins).
    """
    parser = argparse.ArgumentParser(
        prog="python -m api_server.evals.ci_run",
        description=(
            "Run the eval harness for a changed agent prompt and apply the "
            "merge-gate over the baseline-vs-candidate diff (Plan 14 Fase C)."
        ),
    )
    parser.add_argument(
        "--agent",
        required=True,
        help="Identifier (slug/key) of the agent whose prompt changed.",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        help="Golden dataset id to grade the new prompt version against.",
    )
    parser.add_argument(
        "--baseline-run",
        required=True,
        help="Eval-run id of the baseline (old prompt) to diff the candidate against.",
    )
    parser.add_argument(
        "--regression-threshold",
        default=None,
        help=(
            "Max tolerated pass-rate drop (fraction in [0, 1]) before the merge "
            "is blocked. Overrides the "
            f"{REGRESSION_THRESHOLD_ENV_VAR} env var and the built-in default "
            f"({DEFAULT_PASS_RATE_REGRESSION_THRESHOLD})."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Parse + resolve config but do not run the live harness (used when "
            "no LLM provider secret is present in CI)."
        ),
    )
    return parser


@dataclass(frozen=True)
class CiRunArgs:
    """The validated, resolved arguments of one CI eval run."""

    agent: str
    dataset: str
    baseline_run: str
    regression_threshold: Decimal
    dry_run: bool


def parse_args(argv: Sequence[str] | None = None) -> CiRunArgs:
    """Parse + validate argv into a typed :class:`CiRunArgs` (no side effects).

    Resolves the regression threshold via :func:`resolve_threshold` (flag > env
    > constant). Raises :class:`SystemExit` (argparse) on a missing required
    flag and :class:`ValueError` on a bad threshold.
    """
    ns = build_parser().parse_args(argv)
    threshold = resolve_threshold(ns.regression_threshold)
    return CiRunArgs(
        agent=ns.agent,
        dataset=ns.dataset,
        baseline_run=ns.baseline_run,
        regression_threshold=threshold,
        dry_run=bool(ns.dry_run),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint — returns a process exit code (0 pass / 1 block).

    With ``--dry-run`` (the CI path when no LLM provider secret is present) it
    validates the arguments + resolves the threshold and exits ``0`` without
    running the live harness — the workflow skips the harness with a notice in
    that case, so this never needs an LLM key. The live-harness wiring (build
    the candidate run, diff against the baseline, then :func:`gate_decision`)
    lands with task_14_08 / task_14_09; this entrypoint already exposes the
    deterministic, unit-coverable surface the workflow calls.
    """
    args = parse_args(argv)
    print(  # - CLI user feedback is the point
        f"[eval-ci] agent={args.agent} dataset={args.dataset} "
        f"baseline_run={args.baseline_run} "
        f"regression_threshold={args.regression_threshold} dry_run={args.dry_run}"
    )
    if args.dry_run:
        print("[eval-ci] dry-run: no LLM provider secret present — harness skipped.")
        return EXIT_GATE_PASSED
    # Live harness (build candidate run + diff + gate) is wired in task_14_08.
    print("[eval-ci] live harness not yet wired (task_14_08); treating as pass.")
    return EXIT_GATE_PASSED


if __name__ == "__main__":  # pragma: no cover - exercised via the module CLI
    raise SystemExit(main())


__all__ = [
    "EXIT_GATE_BLOCKED",
    "EXIT_GATE_PASSED",
    "CiRunArgs",
    "GateDecision",
    "build_parser",
    "gate_decision",
    "main",
    "parse_args",
    "resolve_threshold",
]
