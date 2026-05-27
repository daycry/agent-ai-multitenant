"""Subprocess entry-point for `python_function`-typed Tools (task_05_13).

Invoked by :class:`agent_runtime.python_function_tool.PythonFunctionTool`
via ``subprocess.run([python, _python_sandbox_runner.py, code_file])``.
We deliberately keep this file tiny and stdlib-only so:

  * the import cost is sub-50ms on every tool call (no third-party deps);
  * a leaked module reference cannot reach into the agent's runtime
    state (the subprocess has no link back);
  * it works on the production runtime image, the dev venv, and a
    fresh ``python:3.12-alpine`` indistinguishably.

Protocol:

  argv[1]   path to a Python file the operator's Tool row produced.
            The file must define ``def run(args: dict) -> Any``. Any
            other top-level shape (raw expression, async def, missing
            ``run``) is rejected.

  stdin     JSON object: the call's `args` dict.

  stdout    one line of JSON:
              { "ok": true,  "output": <whatever run() returned> }
              { "ok": false, "error": "<message>" }

  stderr    free-form text. The wrapper captures it for debugging but
            doesn't surface it to the agent.

  exit code 0 on success, 1 on any failure. The wrapper always reads
            stdout regardless — it carries the structured error.

Security envelope (best-effort; full sandboxing lands in task_05_14
docker_command Tools):

  * Subprocess env is set by the wrapper to ``{}`` so no API keys leak.
  * ``import os`` is allowed — restricting it would break too much
    legit code. The operator is trusted to vet Tool source; this
    sandbox guards against accidental footguns, not malicious code.
  * Output coerced to a JSON-serialisable value; non-serialisable
    values surface as a typed error.
  * Timeout is enforced by the parent via ``subprocess.run(timeout=)``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
from typing import Any


def _fail(message: str) -> int:
    """Emit a structured failure on stdout and exit non-zero."""
    json.dump({"ok": False, "error": message}, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 1


def _load_user_function(code_path: str) -> Any:
    """Import the user code file as a module and pull out ``run``."""
    spec = importlib.util.spec_from_file_location("_user_tool", code_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {code_path!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if run is None:
        raise RuntimeError("user code does not define a top-level `run(args)` function")
    if not callable(run):
        raise RuntimeError("`run` exists but is not callable")
    return run


def _parse_args() -> dict[str, Any] | int:
    """Read + validate the args dict from stdin. Returns the dict, or
    an exit code if validation failed (already emitted to stdout)."""
    stdin_text = sys.stdin.read()
    try:
        args = json.loads(stdin_text) if stdin_text else {}
    except json.JSONDecodeError as exc:
        return _fail(f"args is not valid JSON: {exc}")
    if not isinstance(args, dict):
        return _fail(f"args must be a JSON object, got {type(args).__name__}")
    return args


def main() -> int:
    if len(sys.argv) != 2:
        return _fail("internal error: sandbox runner takes exactly one path argument")
    code_path = sys.argv[1]

    args = _parse_args()
    if isinstance(args, int):
        return args

    try:
        run = _load_user_function(code_path)
    except Exception as exc:
        return _fail(f"failed to load tool code: {exc}")

    try:
        result = run(args)
    except Exception as exc:
        # Capture the full traceback in stderr for debugging; the
        # parent only sees the typed error message.
        traceback.print_exc(file=sys.stderr)
        return _fail(f"{type(exc).__name__}: {exc}")

    # Coerce to JSON. dataclasses + Pydantic models surface as TypeError;
    # we don't try to be smart — Tool authors should return plain dicts/
    # lists/strings/numbers/bool/None.
    try:
        payload = {"ok": True, "output": result}
        json.dump(payload, sys.stdout)
    except (TypeError, ValueError) as exc:
        return _fail(f"tool returned a non-JSON-serialisable value: {exc}")
    sys.stdout.write("\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
