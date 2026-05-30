"""Operational tunables for the eval subsystem (Plan 14 Fase C).

These are the named defaults the CI merge-gate (task_14_07 / task_14_08),
the shadow-eval sampler (task_14_09) and the drift detector (task_14_10)
read so the policy is in ONE place instead of magic numbers sprinkled
through workflows and call sites. They are *defaults*: every one is meant
to be operator-configurable (env / project config) per the project-config
principle — a deployment overrides the default without touching code.

Kept dependency-light (only ``Decimal``) so the pure, pytest-able functions
that consume them (the merge-gate decision in :mod:`api_server.evals.ci_run`)
import them without dragging in pydantic / a DB session.
"""

from __future__ import annotations

from decimal import Decimal

# --- Merge-gate (CI regression block — task_14_07 / task_14_08) -------------
# How much the pass rate may drop between the baseline run and the candidate
# run before the diff verdict counts as a REGRESSION that blocks a merge.
# A fraction in [0, 1] of the pass rate. Default ``0`` means "any drop is a
# regression" (the strictest, safest default); an operator loosens it (e.g.
# ``0.05`` tolerates a 5-point dip) via the CLI ``--regression-threshold``
# flag or the env override below.
DEFAULT_PASS_RATE_REGRESSION_THRESHOLD: Decimal = Decimal("0")

# Env var the CI workflow / operator sets to override the threshold above
# without editing the workflow. Read by the CLI entrypoint as a fallback
# when ``--regression-threshold`` is not passed.
REGRESSION_THRESHOLD_ENV_VAR = "EVAL_REGRESSION_THRESHOLD"

# --- Shadow evals (task_14_09) ----------------------------------------------
# Fraction of real, completed tasks replayed through the shadow reviewer.
# Shadow evals NEVER block real execution (Plan 14 Decisiones Clave) — this
# only controls how large the background sample is. Default 5% per the plan.
DEFAULT_SHADOW_SAMPLE_RATE: Decimal = Decimal("0.05")

# Env var the operator sets to override the shadow sample rate above without
# editing code. Read by :func:`api_server.evals.shadow.resolve_sample_rate` as
# a fallback when an explicit rate is not passed.
SHADOW_SAMPLE_RATE_ENV_VAR = "EVAL_SHADOW_SAMPLE_RATE"

# --- Drift detection (task_14_10) -------------------------------------------
# A drift alert needs a SUSTAINED decline, not a single dip: at least this
# many consecutive windows must each fall by at least the drop threshold
# below before an alert fires.
DEFAULT_DRIFT_WINDOW: int = 3
# Minimum per-window pass-rate drop (fraction in [0, 1]) that counts toward a
# sustained decline.
DEFAULT_DRIFT_DROP_THRESHOLD: Decimal = Decimal("0.1")


__all__ = [
    "DEFAULT_DRIFT_DROP_THRESHOLD",
    "DEFAULT_DRIFT_WINDOW",
    "DEFAULT_PASS_RATE_REGRESSION_THRESHOLD",
    "DEFAULT_SHADOW_SAMPLE_RATE",
    "REGRESSION_THRESHOLD_ENV_VAR",
    "SHADOW_SAMPLE_RATE_ENV_VAR",
]
