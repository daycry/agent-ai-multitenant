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

# Sentinel distinguishing "spec has no `allowed_tools` key" (no restriction)
# from "spec has `allowed_tools: []`" (block every tool). A plain falsy
# default would conflate the two.
_NO_ALLOWLIST = object()


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


def _build_internal_api() -> Any | None:
    """The ``/internal/agent/*`` client the knowledge + memory families need.

    Built from ``AGENTIC_API_URL`` + ``AGENTIC_INTERNAL_TOKEN`` (the worker
    mints the token just before launching the container, ADR 0012). When the
    token is absent (a bare run / a deployment that wired no internal token)
    we skip those families rather than crash the boot — they simply do not
    register, and an assignment to one is reported honestly.
    """
    from agent_runtime.internal_api import InternalAgentAPI, InternalAPIConfigError

    try:
        return InternalAgentAPI.from_env()
    except InternalAPIConfigError:
        return None


def _wire_assigned_tools(
    registry: Any,
    spec: dict[str, Any],
) -> None:
    """Register every assigned tool family + serialized ToolSpec (task_06_18_05).

    Activated only when the worker serialised a ``tool_specs`` list (an agent
    WITH ``agent_tools`` assignments). With no ``tool_specs`` the boot keeps
    the pre-06.18 behaviour (echo/noop + conditional shell_exec) — the
    06.15 backward-compat rule: an agent without assignments is unchanged.

    Two seams cooperate:

      * :func:`builtin_families.register_builtin_families` wires the executable
        builtin families (file / network / notification / orchestration /
        knowledge / memory) under their CANONICAL names so they match the
        canonicalised allowlist (ADR 0048).
      * :func:`tool_wiring.register_tool_specs` wires the typed rows the
        operator/worker serialised — the ``run_*`` ``docker_command`` tools
        (image pre-resolved by the worker, which owns the runtime catalog) and
        tenant-custom ``http_endpoint`` / ``python_function`` tools. ``builtin``
        / ``mcp_tool`` specs are ignored there (the families above + MCP wiring
        own them).
    """
    from agent_runtime.builtin_families import register_builtin_families
    from agent_runtime.orchestration_tools import OrchestrationSink
    from agent_runtime.tool_wiring import ToolSpec, WiringContext, register_tool_specs

    allowed_domains = frozenset(str(d) for d in (spec.get("allowed_domains") or []))

    register_builtin_families(
        registry,
        api=_build_internal_api(),
        sink=OrchestrationSink(),
        allowed_domains=allowed_domains,
    )

    raw_specs = spec.get("tool_specs") or []
    specs = [
        ToolSpec(
            name=str(row["name"]),
            implementation_type=str(row["implementation_type"]),
            config=dict(row.get("config") or {}),
        )
        for row in raw_specs
    ]
    ctx = WiringContext(
        allowed_domains=allowed_domains,
        project_default_runtime=spec.get("default_runtime_template"),
    )
    register_tool_specs(registry, specs, ctx=ctx)


def run_task(spec: dict[str, Any]) -> int:
    """Run the agent loop for `spec`, streaming the steps_log as JSON lines."""
    from agent_runtime.approval import ApprovalGate
    from agent_runtime.graph import AgentDeps, run_agent
    from agent_runtime.model import model_from_spec
    from agent_runtime.safeguards import Budgets
    from agent_runtime.shell_exec import ShellExecTool
    from agent_runtime.tools import default_registry

    task = spec["task"]
    # The worker passes the project's human_approval_policy here; with a
    # policy the loop gates sensitive tool calls (task_02_33).
    policy = spec.get("approval_policy")

    registry = default_registry()

    # Wire the assigned tool families + serialized ToolSpec rows (task_06_18_05).
    # Gated on the presence of `tool_specs`: an agent WITH `agent_tools`
    # assignments carries the serialized list and gets its real tools wired
    # under canonical names; an agent without assignments carries no key and
    # keeps the pre-06.18 echo/noop behaviour (06.15 backward-compat).
    if "tool_specs" in spec:
        _wire_assigned_tools(registry, spec)

    # `shell_exec` is wired per project (task_06_16_02). The worker forwards
    # the project's `allowed_commands` allowlist here; we register a
    # `ShellExecTool` bound to it so the agent can run STACK commands
    # (`php`, `composer`, `vendor/bin/phpunit`, `npm`, …) — but ONLY those
    # binaries (deny-by-default). The key is always present from the worker:
    # an empty list registers a deny-all shell_exec (every command rejected),
    # which is the safe default for a project that authorised nothing. When
    # the key is absent (a bare run / older payload) shell_exec is simply
    # not registered.
    allowed_commands = spec.get("allowed_commands")
    if allowed_commands is not None:
        registry.register(
            "shell_exec",
            ShellExecTool(allowed_commands=frozenset(allowed_commands)),
        )

    # The active chat mode's tool whitelist (task_06_14_07). The worker
    # forwards `ChatModeConfig.allowed_tools` here; when present, the
    # registry rejects any tool outside the set at call time. Absent
    # (None) = no restriction. An explicit empty list = block every tool
    # (the `discussion` mode). We must distinguish "key missing" from
    # "key present but empty", so we read with a sentinel rather than a
    # falsy default.
    allowed_tools = spec.get("allowed_tools", _NO_ALLOWLIST)
    if allowed_tools is not _NO_ALLOWLIST:
        registry.set_allowed_tools(allowed_tools)

    deps = AgentDeps(
        model=model_from_spec(spec["model"]),
        tools=registry,
        approval=ApprovalGate(policy) if policy else None,
    )

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
