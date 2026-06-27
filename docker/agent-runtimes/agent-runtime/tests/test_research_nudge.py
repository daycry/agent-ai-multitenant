"""The agent loop nudges itself off a research rut (regression 2026-06-27).

A claude_sdk run burned all 25 iterations on reads/searches (list_files 11 times,
with repeated dirs, plus rag_search 9 times) and wrote NOTHING. `reflect` injects guidance
into the working context — on a repeated research call or a long research-only
streak — pushing the model to produce the deliverable.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import (
    _RESEARCH_STREAK_LIMIT,
    AgentDeps,
    _AgentLoop,
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
