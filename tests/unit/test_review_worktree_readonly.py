"""Reviewer mounts the implementer's worktree READ-ONLY (ADR 0095).

A review run used to get an empty tmpfs `/workspace` (blind reviewer). Now it
resolves the implementer's existing per-task worktree and mounts it read-only so
the reviewer can read the code without mutating it.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_resolve_review_worktree_returns_existing_dir(tmp_path) -> None:
    from workers.config import Settings
    from workers.execution import _resolve_review_worktree

    settings = Settings(data_root=str(tmp_path))
    wt = tmp_path / "projects" / "demo" / "api-ci" / "worktrees" / "task-1"
    wt.mkdir(parents=True)

    assert _resolve_review_worktree(settings, "demo", "api-ci", "task-1") == str(wt)


def test_resolve_review_worktree_none_when_missing(tmp_path) -> None:
    # Implementer ran in a tmpfs (no worktree) → reviewer falls back to empty /workspace.
    from workers.config import Settings
    from workers.execution import _resolve_review_worktree

    settings = Settings(data_root=str(tmp_path))
    assert _resolve_review_worktree(settings, "demo", "api-ci", "task-x") is None


def test_resolve_review_worktree_none_when_path_is_a_file(tmp_path) -> None:
    from workers.config import Settings
    from workers.execution import _resolve_review_worktree

    settings = Settings(data_root=str(tmp_path))
    wtroot = tmp_path / "projects" / "demo" / "api-ci" / "worktrees"
    wtroot.mkdir(parents=True)
    (wtroot / "task-f").write_text("not a dir", encoding="utf-8")
    assert _resolve_review_worktree(settings, "demo", "api-ci", "task-f") is None
