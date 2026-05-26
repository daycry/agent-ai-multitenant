"""Unified async MCP client (Plan 05 task_05_01).

The agentic platform talks to MCP servers (Model Context Protocol —
the open spec Anthropic + others maintain at modelcontextprotocol.io)
through this package. It wraps the official `mcp` Python SDK with:

  - a uniform `MCPClient` API that hides which transport
    (stdio / sse / streamable_http) a given server uses;
  - a typed :class:`MCPServerConfig` dataclass projects declare in
    `Project.mcp_servers` (Plan 05 task_05_04);
  - exception hierarchy so callers can distinguish transport, auth
    and tool errors without catching `Exception`.

The Vault-backed auth injection lands in task_05_05; the
`tools/list` + `tools/call` adapter that turns MCP tools into agent
tools lands in task_05_02 / task_05_03.
"""

from shared_mcp.client import MCPClient, MCPSession
from shared_mcp.exceptions import (
    MCPAuthError,
    MCPError,
    MCPToolError,
    MCPTransportError,
)
from shared_mcp.types import (
    MCPServerConfig,
    MCPTool,
    MCPToolResult,
    Transport,
)

__all__ = [
    "MCPAuthError",
    "MCPClient",
    "MCPError",
    "MCPServerConfig",
    "MCPSession",
    "MCPTool",
    "MCPToolError",
    "MCPToolResult",
    "MCPTransportError",
    "Transport",
]
