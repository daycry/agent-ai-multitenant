"""Unit test — prod-06 task_prod06_budget_02.

The per-run budget envelope resolves platform-default ← project-override, with
every key CLAMPED to the runtime ceiling (the agent-runtime ``Budgets`` dataclass
defaults). A project can tighten a budget but never loosen it past the platform's
hard envelope; unknown/garbage keys are dropped; nothing-to-override returns None
so the worker emits no ``budgets`` key and the runtime uses its own defaults.
"""

from __future__ import annotations

import pytest
from api_server.budgets import EXECUTION_BUDGET_CEILING, resolve_execution_budgets

pytestmark = pytest.mark.unit


def test_no_override_returns_none() -> None:
    assert resolve_execution_budgets(platform_default=None, project_override=None) is None
    assert resolve_execution_budgets(platform_default={}, project_override={}) is None


def test_project_override_wins_over_platform_default() -> None:
    out = resolve_execution_budgets(
        platform_default={"max_tokens": 80_000, "max_cost_usd": 4.0},
        project_override={"max_cost_usd": 2.0},
    )
    assert out == {"max_tokens": 80_000, "max_cost_usd": 2.0}


def test_values_above_ceiling_are_clamped() -> None:
    out = resolve_execution_budgets(
        platform_default=None,
        project_override={"max_tokens": 999_999, "max_cost_usd": 1000.0},
    )
    # Clamped down to the runtime ceiling — a project can never loosen it.
    assert out == {
        "max_tokens": EXECUTION_BUDGET_CEILING["max_tokens"],
        "max_cost_usd": EXECUTION_BUDGET_CEILING["max_cost_usd"],
    }


def test_lower_values_pass_through() -> None:
    out = resolve_execution_budgets(
        platform_default=None,
        project_override={"max_iterations": 5, "max_wall_clock_s": 120.0},
    )
    assert out == {"max_iterations": 5, "max_wall_clock_s": 120.0}


def test_unknown_and_garbage_keys_dropped() -> None:
    out = resolve_execution_budgets(
        platform_default=None,
        project_override={
            "max_review_retries": 99,  # hard platform limit — NOT a per-project budget
            "nonsense": 1,
            "max_tokens": "lots",  # non-numeric
            "max_cost_usd": -5,  # non-positive
            "max_tool_calls": True,  # bool is not a real count
            "max_iterations": 10,  # the one good value
        },
    )
    assert out == {"max_iterations": 10}


def test_int_keys_stay_int() -> None:
    out = resolve_execution_budgets(
        platform_default=None,
        project_override={"max_tokens": 50_000.0, "max_cost_usd": 3.5},
    )
    assert out is not None
    assert isinstance(out["max_tokens"], int)
    assert out["max_tokens"] == 50_000
    assert isinstance(out["max_cost_usd"], float)


def test_ceiling_excludes_review_retries() -> None:
    # max_review_retries is owned by platform_settings (ADR 0013), never here.
    assert "max_review_retries" not in EXECUTION_BUDGET_CEILING


# --- prod-06 A2 (auditoría 2026-07-06): el techo no debe estrangular los
# presupuestos por-kind legítimos de claude_sdk (500k tokens / 50 iter / 7200s).
# Si el operador (o el platform-default) fija esos valores, resolve_execution_budgets
# NO debe clamparlos por debajo — antes el techo era 100k/25/600 y los pisaba,
# reviviendo el corte a ~23 iteraciones que arregló la remediación 07c91cc.
_CLAUDE_SDK_IMPLEMENTER_BUDGET = {
    "max_tokens": 500_000,
    "max_iterations": 50,
    "max_wall_clock_s": 7200.0,
}


def test_ceiling_admits_claude_sdk_per_kind_budgets() -> None:
    for key, legit in _CLAUDE_SDK_IMPLEMENTER_BUDGET.items():
        assert EXECUTION_BUDGET_CEILING[key] >= legit, (
            f"el techo de {key} ({EXECUTION_BUDGET_CEILING[key]}) estrangula el "
            f"presupuesto por-kind legítimo de claude_sdk ({legit})"
        )


def test_operator_can_set_claude_sdk_sized_budgets() -> None:
    out = resolve_execution_budgets(
        platform_default=None,
        project_override=dict(_CLAUDE_SDK_IMPLEMENTER_BUDGET),
    )
    assert out == {
        "max_tokens": 500_000,
        "max_iterations": 50,
        "max_wall_clock_s": 7200.0,
    }


def test_absurd_values_still_clamped_after_raise() -> None:
    out = resolve_execution_budgets(
        platform_default=None,
        project_override={"max_tokens": 10_000_000, "max_iterations": 9_999},
    )
    assert out == {
        "max_tokens": EXECUTION_BUDGET_CEILING["max_tokens"],
        "max_iterations": EXECUTION_BUDGET_CEILING["max_iterations"],
    }
