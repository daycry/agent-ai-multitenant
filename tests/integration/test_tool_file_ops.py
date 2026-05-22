"""Integration tests for the file_read / file_write / file_list tools
(task_02_16).

The workspace is a real tmp_path; the key property under test is that
nothing the agent does can reach outside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime.file_tools import WorkspaceFiles

pytestmark = pytest.mark.integration


def _files(tmp_path: Path) -> WorkspaceFiles:
    return WorkspaceFiles(root=str(tmp_path))


def test_write_then_read_round_trips(tmp_path: Path) -> None:
    files = _files(tmp_path)
    written = files.file_write({"path": "notes.txt", "content": "hello workspace"})
    assert written.ok is True
    assert written.output["bytes_written"] == 15

    read = files.file_read({"path": "notes.txt"})
    assert read.ok is True
    assert read.output["content"] == "hello workspace"


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    result = _files(tmp_path).file_write({"path": "sub/dir/deep.txt", "content": "x"})
    assert result.ok is True
    assert (tmp_path / "sub" / "dir" / "deep.txt").read_text() == "x"


def test_file_list_reports_entries(tmp_path: Path) -> None:
    files = _files(tmp_path)
    files.file_write({"path": "a.txt", "content": "aa"})
    files.file_write({"path": "b.txt", "content": "bbbb"})

    listed = files.file_list({"path": "."})
    assert listed.ok is True
    by_name = {e["name"]: e for e in listed.output["entries"]}
    assert by_name["a.txt"]["type"] == "file"
    assert by_name["a.txt"]["size"] == 2
    assert by_name["b.txt"]["size"] == 4


def test_read_of_a_missing_file_fails(tmp_path: Path) -> None:
    result = _files(tmp_path).file_read({"path": "nope.txt"})
    assert result.ok is False
    assert "not a file" in (result.error or "")


def test_read_of_a_directory_fails(tmp_path: Path) -> None:
    files = _files(tmp_path)
    files.file_write({"path": "dir/f.txt", "content": "x"})
    assert files.file_read({"path": "dir"}).ok is False


def test_relative_traversal_is_blocked(tmp_path: Path) -> None:
    files = _files(tmp_path)
    for raw in ("../escape.txt", "../../etc/passwd", "sub/../../escape"):
        result = files.file_read({"path": raw})
        assert result.ok is False
        assert "escapes the workspace" in (result.error or "")


def test_absolute_path_is_blocked(tmp_path: Path) -> None:
    files = _files(tmp_path)
    # An absolute path must not let the agent read outside /workspace.
    blocked = files.file_write({"path": str(tmp_path.parent / "outside.txt"), "content": "x"})
    assert blocked.ok is False
    assert "escapes the workspace" in (blocked.error or "")
    assert not (tmp_path.parent / "outside.txt").exists()


def test_traversal_block_holds_for_write_and_list(tmp_path: Path) -> None:
    files = _files(tmp_path)
    assert files.file_write({"path": "../evil.txt", "content": "x"}).ok is False
    assert files.file_list({"path": ".."}).ok is False


def test_write_rejects_non_string_content(tmp_path: Path) -> None:
    result = _files(tmp_path).file_write({"path": "x.txt", "content": 123})
    assert result.ok is False
    assert "must be a string" in (result.error or "")


def test_empty_path_is_rejected(tmp_path: Path) -> None:
    assert _files(tmp_path).file_read({"path": "  "}).ok is False
    assert _files(tmp_path).file_read({}).ok is False
