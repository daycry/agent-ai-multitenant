"""The self-review sees the ACTUAL produced code (ADR 0087, Option 1).

Root cause of the JWT escalation: `_review_messages` fed the reviewer only the
agent's prose summary ("the files are written"), which it cannot verify — so it
rejected an implementation it never saw and the run escalated. The loop now
harvests the files the agent wrote (path+content, from producing tool-call args)
and injects them into the review state. Analysis/design runs (no produced files)
keep the prose-only review unchanged.

These drive the graph NODES directly (no real write_file) so the capture +
injection are pinned without filesystem side effects.
"""

from __future__ import annotations

from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import ReviewResponse
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import STATUS_RUNNING


class _RecordingModel:
    """A ModelClient whose review() records the state it was handed."""

    def __init__(self, review_result: ReviewResponse) -> None:
        self._review = review_result
        self.seen_state: dict | None = None

    def decide(self, state: dict) -> object:  # noqa: ARG002  # pragma: no cover
        raise AssertionError("decide should not be called in these tests")

    def review(self, state: dict) -> ReviewResponse:
        self.seen_state = state
        return self._review


def _loop(model: _RecordingModel) -> _AgentLoop:
    return _AgentLoop(AgentDeps(model=model), SafeguardTracker(Budgets()), LoopDetector())  # type: ignore[arg-type]


def _produce_state(path: str, content: str) -> dict:
    return {
        "last_observation": {"tool": "write_file", "ok": True},
        "last_decision": {"tool": "write_file", "tool_args": {"path": path, "content": content}},
        "steps": [],
    }


def test_reflect_captures_written_file() -> None:
    loop = _loop(_RecordingModel(ReviewResponse(passed=True)))
    loop.reflect(_produce_state("app/Login.php", "<?php echo 1;"))
    assert loop.written_files == {"app/Login.php": "<?php echo 1;"}
    assert loop.has_produced is True


def test_reflect_keeps_latest_content_per_path() -> None:
    loop = _loop(_RecordingModel(ReviewResponse(passed=True)))
    loop.reflect(_produce_state("a.php", "v1"))
    loop.reflect(_produce_state("a.php", "v2"))
    assert loop.written_files == {"a.php": "v2"}


def test_research_tool_does_not_capture_a_file() -> None:
    loop = _loop(_RecordingModel(ReviewResponse(passed=True)))
    loop.reflect(
        {
            "last_observation": {"tool": "read_file", "ok": True},
            "last_decision": {"tool": "read_file", "tool_args": {"path": "a.php"}},
            "steps": [],
        }
    )
    assert loop.written_files == {}


def _review_state() -> dict:
    return {
        "status": STATUS_RUNNING,
        "review_retries": 0,
        "task": {"title": "T", "description": "d"},
        "output": "done",
        "steps": [],
        "last_decision": {},
    }


def test_self_review_injects_written_files() -> None:
    model = _RecordingModel(ReviewResponse(passed=True))
    loop = _loop(model)
    loop.written_files = {"a.php": "<?php class A {}"}
    loop.self_review(_review_state())
    assert model.seen_state is not None
    assert model.seen_state["written_files"] == [{"path": "a.php", "content": "<?php class A {}"}]


def test_self_review_no_files_for_analysis_run() -> None:
    model = _RecordingModel(ReviewResponse(passed=True))
    loop = _loop(model)  # no written_files (analysis/design run)
    loop.self_review(_review_state())
    assert model.seen_state is not None
    assert "written_files" not in model.seen_state
