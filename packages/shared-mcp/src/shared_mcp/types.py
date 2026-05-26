"""Typed config + tool data structures (Plan 05 task_05_01).

`MCPServerConfig` is the shape `Project.mcp_servers` (JSONB) holds —
each project lists 0..N servers it wants its agents to talk to. The
three transports map 1:1 to fields on this dataclass:

  - ``stdio``            spawns a subprocess via `command` + `args` + `env`
  - ``sse``              HTTP GET that streams Server-Sent Events from `url`
  - ``streamable_http``  HTTP POST round-trips at `url` (the newer transport)

`auth_ref` is a *pointer* to a Vault secret (e.g. ``vault:secret/data/
mcp/github/<project_id>``). Plan 05 task_05_05 wires the resolution
at connect time so this package never sees the cleartext token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# The three transports the MCP spec defines. We expose them as a
# typed literal so callers get static checking without depending on
# the SDK's enum (which has changed shape a few times).
Transport = Literal["stdio", "sse", "streamable_http"]


@dataclass(frozen=True)
class MCPServerConfig:
    """One MCP server a project declares.

    Validation lives in `task_05_04` (Pydantic model + schema on top
    of `Project.mcp_servers` JSONB); this dataclass is the runtime
    shape the client consumes.
    """

    # Human-facing identifier within a project. Two servers in the
    # same project must not share name.
    name: str
    transport: Transport

    # ----- stdio fields -----
    # Path or shell command to spawn. Required when transport=stdio,
    # ignored otherwise.
    command: str | None = None
    args: tuple[str, ...] = field(default_factory=tuple)
    # Extra env vars merged on top of the worker's env when spawning.
    # Useful for things like `GITHUB_TOKEN` for github-mcp.
    env: dict[str, str] = field(default_factory=dict)

    # ----- sse / streamable_http fields -----
    # Full URL the transport hits. Required when transport in
    # {sse, streamable_http}, ignored when transport=stdio.
    url: str | None = None
    # Extra headers (e.g. `Authorization: Bearer ...`) sent on every
    # HTTP roundtrip. The Vault-injected auth ends up here.
    headers: dict[str, str] = field(default_factory=dict)

    # ----- auth -----
    # Vault path that resolves at connect time to the secret
    # injected into `env` (stdio) or `headers` (HTTP). None means
    # "this server needs no auth" — useful for local dev or for
    # servers that already trust the network they live on.
    auth_ref: str | None = None

    # ----- ergonomics -----
    # Per-server timeout (seconds) for any single tool call. Tools
    # that hang past this raise MCPTransportError. Default 30s per
    # Plan 05 decision "Timeouts agresivos por tool (default 30s)
    # configurables".
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError("transport='stdio' requires `command`")
            if self.url is not None:
                # Not fatal but a config smell — warn loudly via the
                # exception so it shows up at JSON validation time.
                raise ValueError("transport='stdio' must not set `url`")
        else:  # sse | streamable_http
            if not self.url:
                raise ValueError(f"transport={self.transport!r} requires `url`")
            if self.command is not None:
                raise ValueError(f"transport={self.transport!r} must not set `command`")


@dataclass(frozen=True)
class MCPTool:
    """One tool the server advertises during `tools/list`.

    The agent-runtime adapter (task_05_03) turns each of these into a
    `ToolFn` registered on its `ToolRegistry` — same shape as the
    builtin tools (`echo`, `shell_exec`, `memory_recall`).
    """

    name: str
    description: str | None
    # JSON Schema dict the server publishes for the tool's args.
    # The agent's planner uses this to know what to pass.
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class MCPToolResult:
    """One tool's response. Mirrors the shape of MCP's `CallToolResult`."""

    # Concatenated text content from all `content` blocks of type text.
    # Multimedia (image, resource) blocks land in `raw` for now.
    content: str
    # True when the server flipped `isError=True` in the response.
    # `MCPClient.call_tool` raises MCPToolError when this is True,
    # so callers don't normally see this field — but the raw result
    # is here for debugging.
    is_error: bool = False
    # Untouched JSON-RPC payload — for callers that need media etc.
    raw: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "MCPServerConfig",
    "MCPTool",
    "MCPToolResult",
    "Transport",
]
