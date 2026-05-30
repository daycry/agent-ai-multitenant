"""Unit tests for the eval-on-prompt-change CI gate (Plan 14 task_14_07).

In-process only — NO DB, NO LLM, NO live CI run (the workflow itself is
checked by actionlint = auto_14_07_a). We pin the two deterministic surfaces
the ``.github/workflows/eval-on-prompt-change.yml`` workflow calls:

  * :func:`~api_server.evals.ci_run.gate_decision` — the PURE merge-gate over a
    :class:`~api_server.evals.diff.RunDiff`: ``REGRESSED`` blocks (exit 1),
    ``IMPROVED`` / ``UNCHANGED`` pass (exit 0). This is the function
    task_14_08's regression-block consumes;
  * :func:`~api_server.evals.ci_run.build_parser` / ``parse_args`` /
    ``resolve_threshold`` — the arg parse + the config-vs-flag-vs-env threshold
    resolution (operator-configurable tunable, never a magic number);
  * :func:`~api_server.evals.ci_run.main` — the ``--dry-run`` path the workflow
    runs when no LLM provider secret is present (exits 0 with no LLM call).

``domain`` is imported so the eval ORM's cross-module FK targets are registered
with the mapper registry before we instantiate EvalRun/EvalResult.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

# Import for FK-target mapper registration (agents / tasks / executions).
from api_server.db import domain as _domain  # noqa: F401
from api_server.db.evals import EvalResult, EvalResultVerdict, EvalRun
from api_server.evals.ci_run import (
    EXIT_GATE_BLOCKED,
    EXIT_GATE_PASSED,
    build_parser,
    gate_decision,
    main,
    parse_args,
    resolve_threshold,
)
from api_server.evals.constants import (
    DEFAULT_PASS_RATE_REGRESSION_THRESHOLD,
    REGRESSION_THRESHOLD_ENV_VAR,
)
from api_server.evals.diff import DiffVerdict, diff_runs

pytestmark = pytest.mark.unit

_PASS = EvalResultVerdict.PASS.value
_FAIL = EvalResultVerdict.FAIL.value


# ---------------------------------------------------------------------------
# Builders — unsaved ORM rows carrying only the fields the diff reads
# ---------------------------------------------------------------------------
def _result(*, item_id: UUID, verdict: str) -> EvalResult:
    return EvalResult(item_id=item_id, verdict=verdict)


def _diff(*, base_verdicts: list[str], cand_verdicts: list[str], threshold: str = "0"):
    dataset = uuid4()
    items = [uuid4() for _ in base_verdicts]
    base = EvalRun(id=uuid4(), dataset_id=dataset)
    candidate = EvalRun(id=uuid4(), dataset_id=dataset)
    base_results = [
        _result(item_id=i, verdict=v) for i, v in zip(items, base_verdicts, strict=False)
    ]
    cand_results = [
        _result(item_id=i, verdict=v) for i, v in zip(items, cand_verdicts, strict=False)
    ]
    return diff_runs(
        base,
        candidate,
        base_results,
        cand_results,
        pass_rate_regression_threshold=Decimal(threshold),
    )


# ---------------------------------------------------------------------------
# gate_decision — the pure merge-gate over a RunDiff
# ---------------------------------------------------------------------------
def test_regressed_diff_blocks_the_merge() -> None:
    # base 2/2 pass -> candidate 1/2 pass: a 0.5 drop, threshold 0 -> REGRESSED.
    diff = _diff(base_verdicts=[_PASS, _PASS], cand_verdicts=[_PASS, _FAIL])
    assert diff.verdict is DiffVerdict.REGRESSED

    decision = gate_decision(diff)
    assert decision.blocked is True
    assert decision.verdict is DiffVerdict.REGRESSED
    assert decision.exit_code == EXIT_GATE_BLOCKED
    assert "blocked" in decision.reason


def test_improved_diff_allows_the_merge() -> None:
    diff = _diff(base_verdicts=[_PASS, _FAIL], cand_verdicts=[_PASS, _PASS])
    assert diff.verdict is DiffVerdict.IMPROVED
    decision = gate_decision(diff)
    assert decision.blocked is False
    assert decision.exit_code == EXIT_GATE_PASSED


def test_unchanged_diff_allows_the_merge() -> None:
    diff = _diff(base_verdicts=[_PASS, _FAIL], cand_verdicts=[_PASS, _FAIL])
    assert diff.verdict is DiffVerdict.UNCHANGED
    decision = gate_decision(diff)
    assert decision.blocked is False
    assert decision.exit_code == EXIT_GATE_PASSED


def test_sub_threshold_drop_is_not_blocked() -> None:
    # A 0.5 drop with a 0.6 tolerance is below threshold -> UNCHANGED -> pass.
    diff = _diff(base_verdicts=[_PASS, _PASS], cand_verdicts=[_PASS, _FAIL], threshold="0.6")
    assert diff.verdict is DiffVerdict.UNCHANGED
    decision = gate_decision(diff)
    assert decision.blocked is False
    # The gate echoes the policy that produced the verdict.
    assert decision.threshold == Decimal("0.6")


def test_drop_at_threshold_blocks() -> None:
    # A 0.5 drop with a 0.5 tolerance is AT the threshold -> REGRESSED (>=).
    diff = _diff(base_verdicts=[_PASS, _PASS], cand_verdicts=[_PASS, _FAIL], threshold="0.5")
    assert diff.verdict is DiffVerdict.REGRESSED
    assert gate_decision(diff).blocked is True


# ---------------------------------------------------------------------------
# Threshold resolution — CLI flag > env var > constant default
# ---------------------------------------------------------------------------
def test_threshold_defaults_to_constant() -> None:
    assert resolve_threshold(None, env={}) == DEFAULT_PASS_RATE_REGRESSION_THRESHOLD


def test_threshold_from_env_var() -> None:
    assert resolve_threshold(None, env={REGRESSION_THRESHOLD_ENV_VAR: "0.05"}) == Decimal("0.05")


def test_cli_flag_overrides_env_var() -> None:
    got = resolve_threshold("0.2", env={REGRESSION_THRESHOLD_ENV_VAR: "0.05"})
    assert got == Decimal("0.2")


def test_empty_threshold_falls_back_to_default() -> None:
    assert resolve_threshold("", env={}) == DEFAULT_PASS_RATE_REGRESSION_THRESHOLD


def test_non_numeric_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_threshold("not-a-number", env={})


def test_negative_threshold_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_threshold("-0.1", env={})


# ---------------------------------------------------------------------------
# Arg parse — the surface the workflow invokes
# ---------------------------------------------------------------------------
def test_parse_args_happy_path() -> None:
    args = parse_args(
        [
            "--agent",
            "backend-dev",
            "--dataset",
            "ds-1",
            "--baseline-run",
            "run-1",
            "--regression-threshold",
            "0.1",
        ]
    )
    assert args.agent == "backend-dev"
    assert args.dataset == "ds-1"
    assert args.baseline_run == "run-1"
    assert args.regression_threshold == Decimal("0.1")
    assert args.dry_run is False


def test_parse_args_dry_run_flag() -> None:
    args = parse_args(["--agent", "qa", "--dataset", "ds", "--baseline-run", "run", "--dry-run"])
    assert args.dry_run is True
    # No flag, no env -> constant default.
    assert args.regression_threshold == DEFAULT_PASS_RATE_REGRESSION_THRESHOLD


def test_parser_requires_agent_dataset_baseline() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])  # missing required flags


# ---------------------------------------------------------------------------
# main() — the dry-run path the workflow runs with no provider secret
# ---------------------------------------------------------------------------
def test_main_dry_run_exits_zero_without_llm() -> None:
    code = main(["--agent", "qa", "--dataset", "ds", "--baseline-run", "run", "--dry-run"])
    assert code == EXIT_GATE_PASSED
