"""agent-runtime entrypoint (Plan 02 Fase B + Fase G / task_02_29).

Two modes:

  * **With a task spec** — env `AGENT_TASK_SPEC` (JSON) or the file
    `/workspace/agent_task.json` — it runs the LangGraph agent loop and
    emits one JSON line per step on stdout, then a final result line.
    The worker (task_02_30) tails this stream.
  * **Without a spec** — the Fase B dependency self-check (a JSON banner),
    so a bare `docker run agent-runtime:v1` is still a health probe.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from importlib import metadata
from pathlib import Path
from typing import Any

# Where the worker drops a task spec when it does not pass AGENT_TASK_SPEC.
_TASK_SPEC_FILE = "/workspace/agent_task.json"


def _dep_version(dist: str) -> str:
    """Best-effort installed version of a distribution."""
    try:
        return metadata.version(dist)
    except metadata.PackageNotFoundError:
        return "missing"


def selftest() -> dict[str, str]:
    """Import the critical dependencies and report their versions."""
    info: dict[str, str] = {
        "runtime": "agent-runtime",
        "version": "v1",
        "python": platform.python_version(),
        "status": "ready",
    }
    try:
        import langgraph  # noqa: F401

        info["langgraph"] = _dep_version("langgraph")
        info["langchain_core"] = _dep_version("langchain-core")
    except ImportError as exc:
        info["status"] = "error"
        info["error"] = str(exc)
    return info


def _emit(event: dict[str, Any]) -> None:
    """Write one JSON event line to stdout, flushed so the worker sees it live."""
    print(json.dumps(event, sort_keys=True, default=str), flush=True)


def _load_spec() -> dict[str, Any] | None:
    """The task spec from AGENT_TASK_SPEC, or the workspace file, or None."""
    raw = os.environ.get("AGENT_TASK_SPEC")
    if raw and raw.strip():
        return json.loads(raw)  # type: ignore[no-any-return]
    path = Path(_TASK_SPEC_FILE)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return None


def run_task(spec: dict[str, Any]) -> int:
    """Run the agent loop for `spec`, streaming the steps_log as JSON lines."""
    from agent_runtime.graph import AgentDeps, run_agent
    from agent_runtime.model import model_from_spec
    from agent_runtime.safeguards import Budgets

    task = spec["task"]
    deps = AgentDeps(model=model_from_spec(spec["model"]))

    budgets = None
    if spec.get("budgets"):
        known = {
            key: value
            for key, value in spec["budgets"].items()
            if key in Budgets.__dataclass_fields__
        }
        budgets = Budgets(**known)

    _emit({"event": "execution.started", "task": task})
    result = run_agent(
        deps,
        task,
        budgets=budgets,
        on_step=lambda step: _emit({"event": "step", "step": step}),
    )
    _emit({"event": "execution.finished", "result": result.as_dict()})
    return 0


def main() -> int:
    spec = _load_spec()
    if spec is None:
        info = selftest()
        print(json.dumps(info, sort_keys=True))
        return 0 if info["status"] == "ready" else 1
    try:
        return run_task(spec)
    except Exception as exc:  # a crash must still surface a structured line
        _emit({"event": "execution.error", "error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    sys.exit(main())
