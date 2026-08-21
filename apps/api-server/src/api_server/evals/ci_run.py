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
  * :func:`inconclusive_gate` — the THIRD outcome (``task_gov_04``): the gate
    was asked to gate and could not measure. Non-zero and distinct from a
    regression, because a gate that reports a pass it did not earn is worse
    than no gate at all — the check goes green and nobody looks again.
  * :func:`build_parser` / :func:`resolve_threshold` — the arg parse and the
    config-vs-flag threshold resolution, also unit-coverable without a live run.

The threshold and other tunables are NAMED CONSTANTS in
:mod:`api_server.evals.constants` (operator-overridable), never magic numbers.
"""

from __future__ import annotations

import argparse
import enum
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from api_server.evals.constants import (
    DEFAULT_PASS_RATE_REGRESSION_THRESHOLD,
    REGRESSION_THRESHOLD_ENV_VAR,
)
from api_server.evals.diff import DiffVerdict, RunDiff

# A seam that turns the resolved CI args into the baseline-vs-candidate diff the
# gate decides over. The real implementation (build the candidate run via the
# judge engine, load the baseline, diff them) needs an LLM provider + a tenant-
# bound session, so it is injected: the workflow wires the live producer when a
# provider secret is present, and task_14_08's test injects a SCRIPTED producer
# that returns a canned diff (NO real LLM, NO DB) so the gate -> exit-code path
# is deterministic and unit-coverable. ``None`` (the default) means "no live diff
# can be produced here": with --dry-run that is the declared no-provider CI path
# (exit 0, nothing claimed); WITHOUT --dry-run it is an INCONCLUSIVE gate (exit
# 2), never a pass — see :class:`GateOutcome`.
#
# NOTE (task_gov_04, 2026-08-19): no production call site wires a live provider
# yet. Seeding the golden dataset and building the candidate run needs an LLM
# provider + a tenant-bound session, which is the remaining body of that task.
# Until then the workflow's live branch reports INCONCLUSIVE — which is the
# truth — instead of a green check that gates nothing.
DiffProvider = Callable[["CiRunArgs"], RunDiff]

# Process exit codes the CI step keys on: 0 = gate passed (merge may proceed),
# 1 = gate failed (regression beyond threshold — block the merge), 2 = the gate
# could NOT measure anything, so it certifies nothing (see GateOutcome below).
EXIT_GATE_PASSED = 0
EXIT_GATE_BLOCKED = 1
EXIT_GATE_INCONCLUSIVE = 2


class GateOutcome(enum.StrEnum):
    """What the merge-gate is able to say about a prompt change.

    Three states, not two, and the third is the point (``task_gov_04``, 2026-08-19):

    * ``PASSED`` — a diff was produced and it does NOT regress beyond the
      threshold. Exit 0, merge may proceed.
    * ``BLOCKED`` — a diff was produced and it REGRESSES. Exit 1, merge blocked.
    * ``INCONCLUSIVE`` — **no diff could be produced**, so nothing was measured
      and the gate certifies nothing. Exit 2: non-zero (it must not read as a
      pass) and DISTINCT from ``BLOCKED`` (the two demand different fixes from
      whoever reads the check — «the prompt got worse» vs «the gate is
      misconfigured»).

    Until this enum existed, :func:`main` returned ``EXIT_GATE_PASSED`` when no
    ``diff_provider`` was wired, with the message «nothing to gate, treating as
    pass». Since NOTHING in the repo wires a live provider, the workflow's live
    branch could only ever take that path: the regression gate was structurally
    incapable of blocking, and it said so in green. The
    :mod:`tests.unit.test_eval_gate_config` docstring carries the reasoning and
    the ADR 0038 precedence argument.

    The one legitimate way to exit 0 without measuring is ``--dry-run``, which
    the *invoker* passes to declare «I am not gating here» (ADR 0038 §3, the
    no-provider CI path). Absence of a provider is not such a declaration.
    """

    PASSED = "passed"
    BLOCKED = "blocked"
    INCONCLUSIVE = "inconclusive"


_EXIT_CODES: dict[GateOutcome, int] = {
    GateOutcome.PASSED: EXIT_GATE_PASSED,
    GateOutcome.BLOCKED: EXIT_GATE_BLOCKED,
    GateOutcome.INCONCLUSIVE: EXIT_GATE_INCONCLUSIVE,
}


@dataclass(frozen=True)
class GateDecision:
    """The merge-gate's verdict over a run diff (PURE result).

    ``outcome`` is what the CI step keys on; ``verdict`` / ``threshold`` /
    ``reason`` are echoed so the CI log and the PR check explain *why* — a
    regression beyond the tolerated threshold, an improvement / no-change that
    is fine to merge, or (``INCONCLUSIVE``) that there was nothing to decide
    over.

    ``verdict`` and ``threshold`` are ``None`` exactly when there was no
    ``RunDiff`` to read them from. Filling them in with ``UNCHANGED`` / the
    default threshold would move the lie into the dataclass.
    """

    outcome: GateOutcome
    verdict: DiffVerdict | None
    threshold: Decimal | None
    reason: str

    @property
    def blocked(self) -> bool:
        """Whether this decision stops the merge — i.e. anything but a pass.

        An inconclusive gate blocks: it has not shown the change to be safe.
        Kept as a derived property so no caller can construct a decision whose
        ``blocked`` bit disagrees with its ``outcome``.
        """
        return self.outcome is not GateOutcome.PASSED

    @property
    def exit_code(self) -> int:
        """The process exit code this decision maps to (0 pass / 1 block / 2 inconclusive)."""
        return _EXIT_CODES[self.outcome]


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
            outcome=GateOutcome.BLOCKED,
            verdict=diff.verdict,
            threshold=threshold,
            reason=(
                f"pass-rate regression (drop {drop}) at or beyond the "
                f"configured threshold {threshold} — merge blocked"
            ),
        )
    return GateDecision(
        outcome=GateOutcome.PASSED,
        verdict=diff.verdict,
        threshold=threshold,
        reason=f"verdict {diff.verdict.value!r} within threshold {threshold} — merge allowed",
    )


def inconclusive_gate(detail: str) -> GateDecision:
    """The gate could not measure anything — PURE, no I/O.

    Used when there is no :class:`~api_server.evals.diff.RunDiff` to decide
    over: the gate has produced no signal, so it can neither allow nor blame.
    It exits :data:`EXIT_GATE_INCONCLUSIVE` (non-zero, and distinct from a
    regression) rather than reporting a pass it did not earn.

    ``detail`` says what was missing; the reason text appends the way to declare
    a non-gating run on purpose, so nobody "fixes" a red check by deleting the
    gate.
    """
    return GateDecision(
        outcome=GateOutcome.INCONCLUSIVE,
        verdict=None,
        threshold=None,
        reason=(
            f"INCONCLUSIVE — {detail}. Nothing was measured, so this run "
            "certifies nothing and does NOT count as a pass. If this invocation "
            "is not meant to gate (no provider secret in this environment), say "
            "so explicitly with --dry-run."
        ),
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


def _non_blank(raw: str) -> str:
    """An argparse ``type`` that rejects an empty / whitespace-only value.

    ``required=True`` is satisfied by ``--dataset ""`` — the flag IS present.
    The workflow's live branch interpolated ``${EVAL_GOLDEN_DATASET}`` without
    ever defining it, so it handed the CLI empty strings and the gate "ran"
    against a dataset that does not exist. Rejecting blanks at parse time turns
    that missing configuration into a loud argparse error (exit 2) instead of a
    run over nothing.
    """
    value = raw.strip()
    if not value:
        raise argparse.ArgumentTypeError(
            "must not be empty — an unset CI variable expands to the empty "
            "string, which would run the gate against nothing"
        )
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
        type=_non_blank,
        help="Identifier (slug/key) of the agent whose prompt changed.",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        type=_non_blank,
        help="Golden dataset id to grade the new prompt version against.",
    )
    parser.add_argument(
        "--baseline-run",
        required=True,
        type=_non_blank,
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


def main(
    argv: Sequence[str] | None = None,
    *,
    diff_provider: DiffProvider | None = None,
) -> int:
    """CLI entrypoint — returns a process exit code (0 pass / 1 block).

    With ``--dry-run`` (the CI path when no LLM provider secret is present) it
    validates the arguments + resolves the threshold and exits ``0`` without
    running the live harness — the workflow skips the harness with a notice in
    that case, so this never needs an LLM key.

    Otherwise it asks ``diff_provider`` for the baseline-vs-candidate
    :class:`~api_server.evals.diff.RunDiff` (the live harness builds the
    candidate run + diffs it against the baseline; this is the LLM/DB-touching
    part, hence injected), applies the PURE :func:`gate_decision` over it and
    returns ``decision.exit_code`` — so a regression beyond the configured
    threshold yields a NON-ZERO exit that fails the CI job and BLOCKS the merge
    (task_14_08).

    When no ``diff_provider`` is supplied and ``--dry-run`` was NOT passed, the
    caller asked for a gate that cannot measure: that is
    :data:`EXIT_GATE_INCONCLUSIVE` (2), NOT a pass. It used to return ``0``
    "exactly as the dry-run path does", on the grounds that shadow evals never
    block a merge — but that decision (ADR 0038 §4) is about *shadow* evals over
    real user tasks, not about the CI merge-gate, whose whole job is to block
    (ADR 0038 §3). Generalising it turned the live branch into a check that
    could only ever say yes. The legitimate way to exit 0 without measuring is
    ``--dry-run``, where the invoker declares it is not gating.

    The threshold the gate uses is the diff's own
    ``pass_rate_regression_threshold`` (the diff already classified the change
    against it), so ``diff_provider`` must build the diff with
    ``args.regression_threshold`` — gate and diff can never disagree.
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
    if diff_provider is None:
        # No live harness wired in this environment, and the caller did NOT pass
        # --dry-run — so it asked for a gate and the gate cannot measure. That
        # is INCONCLUSIVE (exit 2), never a pass: this branch used to return
        # EXIT_GATE_PASSED, which made the workflow's live branch structurally
        # incapable of blocking anything while reporting green (task_gov_04).
        decision = inconclusive_gate("no live diff provider is wired in this environment")
        print(f"[eval-ci] {decision.reason}")  # - CLI user feedback is the point
        return decision.exit_code

    diff = diff_provider(args)
    decision = gate_decision(diff)
    print(f"[eval-ci] {decision.reason}")
    return decision.exit_code


if __name__ == "__main__":  # pragma: no cover - exercised via the module CLI
    raise SystemExit(main())


__all__ = [
    "EXIT_GATE_BLOCKED",
    "EXIT_GATE_INCONCLUSIVE",
    "EXIT_GATE_PASSED",
    "CiRunArgs",
    "DiffProvider",
    "GateDecision",
    "GateOutcome",
    "build_parser",
    "gate_decision",
    "inconclusive_gate",
    "main",
    "parse_args",
    "resolve_threshold",
]
