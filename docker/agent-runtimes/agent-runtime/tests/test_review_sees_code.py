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

from pathlib import Path

import pytest
from agent_runtime.graph import AgentDeps, _AgentLoop, _harvest_worktree_files
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


def test_self_review_injects_written_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # No worktree on disk → the self-review falls back to this run's write capture.
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "absent"))
    model = _RecordingModel(ReviewResponse(passed=True))
    loop = _loop(model)
    loop.written_files = {"a.php": "<?php class A {}"}
    loop.self_review(_review_state())
    assert model.seen_state is not None
    assert model.seen_state["written_files"] == [{"path": "a.php", "content": "<?php class A {}"}]


def test_self_review_no_files_for_analysis_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path / "absent"))
    model = _RecordingModel(ReviewResponse(passed=True))
    loop = _loop(model)  # no written_files (analysis/design run)
    loop.self_review(_review_state())
    assert model.seen_state is not None
    assert "written_files" not in model.seen_state


def test_self_review_reads_cumulative_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An INCREMENTAL run: a prior run committed AuthController.php; this run only
    # re-wrote JwtFilter.php. The review must still see BOTH (the cumulative
    # deliverable on disk), not just this run's write — else it rejects "missing
    # files" and the task can never converge.
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    controllers = tmp_path / "app" / "Controllers"
    controllers.mkdir(parents=True)
    (controllers / "AuthController.php").write_text("<?php class AuthController {}")
    filters = tmp_path / "app" / "Filters"
    filters.mkdir(parents=True)
    (filters / "JwtFilter.php").write_text("<?php class JwtFilter {}")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("[core]")

    model = _RecordingModel(ReviewResponse(passed=True))
    loop = _loop(model)
    loop.written_files = {"app/Filters/JwtFilter.php": "<?php class JwtFilter {}"}  # this run only
    loop.self_review(_review_state())
    assert model.seen_state is not None
    paths = {entry["path"] for entry in model.seen_state["written_files"]}
    assert "app/Controllers/AuthController.php" in paths  # prior-run file IS reviewed
    assert "app/Filters/JwtFilter.php" in paths
    assert not any(".git" in p for p in paths)  # VCS excluded


def test_harvest_worktree_excludes_vcs_and_prefers_current(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "A.php").write_text("a")
    (tmp_path / "app" / "B.php").write_text("b")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.php").write_text("framework")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref")

    out = _harvest_worktree_files(tmp_path, prefer=["app/B.php"])
    paths = [entry["path"] for entry in out]
    assert paths[0] == "app/B.php"  # this run's file ordered first
    assert "app/A.php" in paths
    assert all("vendor" not in p and ".git" not in p for p in paths)  # deps/VCS excluded


def test_harvest_missing_root_returns_empty(tmp_path: Path) -> None:
    assert _harvest_worktree_files(tmp_path / "nope", prefer=[]) == []
