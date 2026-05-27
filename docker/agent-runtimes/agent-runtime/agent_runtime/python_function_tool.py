"""`python_function`-typed Tool executor (Plan 05 task_05_13).

A Tool row with ``implementation_type='python_function'`` carries:

  * ``implementation_ref``: the **source code** of a Python function.
    The code must define a top-level ``def run(args: dict) -> Any``.

  * ``input_schema``: JSON schema for the args dict.

  * ``timeout_seconds``: wall-clock cap for the subprocess.

We run the code in an **isolated subprocess** (the runner script
``_python_sandbox_runner.py``), NOT via ``eval`` / ``exec`` in the
agent's interpreter. The roadmap is explicit on this:

    "Activar tools de tipo python_function en sandbox seguro
     (subprocess aislado, no eval)"

Reason: a Tool author's bug (or a malicious operator) shouldn't be
able to mutate the agent runtime's state. A subprocess gives us:

  * a fresh interpreter heap — no shared dicts, no leaked imports;
  * empty env — no API tokens, secrets or paths inherited;
  * wall-clock timeout via ``subprocess.run(timeout=)``;
  * crash isolation — a segfault in user code kills the subprocess,
    not the agent loop.

What this sandbox does NOT do (deferred to task_05_14 docker_command
Tools, where a container provides the full security envelope):

  * Network deny — the subprocess still inherits the worker's
    network access. Pair with project egress allowlists.
  * Filesystem deny — the subprocess can read most of the worker's
    filesystem. We point its CWD at a fresh tempdir so writes don't
    pollute the worker, but it can still read elsewhere.
  * Memory cap — RLIMIT_AS works on Linux but not Windows or macOS;
    we don't try to be consistent across platforms here.

For untrusted code, use docker_command Tools (task_05_14) instead.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime.tools import ToolResult

# Resolved at import time so the executor doesn't pay a stat() per call.
_RUNNER_PATH = str((Path(__file__).parent / "_python_sandbox_runner.py").resolve())


@dataclass
class PythonFunctionTool:
    """One python_function-typed Tool. Sync, sandboxed by subprocess.

    Instantiated once per Tool row at agent-loop boot. The `code`
    field is the function source — written to a tempfile on every
    call so subprocess imports it cleanly.

    `python_executable` defaults to ``sys.executable`` (the agent's
    own Python). Override for tests or to pin a specific minor
    version. The runner script is stdlib-only so any 3.10+ Python
    works.
    """

    name: str
    code: str
    timeout_s: float = 30.0
    python_executable: str = sys.executable

    def __call__(self, args: dict[str, Any]) -> ToolResult:
        # Write code to a tempfile that lives only for the call.
        # We avoid passing code via stdin because importlib.util needs
        # a real file path to give the user nice ``__file__`` semantics.
        with tempfile.TemporaryDirectory(prefix="agent-pyfn-") as tmpdir:
            code_path = Path(tmpdir) / "tool.py"
            code_path.write_text(self.code, encoding="utf-8")

            # Empty env — the subprocess gets nothing inherited.
            # PATH stays minimal so the subprocess can find its own
            # Python if `python_executable` is a relative name.
            child_env = {"PATH": os.environ.get("PATH", "")}

            try:
                completed = subprocess.run(
                    [self.python_executable, _RUNNER_PATH, str(code_path)],
                    input=json.dumps(args),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    cwd=tmpdir,
                    env=child_env,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return ToolResult(
                    ok=False,
                    error=f"python_function timed out after {self.timeout_s}s",
                )
            except FileNotFoundError as exc:
                # `python_executable` doesn't exist on PATH.
                return ToolResult(ok=False, error=f"python executable not found: {exc}")
            return _parse_runner_output(completed)


def _parse_runner_output(completed: subprocess.CompletedProcess[str]) -> ToolResult:
    """Project the sandbox runner's stdout into a ToolResult.

    The runner is contractually expected to emit exactly one JSON line
    of the shape ``{"ok": bool, "output"?, "error"?}``. Anything else
    surfaces as a typed error — the agent loop never sees the raw exit
    code or stderr.
    """
    stdout = completed.stdout.strip()
    if not stdout:
        return ToolResult(
            ok=False,
            error=(
                f"sandbox produced no output (exit={completed.returncode}, "
                f"stderr={completed.stderr.strip()[:200]!r})"
            ),
        )
    try:
        payload = json.loads(stdout.splitlines()[-1])
    except json.JSONDecodeError:
        return ToolResult(ok=False, error=f"sandbox output is not JSON: {stdout[:200]!r}")
    if not isinstance(payload, dict) or "ok" not in payload:
        return ToolResult(ok=False, error=f"sandbox payload has wrong shape: {payload!r}")
    if payload["ok"]:
        return ToolResult(ok=True, output=payload.get("output"))
    return ToolResult(ok=False, error=str(payload.get("error", "unknown sandbox error")))


@dataclass(frozen=True)
class PythonFunctionToolSpec:
    """Persisted shape of a python_function Tool row, projected to
    what :class:`PythonFunctionTool` needs at construction.

    Same role as :class:`agent_runtime.http_endpoint_tool.HttpEndpointToolSpec`:
    keeps the api-server → agent-runtime contract drift visible at
    the type level."""

    name: str
    code: str
    timeout_s: float = 30.0


def build_python_function_tool(spec: PythonFunctionToolSpec) -> PythonFunctionTool:
    """Convenience constructor mirroring `build_http_endpoint_tool`."""
    return PythonFunctionTool(
        name=spec.name,
        code=spec.code,
        timeout_s=spec.timeout_s,
    )


__all__ = [
    "PythonFunctionTool",
    "PythonFunctionToolSpec",
    "build_python_function_tool",
]
