"""The agent loop nudges itself off a research rut (regression 2026-06-27).

A claude_sdk run burned all 25 iterations on reads/searches (list_files 11 times,
with repeated dirs, plus rag_search 9 times) and wrote NOTHING. `reflect` injects guidance
into the working context — on a repeated research call or a long research-only
streak — pushing the model to produce the deliverable.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import (
    _PATH_CHURN_THRESHOLD,
    _RESEARCH_STREAK_LIMIT,
    AgentDeps,
    _AgentLoop,
    _path_churn_nudge,
    _repetition_nudge,
    _research_nudge,
)
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.safeguards import Budgets, SafeguardTracker


def test_nudge_on_repeated_research_tool() -> None:
    msg = _research_nudge(tool="list_files", research_streak=1, repeat_count=3)
    assert msg is not None and "list_files" in msg and "Do not repeat" in msg


def test_nudge_on_long_research_streak() -> None:
    msg = _research_nudge(tool="rag_search", research_streak=_RESEARCH_STREAK_LIMIT, repeat_count=1)
    assert msg is not None and "STOP researching" in msg


def test_no_nudge_for_normal_research() -> None:
    assert _research_nudge(tool="list_files", research_streak=2, repeat_count=1) is None


def test_no_nudge_for_producing_tool() -> None:
    assert _research_nudge(tool="write_file", research_streak=0, repeat_count=1) is None


# --- B1: the repetition nudge fires by tool class at the detector threshold ----
def test_repetition_nudge_fires_at_threshold_for_mutator() -> None:
    # threshold=3 → a write seen 3 times warns on the turn BEFORE the 4th aborts.
    msg = _repetition_nudge(tool="write_file", repeat_count=3, threshold=3, has_produced=True)
    assert msg is not None
    assert "write_file" in msg and "submit_result" in msg  # producer wording → FINISH


def test_repetition_nudge_not_before_threshold() -> None:
    nudge = _repetition_nudge(tool="write_file", repeat_count=2, threshold=3, has_produced=True)
    assert nudge is None


def test_repetition_nudge_readonly_wording() -> None:
    msg = _repetition_nudge(tool="read_file", repeat_count=3, threshold=3, has_produced=False)
    assert msg is not None
    assert "read_file" in msg and "result you already have" in msg
    assert "submit_result" not in msg  # read-only → reuse, NOT finish


def test_repetition_nudge_namespaced_mutator() -> None:
    # An MCP/custom writer (namespaced) still classifies as a mutator → producer wording.
    msg = _repetition_nudge(tool="fs.write_file", repeat_count=4, threshold=3, has_produced=True)
    assert msg is not None and "write_file" in msg and "submit_result" in msg


def test_repetition_nudge_none_for_no_tool() -> None:
    assert _repetition_nudge(tool=None, repeat_count=9, threshold=3, has_produced=True) is None


def _loop() -> _AgentLoop:
    # reflect() never touches deps.model, so a dummy object is fine.
    return _AgentLoop(AgentDeps(model=object()), SafeguardTracker(Budgets()), LoopDetector())  # type: ignore[arg-type]


def _state(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "last_observation": {"tool": tool, "ok": True},
        "last_decision": {"tool": tool, "tool_args": args},
        "steps": [],
    }


def test_reflect_injects_guidance_after_research_streak() -> None:
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(_RESEARCH_STREAK_LIMIT):
        out = loop.reflect(_state("rag_search", {"query": f"q{i}"}))  # vary args → not a repeat
    assert loop.research_streak == _RESEARCH_STREAK_LIMIT
    assert "context" in out and out["context"][0]["role"] == "guidance"
    assert "STOP researching" in out["context"][0]["note"]


def test_reflect_injects_guidance_on_repeat() -> None:
    loop = _loop()
    action = {"tool": "list_files", "args": {"path": "."}}
    loop.detector.record(action)  # seen twice → count_of == 2 in reflect
    loop.detector.record(action)
    out = loop.reflect(_state("list_files", {"path": "."}))
    assert "context" in out and "Do not repeat" in out["context"][0]["note"]


def test_reflect_resets_streak_on_producing_tool() -> None:
    loop = _loop()
    loop.research_streak = 4
    loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert loop.research_streak == 0


def test_reflect_sets_repetition_warning_scalar_not_context() -> None:
    # A write_file repeated to the threshold sets the SCALAR repetition_warning —
    # never `context` (which operator.add would reorder, burying it / breaking
    # context[0] ordering). Record it threshold times so reflect's count_of == 3.
    loop = _loop()
    action = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    for _ in range(loop.detector.threshold):
        loop.detector.record(action)
    out = loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert out.get("repetition_warning") is not None
    assert "submit_result" in out["repetition_warning"]
    assert "context" not in out  # a producing tool emits no research guidance


def test_reflect_no_repetition_warning_below_threshold() -> None:
    loop = _loop()
    action = {"tool": "write_file", "args": {"path": "a.py", "content": "x"}}
    loop.detector.record(action)  # count_of == 1 in reflect, < threshold
    out = loop.reflect(_state("write_file", {"path": "a.py", "content": "x"}))
    assert "repetition_warning" not in out


# --- the over-verification trap: once produced, the nudge says FINISH ----------
def test_finish_nudge_when_already_produced_and_streak() -> None:
    msg = _research_nudge(
        tool="list_files", research_streak=_RESEARCH_STREAK_LIMIT, repeat_count=1, has_produced=True
    )
    # C0 (ADR 0087): the nudge must NOT prescribe "NO tool call" — under the
    # structured-finish contract, FINISH on HTTP providers IS a submit_result tool
    # call. The guidance is provider-neutral: report the result and stop.
    assert msg is not None and "FINISH" in msg
    assert "NO tool call" not in msg and "no tool call" not in msg.lower()


# --- ADR 0089: same-path CHURN nudge (varying content, never byte-identical) ----
def test_path_churn_nudge_fires_at_threshold() -> None:
    msg = _path_churn_nudge(
        path="app/Mig.php", write_count=_PATH_CHURN_THRESHOLD, threshold=_PATH_CHURN_THRESHOLD
    )
    assert msg is not None
    assert "app/Mig.php" in msg and "FINISH" in msg and "submit_result" in msg


def test_path_churn_nudge_not_before_threshold() -> None:
    assert (
        _path_churn_nudge(
            path="a.php", write_count=_PATH_CHURN_THRESHOLD - 1, threshold=_PATH_CHURN_THRESHOLD
        )
        is None
    )


def test_path_churn_nudge_none_without_path() -> None:
    assert _path_churn_nudge(path=None, write_count=99, threshold=_PATH_CHURN_THRESHOLD) is None


def test_reflect_churn_nudge_on_repeated_same_path_varying_content() -> None:
    # The model re-writes the SAME path with DIFFERENT content each turn: the loop
    # detector NEVER trips (content-aware fingerprint) and the identical-args nudge
    # never fires (count_of stays 1) — but the path-churn nudge does, pushing it to
    # FINISH. This is exactly the case that burned 50 iterations re-writing a migration.
    loop = _loop()
    out: dict[str, Any] = {}
    for i in range(_PATH_CHURN_THRESHOLD):
        out = loop.reflect(
            _state("write_file", {"path": "app/Mig.php", "content": f"<?php // v{i}"})
        )
    assert loop.path_write_counts["app/Mig.php"] == _PATH_CHURN_THRESHOLD
    assert out.get("repetition_warning") is not None
    assert "app/Mig.php" in out["repetition_warning"] and "FINISH" in out["repetition_warning"]
    # The detector did NOT count these as a loop (distinct content → distinct fingerprint).
    assert (
        loop.detector.count_of(
            {"tool": "write_file", "args": {"path": "app/Mig.php", "content": "<?php // v0"}}
        )
        <= 1
    )


def test_finish_nudge_on_repeat_after_producing() -> None:
    msg = _research_nudge(tool="read_file", research_streak=1, repeat_count=3, has_produced=True)
    assert msg is not None and "FINISH" in msg


def test_reflect_latches_has_produced_and_nudges_to_finish() -> None:
    loop = _loop()
    # Produce once → latches has_produced (and resets the streak).
    loop.reflect(_state("write_file", {"path": "a.php", "content": "x"}))
    assert loop.has_produced is True and loop.research_streak == 0
    # Then it slips back into verifying; after the streak the nudge pushes FINISH.
    out: dict[str, Any] = {}
    for i in range(_RESEARCH_STREAK_LIMIT):
        out = loop.reflect(_state("list_files", {"path": f"dir{i}"}))
    assert "context" in out and "FINISH" in out["context"][0]["note"]
