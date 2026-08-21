"""Merge-regression gate (Plan 14 task_14_08) — does an eval-run diff BLOCK a merge?

The gate is the CI's go/no-go over the baseline-vs-candidate eval diff produced
by task_14_06 (:func:`~api_server.evals.diff.diff_runs`): a pass-rate drop beyond
a CONFIGURABLE threshold is a REGRESSION that blocks the merge; an improvement,
no change, or a sub-threshold dip passes. The decision is a PURE function
(:func:`~api_server.evals.ci_run.gate_decision`) and the CLI entrypoint
(:func:`~api_server.evals.ci_run.main`) maps it to a process exit code (0 pass /
non-zero block) — which is what the ``eval-on-prompt-change`` workflow keys on.

NO DB, NO LLM here: the diff is built in-process from unsaved ``EvalResult`` rows
(the same builder the diff's own unit test uses), and the CLI is driven with an
INJECTED scripted ``diff_provider`` that returns a canned diff. We assert:

  * a pass-rate drop beyond the threshold -> BLOCK (gate ``blocked`` + non-zero exit);
  * a drop WITHIN tolerance -> PASS (a configurable threshold flips the decision
    on the SAME diff inputs);
  * an IMPROVED and an UNCHANGED diff -> PASS;
  * the CLI exit code reflects the gate (block -> 1, pass -> 0, and a gate that
    could NOT measure -> 2 INCONCLUSIVE, never 0; ``--dry-run`` short-circuits
    to 0 without a provider, which is the declared no-provider CI path);
  * cross_tenant: a diff is only meaningful within ONE dataset — a cross-dataset
    (hence cross-tenant) run pair is rejected before any verdict, so it can never
    reach the gate.

``domain`` is imported so the eval ORM's cross-module FK targets are registered
with the mapper registry before we instantiate EvalRun / EvalResult.
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
    EXIT_GATE_INCONCLUSIVE,
    EXIT_GATE_PASSED,
    CiRunArgs,
    gate_decision,
    main,
)
from api_server.evals.constants import DEFAULT_PASS_RATE_REGRESSION_THRESHOLD
from api_server.evals.diff import (
    DatasetMismatchError,
    DiffVerdict,
    RunDiff,
    diff_runs,
)

pytestmark = pytest.mark.integration

_PASS = EvalResultVerdict.PASS.value
_FAIL = EvalResultVerdict.FAIL.value


# ---------------------------------------------------------------------------
# Builders — unsaved ORM rows + a diff over them (mirrors test_eval_diff.py)
# ---------------------------------------------------------------------------
def _result(*, item_id: UUID, verdict: str) -> EvalResult:
    return EvalResult(item_id=item_id, verdict=verdict)


def _run(dataset_id: UUID) -> EvalRun:
    return EvalRun(id=uuid4(), dataset_id=dataset_id)


def _diff_with_pass_rate_drop(
    *,
    threshold: Decimal = DEFAULT_PASS_RATE_REGRESSION_THRESHOLD,
) -> RunDiff:
    """A diff where the candidate drops one of two passes (pass rate 1.0 -> 0.5).

    The 0.5 drop is classified against ``threshold``: with the default 0 (any
    drop regresses) it is a REGRESSION; a threshold > 0.5 tolerates it.
    """
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    base_results = [_result(item_id=i1, verdict=_PASS), _result(item_id=i2, verdict=_PASS)]
    candidate_results = [_result(item_id=i1, verdict=_PASS), _result(item_id=i2, verdict=_FAIL)]
    return diff_runs(
        base,
        candidate,
        base_results,
        candidate_results,
        pass_rate_regression_threshold=threshold,
    )


def _diff_improved() -> RunDiff:
    """A diff where the candidate gains a pass (pass rate 0.5 -> 1.0) -> IMPROVED."""
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    base_results = [_result(item_id=i1, verdict=_PASS), _result(item_id=i2, verdict=_FAIL)]
    candidate_results = [_result(item_id=i1, verdict=_PASS), _result(item_id=i2, verdict=_PASS)]
    return diff_runs(base, candidate, base_results, candidate_results)


def _diff_unchanged() -> RunDiff:
    """A diff of identical result sets (same pass rate) -> UNCHANGED."""
    dataset = uuid4()
    i1, i2 = uuid4(), uuid4()
    base = _run(dataset)
    candidate = _run(dataset)
    rows = [_result(item_id=i1, verdict=_PASS), _result(item_id=i2, verdict=_FAIL)]
    return diff_runs(base, candidate, list(rows), list(rows))


def _args(threshold: Decimal) -> CiRunArgs:
    return CiRunArgs(
        agent="changed-prompt-agent",
        dataset=str(uuid4()),
        baseline_run=str(uuid4()),
        regression_threshold=threshold,
        dry_run=False,
    )


# ===========================================================================
# Pure gate decision over a diff
# ===========================================================================
def test_pass_rate_drop_beyond_threshold_blocks() -> None:
    # Default threshold 0 -> a 0.5 pass-rate drop is a REGRESSION -> BLOCK.
    diff = _diff_with_pass_rate_drop()
    assert diff.verdict is DiffVerdict.REGRESSED

    decision = gate_decision(diff)

    assert decision.blocked is True
    assert decision.verdict is DiffVerdict.REGRESSED
    assert decision.exit_code == EXIT_GATE_BLOCKED
    assert decision.exit_code != 0
    # The decision echoes the policy that produced it (no gate/diff disagreement).
    assert decision.threshold == DEFAULT_PASS_RATE_REGRESSION_THRESHOLD


def test_pass_rate_drop_within_tolerance_passes() -> None:
    # SAME 0.5 drop, but a 0.6 tolerance -> sub-threshold -> NOT a regression.
    diff = _diff_with_pass_rate_drop(threshold=Decimal("0.6"))
    assert diff.verdict is DiffVerdict.UNCHANGED

    decision = gate_decision(diff)

    assert decision.blocked is False
    assert decision.exit_code == EXIT_GATE_PASSED


def test_threshold_is_configurable_and_flips_the_decision() -> None:
    # The threshold is the only thing that changes between these two — the SAME
    # 0.5 pass-rate drop blocks under the strict default but passes under a
    # looser, operator-configured tolerance.
    strict = gate_decision(_diff_with_pass_rate_drop(threshold=Decimal("0")))
    loose = gate_decision(_diff_with_pass_rate_drop(threshold=Decimal("0.6")))

    assert strict.blocked is True
    assert loose.blocked is False
    assert strict.exit_code != loose.exit_code


def test_drop_exactly_at_threshold_blocks() -> None:
    # Boundary: a drop EQUAL to the threshold is "at or beyond" -> a regression.
    diff = _diff_with_pass_rate_drop(threshold=Decimal("0.5"))
    assert diff.verdict is DiffVerdict.REGRESSED
    assert gate_decision(diff).blocked is True


def test_improved_diff_passes() -> None:
    diff = _diff_improved()
    assert diff.verdict is DiffVerdict.IMPROVED

    decision = gate_decision(diff)

    assert decision.blocked is False
    assert decision.exit_code == EXIT_GATE_PASSED


def test_unchanged_diff_passes() -> None:
    diff = _diff_unchanged()
    assert diff.verdict is DiffVerdict.UNCHANGED
    assert gate_decision(diff).blocked is False


# ===========================================================================
# CLI exit code reflects the gate
# ===========================================================================
def test_cli_exit_code_blocks_on_regression() -> None:
    # A regressed diff fed through the CLI yields a NON-ZERO exit (fails the CI
    # job, blocks the merge). The diff producer is injected — no live LLM run.
    exit_code = main(
        ["--agent", "a", "--dataset", "d", "--baseline-run", "b"],
        diff_provider=lambda _args: _diff_with_pass_rate_drop(),
    )
    assert exit_code == EXIT_GATE_BLOCKED
    assert exit_code != 0


def test_cli_exit_code_passes_on_improvement() -> None:
    exit_code = main(
        ["--agent", "a", "--dataset", "d", "--baseline-run", "b"],
        diff_provider=lambda _args: _diff_improved(),
    )
    assert exit_code == EXIT_GATE_PASSED


def test_cli_uses_resolved_threshold_for_the_diff() -> None:
    # The CLI's --regression-threshold flows to the provider via CiRunArgs, so a
    # looser configured threshold turns the SAME inputs from block into pass.
    captured: list[Decimal] = []

    def provider(args: CiRunArgs) -> RunDiff:
        captured.append(args.regression_threshold)
        return _diff_with_pass_rate_drop(threshold=args.regression_threshold)

    blocked = main(
        ["--agent", "a", "--dataset", "d", "--baseline-run", "b", "--regression-threshold", "0"],
        diff_provider=provider,
    )
    passed = main(
        ["--agent", "a", "--dataset", "d", "--baseline-run", "b", "--regression-threshold", "0.6"],
        diff_provider=provider,
    )

    assert captured == [Decimal("0"), Decimal("0.6")]
    assert blocked == EXIT_GATE_BLOCKED
    assert passed == EXIT_GATE_PASSED


def test_cli_dry_run_passes_without_a_provider() -> None:
    # The CI path with no LLM provider secret: --dry-run validates config and
    # exits 0 without ever calling a provider (a shadow/CI eval never blocks a
    # merge when it cannot produce a diff).
    exit_code = main(["--agent", "a", "--dataset", "d", "--baseline-run", "b", "--dry-run"])
    assert exit_code == EXIT_GATE_PASSED


def test_cli_without_provider_is_inconclusive_never_a_pass() -> None:
    """Sin `--dry-run` y sin productor: INCONCLUSIVE (exit 2), nunca PASS.

    **Este test afirmaba lo contrario** (`test_cli_without_provider_does_not_block`,
    «no regression signal to act on -> do NOT block»), y por eso se reescribe en
    vez de adaptarse: era la trampa nº2 de
    `docs/03-guides/verificar-antes-de-implementar.md` —un test que documenta el
    comportamiento observado sin preguntarse si es el correcto convierte el fallo
    en contrato y encima lo protege de futuros arreglos—. Aqui protegia el
    unico camino que la rama viva del workflow podia tomar, porque en el repo no
    hay ni un productor de diff en produccion. O sea que el merge-gate de
    regresion no podia bloquear NADA, en verde.

    Su razonamiento («que el gate falle debe significar una regresion de verdad»)
    era bueno y se conserva: por eso el codigo de salida NO es el 1 de una
    regresion, es un 2 propio. Lo que no se sostiene es el salto de ahi a «luego
    sale 0»: entre «hay regresion» y «no hay regresion» falta «no lo se», que es
    lo que pasa cuando no se ha medido.

    La forma legitima de salir en 0 sin medir sigue siendo `--dry-run`
    (ADR 0038, decision 3), que es una declaracion explicita del invocante y
    tiene su propio test justo debajo.
    """
    exit_code = main(["--agent", "a", "--dataset", "d", "--baseline-run", "b"])
    assert exit_code == EXIT_GATE_INCONCLUSIVE
    assert exit_code != EXIT_GATE_PASSED
    assert exit_code != EXIT_GATE_BLOCKED


# ===========================================================================
# Cross-tenant: a diff is only ever within ONE dataset/tenant
# ===========================================================================
@pytest.mark.cross_tenant
def test_cross_dataset_runs_never_reach_the_gate() -> None:
    # Datasets are tenant-owned (RLS): runs of DIFFERENT datasets belong to
    # different tenants' golden sets. ``diff_runs`` rejects such a pair BEFORE
    # producing any verdict, so a cross-tenant comparison can never feed the
    # merge-gate (the gate only ever decides over a within-tenant diff).
    base = _run(uuid4())
    candidate = _run(uuid4())  # different dataset == different tenant scope
    with pytest.raises(DatasetMismatchError):
        diff_runs(base, candidate, [], [])
