"""Unit tests for repetitive-loop detection (task_02_14).

`LoopDetector` flags an agent stuck repeating the same action: the
roadmap rule is "misma acción >3 veces aborta", so the 4th identical
action trips it. The graph turns that into an abort with the specific
code `repetitive_loop_detected`.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_runtime.graph import AgentDeps, run_agent
from agent_runtime.loop_detection import DEFAULT_LOOP_THRESHOLD, LoopDetector
from agent_runtime.model import DecisionKind, ModelDecision, ModelResponse, ScriptedModelClient
from agent_runtime.safeguards import Budgets
from agent_runtime.state import STATUS_ABORTED, STATUS_DONE, STATUS_NEEDS_HUMAN_REVIEW

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


# ---------------------------------------------------------------------------
# B2/B3/C: escalate-not-abort when work was produced; read-only exemption;
# the content-aware fingerprint invariant. These drive `run_agent` end to end
# with a fake tool registry (no filesystem) and an absent worktree, so the
# escalation summary falls back to this run's write capture deterministically.
# ---------------------------------------------------------------------------
class _OkResult:
    """A minimal ToolResult stand-in — every call 'succeeds' with no side effect."""

    ok = True
    output = "ok"
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": True, "output": "ok", "error": None}


class _FakeTools:
    """A ToolRegistry stand-in: any tool call succeeds, touching no disk."""

    def call(self, tool: str, args: dict[str, Any]) -> _OkResult:
        return _OkResult()


def _write(content: str, path: str = "app/A.php") -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(
            kind=DecisionKind.ACT, tool="write_file", tool_args={"path": path, "content": content}
        )
    )


def _act(tool: str, **args: object) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(kind=DecisionKind.ACT, tool=tool, tool_args=dict(args))
    )


def _deps(decisions: list[ModelResponse]) -> AgentDeps:
    return AgentDeps(model=ScriptedModelClient(decisions=decisions), tools=_FakeTools())  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _absent_worktree(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    # Point the worktree harvest at a non-existent dir so the escalation summary
    # falls back to this run's in-memory write capture (no filesystem dependency).
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "absent"))


def test_same_path_distinct_content_never_aborts() -> None:
    # REGRESSION (blinds the invariant): the fingerprint includes the FULL args
    # (content), so editing the SAME path with DIFFERENT content every turn — far
    # more than `threshold` times — must NEVER trip the repetitive-loop guard.
    writes = [_write(f"<?php // rev {i}") for i in range(DEFAULT_LOOP_THRESHOLD + 4)]
    finish = ModelResponse(decision=ModelDecision(kind=DecisionKind.FINISH, output="done"))
    result = run_agent(_deps([*writes, finish]), _TASK)
    assert result.status == STATUS_DONE
    assert result.abort_code is None


def test_identical_write_with_production_escalates_with_deliverable() -> None:
    # A model that re-writes the SAME bytes forever AND has produced: the 4th
    # identical write trips the guard, but because work exists the run ESCALATES
    # (needs_human_review, work preserved) instead of a hard abort. The output is a
    # SUMMARY of the deliverable — NOT the looping action's output.
    result = run_agent(_deps([_write("<?php class A {}")]), _TASK)
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == "repetitive_loop_detected"
    assert "app/A.php" in (result.output or "")


def test_sterile_repetition_still_aborts() -> None:
    # A non-producing verb (echo) repeated identically with NOTHING produced stays a
    # hard abort — the escalation gate only fires when work exists.
    result = run_agent(_deps([_act("echo", text="x")]), _TASK)
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "repetitive_loop_detected"


def test_readonly_repetition_does_not_hard_abort() -> None:
    # Tema C: a read-only tool repeated identically must NOT trip the repetitive-loop
    # guard (it cannot corrupt the deliverable); termination is guaranteed by the
    # iteration budget instead, so the run ends on max_iterations, not on the loop.
    budgets = Budgets(max_iterations=6)
    result = run_agent(_deps([_act("read_file", path="a.php")]), _TASK, budgets=budgets)
    assert result.abort_code == "max_iterations_exceeded"
    assert result.abort_code != "repetitive_loop_detected"
    assert result.status == STATUS_ABORTED  # nothing produced → clean abort


def test_varying_content_leaks_to_max_iterations_and_escalates() -> None:
    # B3: a model that VARIES content every turn never trips the loop detector and
    # would leak out via the iteration budget. Because it produced files, that leak
    # ESCALATES (preserving the work) rather than aborting.
    budgets = Budgets(max_iterations=4)
    writes = [_write(f"<?php // turn {i}") for i in range(4)]
    result = run_agent(_deps(writes), _TASK, budgets=budgets)
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == "max_iterations_exceeded"
    assert "app/A.php" in (result.output or "")
