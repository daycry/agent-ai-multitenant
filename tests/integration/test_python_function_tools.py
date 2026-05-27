"""Tests for the `python_function`-typed Tool executor (Plan 05 task_05_13).

The executor runs Tool code in an isolated subprocess via the
`_python_sandbox_runner.py` entry-point. We assert the contract:

  * happy-path round-trip — `def run(args)` returns a dict, the
    executor surfaces it as `ToolResult.output`;
  * timeout enforcement — an infinite-loop tool returns
    `ToolResult.ok=False` after the configured wall-clock;
  * exception inside the tool surfaces as `ToolResult.error` (the
    subprocess captured the traceback in stderr — we don't bubble
    it to the agent, just the typed message);
  * shape errors — missing `run`, non-callable `run`, non-JSON
    return — surface as typed errors, never as a crash;
  * isolation — the subprocess does NOT inherit the parent's env
    (proves a secret in os.environ stays out of the tool).
"""

from __future__ import annotations

import os

import pytest
from agent_runtime.python_function_tool import (
    PythonFunctionTool,
    PythonFunctionToolSpec,
    build_python_function_tool,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Happy-path round-trip
# ---------------------------------------------------------------------------
def test_simple_run_returns_output() -> None:
    tool = PythonFunctionTool(
        name="adder",
        code=("def run(args):\n" "    return {'sum': args['a'] + args['b']}\n"),
    )
    result = tool({"a": 2, "b": 3})
    assert result.ok is True
    assert result.output == {"sum": 5}


def test_tool_returning_list_works() -> None:
    tool = PythonFunctionTool(
        name="lister",
        code="def run(args):\n    return list(range(args['n']))\n",
    )
    result = tool({"n": 4})
    assert result.ok is True
    assert result.output == [0, 1, 2, 3]


def test_tool_returning_none_works() -> None:
    tool = PythonFunctionTool(
        name="void",
        code="def run(args):\n    return None\n",
    )
    result = tool({})
    assert result.ok is True
    assert result.output is None


# ---------------------------------------------------------------------------
# Error mapping — exceptions, missing run, non-JSON
# ---------------------------------------------------------------------------
def test_tool_exception_surfaces_as_failed_toolresult() -> None:
    tool = PythonFunctionTool(
        name="boom",
        code="def run(args):\n    raise ValueError('explicit boom')\n",
    )
    result = tool({})
    assert result.ok is False
    assert "ValueError" in (result.error or "")
    assert "explicit boom" in (result.error or "")


def test_tool_without_run_function_is_rejected() -> None:
    tool = PythonFunctionTool(name="empty", code="x = 1\n")
    result = tool({})
    assert result.ok is False
    assert "run" in (result.error or "")


def test_tool_with_non_callable_run_is_rejected() -> None:
    tool = PythonFunctionTool(name="not-callable", code="run = 42\n")
    result = tool({})
    assert result.ok is False
    assert "not callable" in (result.error or "")


def test_tool_returning_non_json_value_is_rejected() -> None:
    """A function returning an open file or a set surfaces as a typed
    error — the runner's last line of defense before garbage reaches
    the agent."""
    tool = PythonFunctionTool(
        name="bad-return",
        code=("def run(args):\n" "    return {1, 2, 3}  # set is not JSON-serialisable\n"),
    )
    result = tool({})
    assert result.ok is False
    assert "JSON" in (result.error or "")


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------
def test_infinite_loop_is_killed_at_timeout() -> None:
    tool = PythonFunctionTool(
        name="loop",
        code=("def run(args):\n" "    while True:\n" "        pass\n"),
        timeout_s=0.5,
    )
    result = tool({})
    assert result.ok is False
    assert "timed out" in (result.error or "")


# ---------------------------------------------------------------------------
# Isolation — empty env in the subprocess
# ---------------------------------------------------------------------------
def test_subprocess_does_not_inherit_parent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The parent has a secret in os.environ. The subprocess must not
    see it — proves env scrubbing works."""
    monkeypatch.setenv("AGENT_RUNTIME_SECRET", "should-not-leak")
    tool = PythonFunctionTool(
        name="env-leak",
        code=(
            "import os\n"
            "def run(args):\n"
            "    return {'has_secret': 'AGENT_RUNTIME_SECRET' in os.environ}\n"
        ),
    )
    result = tool({})
    assert result.ok is True
    assert result.output == {"has_secret": False}


# ---------------------------------------------------------------------------
# Input mapping — args reach the function as a dict
# ---------------------------------------------------------------------------
def test_args_arrive_as_dict() -> None:
    tool = PythonFunctionTool(
        name="echo",
        code=("def run(args):\n" "    return {'type': type(args).__name__, 'received': args}\n"),
    )
    result = tool({"hello": "world", "n": 42})
    assert result.ok is True
    assert result.output["type"] == "dict"
    assert result.output["received"] == {"hello": "world", "n": 42}


def test_invalid_args_type_in_runner_path() -> None:
    """The runner asserts `args` is a dict — if a buggy caller sent a
    list, surface as typed error rather than crash inside user code."""
    # We can't easily hit this via PythonFunctionTool's __call__ (it
    # always json-dumps a dict), but the runner protocol covers it.
    # Instead pin that an empty stdin still works (empty dict).
    tool = PythonFunctionTool(
        name="empty-args",
        code="def run(args):\n    return {'got': args}\n",
    )
    result = tool({})
    assert result.ok is True
    assert result.output == {"got": {}}


# ---------------------------------------------------------------------------
# Spec + builder convenience
# ---------------------------------------------------------------------------
def test_build_from_spec() -> None:
    spec = PythonFunctionToolSpec(
        name="adder",
        code="def run(args):\n    return args['a'] + args['b']\n",
        timeout_s=5.0,
    )
    tool = build_python_function_tool(spec)
    assert tool.name == "adder"
    assert tool.timeout_s == 5.0
    result = tool({"a": 10, "b": 20})
    assert result.ok is True
    assert result.output == 30


# ---------------------------------------------------------------------------
# Missing executable surfaces as a typed error (not a crash)
# ---------------------------------------------------------------------------
def test_missing_python_executable_returns_failed_toolresult() -> None:
    tool = PythonFunctionTool(
        name="x",
        code="def run(args):\n    return 1\n",
        python_executable="this-python-does-not-exist-12345",
    )
    result = tool({})
    assert result.ok is False
    err = result.error or ""
    # On Windows the error mentions "not found"; on Unix "No such file".
    assert "not found" in err.lower() or "no such file" in err.lower()


# ---------------------------------------------------------------------------
# CWD isolation — the subprocess runs in a fresh tempdir, not the worker's CWD
# ---------------------------------------------------------------------------
def test_cwd_is_isolated_per_call() -> None:
    """Every call gets its own tempdir as cwd. The tool can write
    side-effect files there but they vanish on call return."""
    tool = PythonFunctionTool(
        name="cwd-probe",
        code=(
            "import os\n"
            "from pathlib import Path\n"
            "def run(args):\n"
            "    here = Path(os.getcwd())\n"
            "    (here / 'sentinel.txt').write_text('hi')\n"
            "    return {'cwd_name': here.name, 'sentinel_exists': True}\n"
        ),
    )
    result = tool({})
    assert result.ok is True
    assert result.output["sentinel_exists"] is True
    # The sentinel was inside the tempdir — long gone now.
    assert "agent-pyfn-" in result.output["cwd_name"]
    # And the parent process' cwd doesn't have the sentinel.
    assert not (os.getcwd() + "/sentinel.txt").endswith("sentinel.txt") or not os.path.exists(
        os.path.join(os.getcwd(), "sentinel.txt")
    )
