"""Unit tests for the file family tools — focus on file_delete (ADR 0089 / R6).

A run that produces a coherent deliverable sometimes has to REMOVE a stale or
duplicate file left by an earlier attempt (the worktree persists across runs).
Before delete_file the agent had no way to do this (`rm`/`git rm` gated, no
delete tool), so it could not reconcile competing implementations and never
converged. ``file_delete`` closes that gap, path-jailed to the workspace.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.file_tools import WorkspaceFiles


def _files(tmp_path: Path) -> WorkspaceFiles:
    return WorkspaceFiles(root=str(tmp_path))


def test_delete_removes_an_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "dup.php"
    target.write_text("<?php // duplicate", encoding="utf-8")
    res = _files(tmp_path).file_delete({"path": "dup.php"})
    assert res.ok is True
    assert res.output == {"path": "dup.php", "deleted": True}
    assert not target.exists()


def test_delete_missing_file_fails_cleanly(tmp_path: Path) -> None:
    res = _files(tmp_path).file_delete({"path": "nope.php"})
    assert res.ok is False
    assert "not a file" in (res.error or "")


def test_delete_directory_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    res = _files(tmp_path).file_delete({"path": "sub"})
    assert res.ok is False
    assert "directory" in (res.error or "")
    assert (tmp_path / "sub").is_dir()  # not removed


def test_delete_path_escaping_workspace_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("keep me", encoding="utf-8")
    try:
        res = _files(tmp_path).file_delete({"path": "../secret.txt"})
        assert res.ok is False
        assert "escapes the workspace" in (res.error or "")
        assert outside.exists()  # path-jail prevented the delete
    finally:
        outside.unlink()


def test_delete_empty_path_is_rejected(tmp_path: Path) -> None:
    res = _files(tmp_path).file_delete({"path": "   "})
    assert res.ok is False
    assert "non-empty 'path'" in (res.error or "")
