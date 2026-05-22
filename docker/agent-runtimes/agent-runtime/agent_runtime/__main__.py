"""agent-runtime entrypoint (Plan 02 Fase B, task_02_05).

A self-check: confirm the runtime's critical dependencies import,
emit a JSON banner on stdout, exit 0. The full LangGraph agent loop is
wired in Fase C (task_02_10) — until then this proves the image is
healthy and gives the worker (task_02_06) a deterministic line to
assert on.
"""

from __future__ import annotations

import json
import platform
import sys
from importlib import metadata


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


def main() -> int:
    info = selftest()
    print(json.dumps(info, sort_keys=True))
    return 0 if info["status"] == "ready" else 1


if __name__ == "__main__":
    sys.exit(main())
