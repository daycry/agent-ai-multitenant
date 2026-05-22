"""Integration tests for the shell_exec builtin tool (task_02_15).

Real subprocesses, run through `python` (on PATH on every platform and
on CI). The command allowlist and the timeout are the two guards under
test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_runtime.shell_exec import ShellExecTool

pytestmark = pytest.mark.integration


def _tool(tmp_path: Path, *, timeout_s: float = 30.0) -> ShellExecTool:
    return ShellExecTool(
        allowed_commands=frozenset({"python", "python.exe"}),
        timeout_s=timeout_s,
        workspace=str(tmp_path),
    )


def test_allowed_command_runs_and_captures_stdout(tmp_path: Path) -> None:
    result = _tool(tmp_path)({"command": "python -c \"print('hello shell')\""})
    assert result.ok is True
    assert result.output["exit_code"] == 0
    assert "hello shell" in result.output["stdout"]


def test_command_not_in_allowlist_is_blocked(tmp_path: Path) -> None:
    result = _tool(tmp_path)({"command": "rm -rf /"})
    assert result.ok is False
    assert "not allowed" in (result.error or "")
    # Blocked before execution — there is no exit code.
    assert "exit_code" not in (result.output or {})


def test_nonzero_exit_code_is_reported(tmp_path: Path) -> None:
    result = _tool(tmp_path)({"command": 'python -c "import sys; sys.exit(3)"'})
    assert result.ok is False
    assert result.output["exit_code"] == 3


def test_stderr_is_captured(tmp_path: Path) -> None:
    result = _tool(tmp_path)({"command": "python -c \"import sys; sys.stderr.write('boom')\""})
    assert "boom" in result.output["stderr"]


def test_timeout_kills_a_hung_command(tmp_path: Path) -> None:
    result = _tool(tmp_path, timeout_s=1.0)({"command": 'python -c "import time; time.sleep(10)"'})
    assert result.ok is False
    assert "timed out" in (result.error or "")


def test_command_runs_in_the_configured_workspace(tmp_path: Path) -> None:
    result = _tool(tmp_path)({"command": "python -c \"open('marker.txt', 'w').write('here')\""})
    assert result.ok is True
    assert (tmp_path / "marker.txt").read_text() == "here"


def test_empty_command_is_rejected(tmp_path: Path) -> None:
    assert _tool(tmp_path)({"command": "   "}).ok is False
    assert _tool(tmp_path)({}).ok is False


def test_unparseable_command_is_rejected(tmp_path: Path) -> None:
    result = _tool(tmp_path)({"command": 'python -c "unterminated'})
    assert result.ok is False
    assert "could not parse" in (result.error or "")
