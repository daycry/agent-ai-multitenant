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


def transport_root_cause(exc: BaseException) -> BaseException | None:
    """La excepción ORIGINAL debajo de un `MCPError`, o ``None`` si no la hay.

    `MCPClient.connect` normaliza todo fallo del camino de conexión a
    :class:`MCPTransportError`, y lo que envuelve suele ser un
    `BaseExceptionGroup` (el TaskGroup de anyio del SDK), a veces anidado, a
    veces con otro `MCPError` intermedio dentro. Quien tiene que decidir «¿esto
    fue el proxy rechazando el CONNECT o el servidor rechazando la credencial?»
    necesita mirar el TIPO de la hoja —`httpx.ProxyError`, `httpx.ConnectError`,
    `httpx.HTTPStatusError`— y no olfatear el texto del mensaje (ADR 0165,
    addendum A2). Esta función baja por `__cause__` y por los grupos hasta la
    primera hoja que no sea ni grupo ni `MCPError`.
    """
    seen: set[int] = set()
    pendientes: list[BaseException] = [exc]
    while pendientes:
        actual = pendientes.pop(0)
        if id(actual) in seen:
            continue
        seen.add(id(actual))
        if isinstance(actual, BaseExceptionGroup):
            pendientes = list(actual.exceptions) + pendientes
            continue
        if isinstance(actual, MCPError):
            causa = actual.__cause__
            if causa is not None:
                pendientes.insert(0, causa)
            continue
        return actual
    return None


__all__ = [
    "MCPAuthError",
    "MCPError",
    "MCPToolError",
    "MCPTransportError",
    "transport_root_cause",
]
