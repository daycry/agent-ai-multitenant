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


# --- auditoría 2026-07-02: max_tokens por-kind ---------------------------------
# Con la contabilidad de tokens ARREGLADA (F1.4), el default de 100k del runtime
# — calibrado cuando los tokens medían 0 — cortaba runs sanos de claude_sdk a
# ~23 iteraciones (max_tokens_exceeded, observado en vivo en el e2e). El worker
# inyecta ahora un budget por-kind realista, como ya hacía con max_iterations.


def test_max_tokens_injected_when_absent() -> None:
    spec = _agent_spec(_request(), None, max_tokens_budget=500_000)
    assert spec["budgets"]["max_tokens"] == 500_000


def test_operator_max_tokens_wins_over_injected() -> None:
    spec = _agent_spec(_request({"max_tokens": 80_000}), None, max_tokens_budget=500_000)
    assert spec["budgets"]["max_tokens"] == 80_000


def test_settings_expose_per_kind_token_budgets() -> None:
    from workers.config import Settings

    s = Settings()
    implementer = s.agent_max_tokens_for_kind("claude_sdk")
    review = s.agent_max_tokens_for_kind("claude_sdk", is_review=True)
    assert implementer is not None and implementer > 100_000  # > el default roto
    assert review is not None and review < implementer
    assert s.agent_max_tokens_for_kind("ollama") is None  # HTTP: default del runtime
