"""Factory that turns Tool rows into registered tools (Plan 05 task_05_17).

When the agent-runtime boots, the worker hands it a JSON list of Tool
rows — one entry per Tool wired to the agent via the `agent_tools`
junction. This module is the **single seam** that consumes that list
and registers each Tool on the loop's :class:`ToolRegistry`.

Why a single seam: the agent loop's `ToolRegistry` is sync and
type-agnostic — it stores `Callable[[dict], ToolResult]`. Each
`implementation_type` needs a different executor. Keeping the
dispatch here means the loop boot path doesn't grow a five-way
if/else, and adding a sixth implementation type (the spec already
lists `builtin` / `python_function` / `http_endpoint` / `mcp_tool`
/ `docker_command`) is a one-line addition to ``_BUILDERS``.

What this module does NOT do:

  * It doesn't load the Tool rows from the DB. The worker queries
    api-server's internal API; this module accepts the materialised
    list as input.
  * It doesn't connect to MCP servers. That's `register_mcp_server`
    in :mod:`mcp_tools` — wired separately by the boot path.
  * It doesn't enforce security_level. That's Plan 11 (guardrails).
    Today a `privileged` Tool registers like any other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agent_runtime.docker_command_tool import (
    DockerCommandToolSpec,
    build_docker_command_tool,
)
from agent_runtime.http_endpoint_tool import (
    HttpEndpointToolSpec,
    build_http_endpoint_tool,
)
from agent_runtime.python_function_tool import (
    PythonFunctionToolSpec,
    build_python_function_tool,
)
from agent_runtime.tools import ToolFn, ToolRegistry


@dataclass(frozen=True)
class ToolSpec:
    """One Tool row, projected to what the boot path needs.

    `config` is the type-specific extra (URL template, code, image+command,
    static env, ...). Shape per type:

      * http_endpoint:  {url_template, method, static_headers, static_query, timeout_s}
      * python_function: {code, timeout_s}
      * docker_command:  {image, command_template, timeout_s, mem_limit_bytes,
                           pids_limit, network_mode, static_env}
      * builtin:        ignored (already registered)
      * mcp_tool:       ignored (registered by mcp_tools.register_mcp_server)
    """

    name: str
    implementation_type: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WiringContext:
    """Runtime concerns the api-server doesn't know — supplied by the
    worker at boot time and passed to each executor builder.

    * `allowed_domains`: the project's egress allowlist. Threaded into
      http_endpoint Tools at construction (the dataclass field is
      hashable).
    * `vault_resolver`: optional, for future per-Tool Vault auth. Not
      consumed by the three executors of Plan 05; here as the seam.
    """

    allowed_domains: frozenset[str] = frozenset()
    vault_resolver: Any | None = None


# A builder maps (spec, ctx) → (tool_name, ToolFn). Returning None
# signals "ignore this row" (builtin + mcp_tool are wired elsewhere).
_Builder = Callable[[ToolSpec, WiringContext], tuple[str, ToolFn] | None]


def _build_http_endpoint(spec: ToolSpec, ctx: WiringContext) -> tuple[str, ToolFn]:
    inner = build_http_endpoint_tool(
        HttpEndpointToolSpec(
            name=spec.name,
            url_template=str(spec.config["url_template"]),
            method=str(spec.config.get("method", "GET")),
            static_headers=dict(spec.config.get("static_headers") or {}),
            static_query=dict(spec.config.get("static_query") or {}),
            timeout_s=float(spec.config.get("timeout_s", 30.0)),
        ),
        allowed_domains=ctx.allowed_domains,
    )
    return spec.name, inner


def _build_python_function(spec: ToolSpec, _ctx: WiringContext) -> tuple[str, ToolFn]:
    inner = build_python_function_tool(
        PythonFunctionToolSpec(
            name=spec.name,
            code=str(spec.config["code"]),
            timeout_s=float(spec.config.get("timeout_s", 30.0)),
        )
    )
    return spec.name, inner


def _build_docker_command(spec: ToolSpec, _ctx: WiringContext) -> tuple[str, ToolFn]:
    inner = build_docker_command_tool(
        DockerCommandToolSpec(
            name=spec.name,
            image=str(spec.config["image"]),
            command_template=list(spec.config.get("command_template") or []),
            timeout_s=float(spec.config.get("timeout_s", 30.0)),
            mem_limit_bytes=int(spec.config.get("mem_limit_bytes", 256 * 1024 * 1024)),
            pids_limit=int(spec.config.get("pids_limit", 64)),
            network_mode=str(spec.config.get("network_mode", "none")),
            static_env=dict(spec.config.get("static_env") or {}),
        )
    )
    return spec.name, inner


def _ignore(_spec: ToolSpec, _ctx: WiringContext) -> None:
    """builtin + mcp_tool are not wired by this factory.

    * builtin tools live in `default_registry()` already.
    * mcp_tool entries get registered by `mcp_tools.register_mcp_server`
      once their owning server is connected.
    """
    return None


_BUILDERS: dict[str, _Builder] = {
    "http_endpoint": _build_http_endpoint,
    "python_function": _build_python_function,
    "docker_command": _build_docker_command,
    "builtin": _ignore,
    "mcp_tool": _ignore,
}


def register_tool_specs(
    registry: ToolRegistry,
    specs: list[ToolSpec],
    *,
    ctx: WiringContext | None = None,
) -> list[str]:
    """Register every spec on the given registry.

    Returns the list of names actually registered (skips builtin /
    mcp_tool). An unknown `implementation_type` raises — that's a
    config error the operator must see at boot, not silently at
    first tool call.
    """
    if ctx is None:
        ctx = WiringContext()
    registered: list[str] = []
    for spec in specs:
        builder = _BUILDERS.get(spec.implementation_type)
        if builder is None:
            raise ValueError(
                f"Tool {spec.name!r}: unknown implementation_type "
                f"{spec.implementation_type!r}. Valid: {sorted(_BUILDERS)}"
            )
        built = builder(spec, ctx)
        if built is None:
            continue
        name, fn = built
        registry.register(name, fn)
        registered.append(name)
    return registered


__all__ = [
    "ToolSpec",
    "WiringContext",
    "register_tool_specs",
]
