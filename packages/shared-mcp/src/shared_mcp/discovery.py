"""One-shot tool discovery (Plan 05 task_05_02).

The MCP spec's connection lifecycle is:

  1. Open transport (stdio subprocess / SSE stream / HTTP roundtrip).
  2. `initialize` — handshake. Server announces capabilities + version.
  3. `tools/list` — server lists the tools it offers.
  4. ... (any number of tools/call invocations) ...
  5. Close transport.

For long-running agents the right pattern is to keep the session open
for the whole run (Plan 05 task_05_03 builds that on top of
:class:`MCPClient`). But there's a simpler use case that pops up a lot:

  - "Test connection" buttons in the admin-panel (task_05_07).
  - Project configuration validation when an operator saves a new
    MCP server entry (task_05_04 / task_05_06).
  - Catalog seeding: when we add a verified server template
    (task_05_08 docling-mcp, task_05_09 github-mcp, etc.) we want
    one-shot "what tools does this server expose" without writing
    the open/close boilerplate every time.

`discover_tools(config)` is that one-shot: connect → initialize →
list_tools → close, return the list. Errors propagate from the
:mod:`shared_mcp.exceptions` hierarchy so the caller can
distinguish "the credentials are wrong" from "the server is down"
from "the tool list came back empty".

`DiscoveryResult` carries both the tools and the server's own
metadata (name + version from the `initialize` response). The
admin-panel uses the server metadata to show "Connected to <name>
v<version>" alongside the tool list.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared_mcp.client import MCPClient
from shared_mcp.types import MCPServerConfig, MCPTool


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of a one-shot discovery.

    `server_name` and `server_version` come from the MCP `initialize`
    response's `serverInfo` field. They are best-effort; some servers
    don't populate them (we default to empty strings rather than
    fail — the tool list is the load-bearing part).
    """

    tools: list[MCPTool]
    server_name: str = ""
    server_version: str = ""
    # The raw `instructions` field from the initialize response. Some
    # MCP servers use this to tell the agent how to think about their
    # tools (one-paragraph system prompt). We expose it verbatim;
    # consumers can stick it in the agent's context.
    server_instructions: str | None = None
    # Capabilities dict from the initialize response (tools / prompts
    # / resources etc.). We surface the keys the caller might filter
    # on; downstream tasks will consume specific entries.
    capabilities: dict[str, object] = field(default_factory=dict)


async def discover_tools(config: MCPServerConfig) -> DiscoveryResult:
    """Open a session, run the MCP handshake, list tools, close.

    Args:
        config: Where to connect + how. The same `MCPServerConfig`
            the agent runtime would use to keep a long-lived session.

    Returns:
        A :class:`DiscoveryResult` with the tools the server
        advertises + the server's self-reported identity.

    Raises:
        MCPTransportError: connection / transport problem.
        MCPAuthError:      server rejected our credentials.
        (Other errors from the SDK bubble up as MCPTransportError.)
    """
    async with MCPClient.connect(config) as session:
        tools = await session.list_tools()
        info = _extract_server_info(session.init_result)
        return DiscoveryResult(
            tools=tools,
            server_name=info.name,
            server_version=info.version,
            server_instructions=info.instructions,
            capabilities=info.capabilities,
        )


@dataclass(frozen=True)
class _ServerInfo:
    """Plain-typed projection of the SDK's `InitializeResult`."""

    name: str = ""
    version: str = ""
    instructions: str | None = None
    capabilities: dict[str, object] = field(default_factory=dict)


def _extract_server_info(init_result: object) -> _ServerInfo:
    """Project the SDK's `InitializeResult` to plain primitives.

    Missing fields become empty strings rather than raising —
    discovery should still succeed for old servers that don't
    advertise their identity. The SDK uses pydantic so we lean on
    `model_dump()` for the capabilities dict.
    """
    if init_result is None:
        return _ServerInfo()
    server_info = getattr(init_result, "serverInfo", None) or getattr(
        init_result, "server_info", None
    )
    capabilities = getattr(init_result, "capabilities", None)
    instructions_raw = getattr(init_result, "instructions", None)
    name = ""
    version = ""
    if server_info is not None:
        name = str(getattr(server_info, "name", "") or "")
        version = str(getattr(server_info, "version", "") or "")
    caps: dict[str, object] = {}
    if capabilities is not None and hasattr(capabilities, "model_dump"):
        dumped = capabilities.model_dump(exclude_none=True)
        if isinstance(dumped, dict):
            caps = dumped
    elif isinstance(capabilities, dict):
        caps = capabilities
    return _ServerInfo(
        name=name,
        version=version,
        instructions=instructions_raw if isinstance(instructions_raw, str) else None,
        capabilities=caps,
    )


__all__ = ["DiscoveryResult", "discover_tools"]
