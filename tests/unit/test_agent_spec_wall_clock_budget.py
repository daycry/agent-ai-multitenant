"""_agent_spec aligns the agent-loop wall-clock budget with the container budget
(regression 2026-06-26).

A slow ``claude_sdk`` run was aborted by ``max_wall_clock_exceeded`` — the agent
loop's internal 600s default fired long before the (raised) 3600s container
budget. The worker now injects ``max_wall_clock_s`` into the spec so the two
align; an operator-supplied value always wins.
"""

from __future__ import annotations

from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec


def _request(budgets: dict | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "claude_sdk"},
        budgets=budgets,
    )


def test_wall_clock_budget_injected_when_absent() -> None:
    spec = _agent_spec(_request(), None, wall_clock_budget_s=3600)
    assert spec["budgets"]["max_wall_clock_s"] == 3600.0


def test_operator_budget_wins_over_injected() -> None:
    spec = _agent_spec(_request({"max_wall_clock_s": 900}), None, wall_clock_budget_s=3600)
    assert spec["budgets"]["max_wall_clock_s"] == 900


def test_no_budgets_key_when_nothing_to_set() -> None:
    spec = _agent_spec(_request(), None, wall_clock_budget_s=None)
    assert "budgets" not in spec


def test_max_iterations_injected_when_absent() -> None:
    spec = _agent_spec(_request(), None, max_iterations_budget=50)
    assert spec["budgets"]["max_iterations"] == 50


def test_operator_max_iterations_wins_over_injected() -> None:
    spec = _agent_spec(_request({"max_iterations": 12}), None, max_iterations_budget=50)
    assert spec["budgets"]["max_iterations"] == 12


def test_both_budgets_injected_together() -> None:
    spec = _agent_spec(_request(), None, wall_clock_budget_s=7200, max_iterations_budget=50)
    assert spec["budgets"]["max_wall_clock_s"] == 7200.0
    assert spec["budgets"]["max_iterations"] == 50
