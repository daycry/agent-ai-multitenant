"""Unit tests for repetitive-loop detection (task_02_14).

`LoopDetector` flags an agent stuck repeating the same action: the
roadmap rule is "misma acción >3 veces aborta", so the 4th identical
action trips it. The graph turns that into an abort with the specific
code `repetitive_loop_detected`.
"""

from __future__ import annotations

import pytest
from agent_runtime.graph import AgentDeps, run_agent
from agent_runtime.loop_detection import DEFAULT_LOOP_THRESHOLD, LoopDetector
from agent_runtime.model import DecisionKind, ModelDecision, ModelResponse, ScriptedModelClient
from agent_runtime.state import STATUS_ABORTED, STATUS_DONE

pytestmark = pytest.mark.unit

_TASK = {"id": "t-loop", "title": "Loopy task", "description": "exercises loop detection"}


def _action(tool: str = "echo", **args: object) -> dict[str, object]:
    return {"tool": tool, "args": dict(args)}


# ---------------------------------------------------------------------------
# LoopDetector
# ---------------------------------------------------------------------------
def test_default_threshold_is_three() -> None:
    assert LoopDetector().threshold == DEFAULT_LOOP_THRESHOLD == 3


def test_same_action_within_threshold_is_not_a_loop() -> None:
    detector = LoopDetector()
    action = _action(text="x")
    # First three identical actions are tolerated.
    assert [detector.record(action) for _ in range(3)] == [False, False, False]


def test_fourth_identical_action_is_a_loop() -> None:
    detector = LoopDetector()
    action = _action(text="x")
    for _ in range(3):
        detector.record(action)
    # The 4th occurrence — strictly more than the threshold — trips it.
    assert detector.record(action) is True


def test_distinct_actions_never_trip() -> None:
    detector = LoopDetector()
    results = [detector.record(_action(text=f"step-{i}")) for i in range(10)]
    assert not any(results)


def test_only_the_repeated_action_trips() -> None:
    detector = LoopDetector()
    repeated = _action(text="same")
    # Interleave a repeated action with unique ones; only the repeated
    # one should ever reach the threshold.
    tripped = []
    for i in range(4):
        detector.record(_action(text=f"unique-{i}"))
        tripped.append(detector.record(repeated))
    assert tripped == [False, False, False, True]


def test_count_of_reports_occurrences() -> None:
    detector = LoopDetector()
    action = _action(text="x")
    detector.record(action)
    detector.record(action)
    assert detector.count_of(action) == 2
    assert detector.count_of(_action(text="other")) == 0


def test_total_actions_counts_every_record() -> None:
    detector = LoopDetector()
    for i in range(5):
        detector.record(_action(text=f"a-{i}"))
    assert detector.total_actions == 5


def test_threshold_is_configurable() -> None:
    detector = LoopDetector(threshold=1)
    action = _action(text="x")
    assert detector.record(action) is False  # 1st — within threshold
    assert detector.record(action) is True  # 2nd — exceeds threshold 1


def test_fingerprint_is_independent_of_argument_order() -> None:
    detector = LoopDetector()
    # Same action, keys written in a different order — must be one loop.
    a = {"tool": "echo", "args": {"a": 1, "b": 2}}
    b = {"tool": "echo", "args": {"b": 2, "a": 1}}
    detector.record(a)
    detector.record(b)
    detector.record(a)
    assert detector.record(b) is True


# ---------------------------------------------------------------------------
# The loop aborts with the specific code
# ---------------------------------------------------------------------------
def _repeating_act() -> AgentDeps:
    """A model that proposes the same action forever — never finishes."""
    decision = ModelResponse(
        decision=ModelDecision(kind=DecisionKind.ACT, tool="echo", tool_args={"text": "x"})
    )
    return AgentDeps(model=ScriptedModelClient(decisions=[decision]))


def _distinct_then_finish() -> AgentDeps:
    """Three different actions, then a finish — no repetition."""
    acts = [
        ModelResponse(
            decision=ModelDecision(kind=DecisionKind.ACT, tool="echo", tool_args={"text": label})
        )
        for label in ("a", "b", "c")
    ]
    finish = ModelResponse(decision=ModelDecision(kind=DecisionKind.FINISH, output="done"))
    return AgentDeps(model=ScriptedModelClient(decisions=[*acts, finish]))


def test_repeating_model_aborts_with_loop_code() -> None:
    result = run_agent(_repeating_act(), _TASK)
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "repetitive_loop_detected"


def test_distinct_actions_do_not_trigger_loop_detection() -> None:
    result = run_agent(_distinct_then_finish(), _TASK)
    assert result.status == STATUS_DONE
    assert result.abort_code is None
