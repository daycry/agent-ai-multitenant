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


def _build_mcp_vault_resolver() -> Any | None:
    """Best-effort Vault resolver for MCP auth (task_06_18_12 / ADR 0052).

    A connected MCP server that declares ``auth_ref`` needs a resolver to fetch
    its secret from Vault. We build an ``HvacVaultResolver`` from the env
    (``AGENT_VAULT_ADDR`` + ``AGENT_VAULT_TOKEN``) when both are present; absent
    a token (a bare run / a server that needs no auth) we return ``None`` so the
    runner stays unauthenticated — connecting a server WITH ``auth_ref`` then
    surfaces a typed ``MCPAuthError`` rather than silently opening an
    unauthenticated session.
    """
    token = os.environ.get("AGENT_VAULT_TOKEN")
    if not token:
        return None
    try:
        import hvac
        from shared_mcp import HvacVaultResolver
    except ImportError:  # pragma: no cover - hvac/shared_mcp not installed
        return None
    client = hvac.Client(url=os.environ.get("AGENT_VAULT_ADDR", "http://vault:8200"), token=token)
    return HvacVaultResolver(client=client)


def _to_mcp_config(raw: dict[str, Any]) -> Any:
    """Map one serialised ``mcp_servers`` entry to a ``MCPServerConfig``.

    Mirrors ``api_server.routers.mcp._to_runtime_config`` — the same JSON shape
    the project's ``mcp_servers`` JSONB carries, projected onto the frozen
    dataclass the client consumes (list ``args`` -> tuple to stay hashable).
    """
    from shared_mcp import MCPServerConfig

    return MCPServerConfig(
        name=str(raw["name"]),
        transport=str(raw["transport"]),
        command=raw.get("command"),
        args=tuple(raw.get("args") or ()),
        env=dict(raw.get("env") or {}),
        url=raw.get("url"),
        headers=dict(raw.get("headers") or {}),
        auth_ref=raw.get("auth_ref"),
        timeout_s=float(raw.get("timeout_s", 30.0)),
    )


def _wire_mcp_servers(registry: Any, spec: dict[str, Any]) -> Any | None:
    """Start an ``MCPToolRunner`` and register every declared server's tools.

    Activated only when the worker threaded a non-empty ``mcp_servers`` list
    (task_06_18_12 / ADR 0052). For each server we open a session (auth via
    Vault when ``auth_ref`` is set) and register its tools under the canonical
    ``<server>.<tool>`` namespace so the agent∩mode allowlist (ADR 0048) can
    intersect them like any other tool. A server that fails to connect does NOT
    abort the boot: it is reported as an ``execution`` event and skipped, so the
    rest of the run proceeds with the tools that did connect.

    Returns the live ``MCPToolRunner`` so the caller closes it in ``finally``,
    or ``None`` when there is nothing to wire (feature-safe — no MCP session is
    opened, the pre-06.18 behaviour).
    """
    raw_servers = spec.get("mcp_servers") or []
    if not raw_servers:
        return None

    from agent_runtime.mcp_tools import MCPToolRunner, register_mcp_server

    runner = MCPToolRunner(vault_resolver=_build_mcp_vault_resolver())
    runner.start()
    for raw in raw_servers:
        try:
            config = _to_mcp_config(raw)
            tools = runner.connect(config)
            registered = register_mcp_server(registry, runner, config.name, tools)
            _emit(
                {
                    "event": "mcp.server_connected",
                    "server": config.name,
                    "tools": registered,
                }
            )
        except Exception as exc:
            _emit(
                {
                    "event": "mcp.server_failed",
                    "server": str(raw.get("name", "?")),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return runner


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

    # Wire the project's MCP servers (task_06_18_12 / ADR 0052). Gated on a
    # non-empty `mcp_servers` list: each declared server's `<server>.<tool>`
    # tools are registered so the allowlist below intersects them like any
    # other tool. The runner holds the live sessions and MUST be closed when the
    # run ends — kept here so the `finally` below tears it down.
    mcp_runner = _wire_mcp_servers(registry, spec)

    # The MCP runner (when present) holds live sessions: a background event loop
    # and open transports/subprocesses. From the instant it is started it MUST be
    # torn down on EVERY exit path, so the whole remaining boot — not just the
    # agent loop — runs inside this try/finally. Otherwise an exception while
    # wiring shell_exec, building deps or parsing budgets would leak the runner
    # (task_06_18_12 review fix: previously the try started after deps/budgets).
    try:
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

        # Skills → inyección de prompt (task_06_18_13 / ADR 0050). El worker
        # forwardea los `prompt_fragment` de las skills asignadas; los concatenamos
        # en un preámbulo que el modelo prepende al system prompt EFECTIVO. Clave
        # ausente / lista vacía → `None` → el system prompt queda intacto
        # (backward-compat).
        fragments = spec.get("skill_prompt_fragments") or []
        system_preamble = "\n\n".join(str(f) for f in fragments if f) or None

        _emit({"event": "execution.started", "task": task})
        result = run_agent(
            deps,
            task,
            budgets=budgets,
            on_step=lambda step: _emit({"event": "step", "step": step}),
            system_preamble=system_preamble,
        )
    finally:
        # Always tear down the MCP sessions (background loop + open transports),
        # even when the run raised — leaking them would keep subprocesses alive.
        if mcp_runner is not None:
            mcp_runner.close()
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
