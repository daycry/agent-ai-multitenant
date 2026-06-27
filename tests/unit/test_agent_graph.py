"""Unit tests for the LangGraph agent loop (task_02_10).

Drives the graph with a deterministic `ScriptedModelClient` — no LLM,
no I/O — and asserts the eight-node loop wires up, traverses and
captures correctly.
"""

from __future__ import annotations

import pytest
from agent_runtime.graph import NODE_NAMES, AgentDeps, build_agent_graph, run_agent
from agent_runtime.model import (
    DecisionKind,
    ModelDecision,
    ModelResponse,
    ReviewResponse,
    ScriptedModelClient,
)
from agent_runtime.state import STATUS_DONE, STATUS_NEEDS_HUMAN_REVIEW
from agent_runtime.steps import StepKind

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _act(tool: str = "echo", *, rationale: str = "", **args: object) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(
            kind=DecisionKind.ACT,
            tool=tool,
            tool_args=dict(args),
            rationale=rationale or f"use {tool}",
        ),
        tokens_in=120,
        tokens_out=40,
        cost_usd=0.002,
    )


def _finish(output: str = "done") -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.FINISH, output=output, rationale="finish"),
        tokens_in=80,
        tokens_out=20,
        cost_usd=0.001,
    )


_TASK = {"id": "t-1", "title": "Write a sea poem", "description": "A short poem about the sea."}


def _deps(*decisions: ModelResponse, reviews: list[ReviewResponse] | None = None) -> AgentDeps:
    return AgentDeps(model=ScriptedModelClient(decisions=list(decisions), reviews=reviews or []))


# ---------------------------------------------------------------------------
# Graph shape
# ---------------------------------------------------------------------------
def test_graph_declares_the_eight_loop_nodes() -> None:
    assert NODE_NAMES == (
        "perceive",
        "recall",
        "plan",
        "act",
        "observe",
        "reflect",
        "finalize",
        "self_review",
    )


def test_compiled_graph_contains_every_node() -> None:
    graph = build_agent_graph(_deps(_finish()))
    node_ids = set(graph.get_graph().nodes)
    assert set(NODE_NAMES).issubset(node_ids)


# ---------------------------------------------------------------------------
# A full successful run
# ---------------------------------------------------------------------------
def test_act_then_finish_run_completes() -> None:
    result = run_agent(_deps(_act("echo", text="hi"), _finish("the poem")), _TASK)
    assert result.status == STATUS_DONE
    assert result.succeeded() is True
    assert result.abort_code is None
    assert result.output == "the poem"
    # plan ran twice (one ACT turn, one FINISH turn).
    assert result.iterations == 2


def test_run_visits_every_node() -> None:
    result = run_agent(_deps(_act("echo", text="hi"), _finish("ok")), _TASK)
    visited = {step["node"] for step in result.steps}
    assert visited == set(NODE_NAMES)


def test_run_captures_all_four_step_kinds() -> None:
    result = run_agent(_deps(_act("echo", text="hi"), _finish("ok")), _TASK)
    kinds = {step["kind"] for step in result.steps}
    assert kinds == {
        str(StepKind.NODE),
        str(StepKind.MEMORY_READ),
        str(StepKind.MODEL_CALL),
        str(StepKind.TOOL_CALL),
    }


def test_steps_log_indices_are_contiguous() -> None:
    result = run_agent(_deps(_act("echo", text="hi"), _finish("ok")), _TASK)
    assert [step["index"] for step in result.steps] == list(range(len(result.steps)))


def test_immediate_finish_skips_act_observe_reflect() -> None:
    result = run_agent(_deps(_finish("instant")), _TASK)
    assert result.status == STATUS_DONE
    visited = {step["node"] for step in result.steps}
    assert "act" not in visited
    assert "observe" not in visited
    assert {"perceive", "recall", "plan", "finalize", "self_review"}.issubset(visited)


# ---------------------------------------------------------------------------
# Usage accounting
# ---------------------------------------------------------------------------
def test_usage_totals_reflect_model_and_tool_calls() -> None:
    result = run_agent(_deps(_act("echo", text="hi"), _finish("ok")), _TASK)
    usage = result.usage
    # two plan calls + one self_review call.
    assert usage["model_calls"] == 3
    assert usage["tool_calls"] == 1
    # act turn 160 + finish turn 100 + self-review 0 = 260 tokens.
    assert usage["total_tokens"] == 260
    assert usage["cost_usd"] == pytest.approx(0.003)


# ---------------------------------------------------------------------------
# self_review retry path
# ---------------------------------------------------------------------------
def test_failed_review_retries_then_passes() -> None:
    deps = _deps(
        _finish("v1"),
        reviews=[
            ReviewResponse(passed=False, feedback="needs more"),
            ReviewResponse(passed=True),
        ],
    )
    result = run_agent(deps, _TASK)
    assert result.status == STATUS_DONE
    # one failed review forced a second planning turn.
    assert result.iterations == 2


def test_review_retry_budget_escalates_to_human() -> None:
    # ADR 0087: explicit-fail reviews retried until the budget is exhausted now
    # ESCALATE to a human instead of aborting — the deliverable is preserved for
    # human validation rather than discarded as a hard failure.
    deps = _deps(_finish("v1"), reviews=[ReviewResponse(passed=False, feedback="no")])
    result = run_agent(deps, _TASK)
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == "max_review_retries_exhausted"
    assert result.output == "v1"  # the work is kept for the human reviewer


def test_inconclusive_review_escalates_immediately() -> None:
    # ADR 0087: an inconclusive verdict (untrustworthy) escalates WITHOUT burning
    # retries — retrying an ambiguous review just wastes budget.
    deps = _deps(_finish("v1"), reviews=[ReviewResponse(passed=False, inconclusive=True)])
    result = run_agent(deps, _TASK)
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == "review_inconclusive"
    assert result.iterations == 1  # no retry spent
