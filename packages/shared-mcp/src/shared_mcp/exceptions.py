"""Exception hierarchy for MCP client errors (Plan 05 task_05_01).

Callers should catch the most specific type they can handle and let
the rest propagate. The agent runtime's tool registry maps these to
``ToolResult.ok=False`` with the right `status_code` analogue.
"""

from __future__ import annotations


class MCPError(Exception):
    """Base for anything that goes wrong talking to an MCP server."""


class MCPTransportError(MCPError):
    """The underlying transport (stdio subprocess, SSE, streamable HTTP)
    refused, dropped or timed out before the JSON-RPC layer could
    deliver a response. Network problem, server crash, bad URL."""


class MCPAuthError(MCPError):
    """The transport connected but the server rejected our credentials.
    Token expired, scope insufficient, server requires a header we
    didn't send. Vault auth injection (task_05_05) is the typical
    fix surface."""


class MCPToolError(MCPError):
    """The MCP server accepted the call but the tool itself returned
    an error (`isError=True` in the spec). Distinct from
    :class:`MCPTransportError` — the protocol round-trip succeeded;
    the tool's business logic failed."""


__all__ = [
    "MCPAuthError",
    "MCPError",
    "MCPToolError",
    "MCPTransportError",
]
