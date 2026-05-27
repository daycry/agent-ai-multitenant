"""api_server MCP integration surface (Plan 05 Fase B).

Sub-package that owns the *server-side* validation + persistence layer
for MCP server configurations declared per-project. The async client
itself lives in :mod:`shared_mcp`; here we wrap it with:

* :class:`config.MCPServerConfigModel` — Pydantic mirror of
  :class:`shared_mcp.types.MCPServerConfig`, used to validate the
  ``Project.mcp_servers`` JSONB field on POST/PUT.
* :func:`config.validate_mcp_servers_payload` — bulk validator that
  enforces per-project invariants (unique names) and re-serialises
  to canonical dicts before they hit the DB.
"""

from __future__ import annotations

from api_server.mcp.config import (
    MCPServerConfigModel,
    validate_mcp_servers_payload,
)

__all__ = ["MCPServerConfigModel", "validate_mcp_servers_payload"]
