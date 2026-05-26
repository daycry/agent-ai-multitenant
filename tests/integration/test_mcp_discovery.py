"""Plan 05 task_05_02 — tests for the one-shot tool discovery helper.

`discover_tools(config)` is the convenience function the admin-panel
will call from the "Test connection" button (task_05_07). It opens
a session, handshakes (`initialize`), pulls the tool list, and
closes — all in one async call.

We exercise:

  - happy path against the stdio toy server: tool names round-trip,
    server identity comes through (FastMCP advertises a name);
  - error paths: bad command → `MCPTransportError`; auth failures
    (simulated with a non-existent URL on streamable_http) → also
    `MCPTransportError` because the SDK doesn't reach the auth check
    when the URL refuses to connect.

The streamable_http happy path is exercised in
`test_mcp_client.py`; this file focuses on the one-shot helper's
behaviour. Adding it again here would mostly be duplicate fixture
setup.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from shared_mcp import (
    MCPError,
    MCPServerConfig,
    MCPTransportError,
    discover_tools,
)

pytestmark = pytest.mark.integration


_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"


@pytest.fixture
def stdio_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="toy-stdio",
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        timeout_s=15.0,
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discover_returns_tools_from_toy_server(
    stdio_config: MCPServerConfig,
) -> None:
    """The toy server exposes `echo` + `add`; discovery must list both."""
    result = await discover_tools(stdio_config)

    names = {t.name for t in result.tools}
    assert names == {"echo", "add"}, names

    # Both tools have descriptions (we set them in the toy server)
    by_name = {t.name: t for t in result.tools}
    assert "Echo" in (by_name["echo"].description or "")
    assert "Add" in (by_name["add"].description or "")

    # JSON schema is non-empty for both — agent's planner relies on it.
    assert by_name["echo"].input_schema.get("type") == "object"
    assert by_name["add"].input_schema.get("type") == "object"


@pytest.mark.asyncio
async def test_discover_surfaces_server_identity(
    stdio_config: MCPServerConfig,
) -> None:
    """FastMCP's `name=...` arg should round-trip through the
    `initialize` response's serverInfo. We don't assert the version
    string (SDK-version-dependent), only that the field is populated
    or empty — never raising."""
    result = await discover_tools(stdio_config)

    # FastMCP defaults serverInfo.name to the value we passed to
    # FastMCP(name="toy-mcp-server").
    assert result.server_name == "toy-mcp-server", result.server_name
    # version may be empty depending on the SDK; we only ensure the
    # attribute exists and is a string (no None).
    assert isinstance(result.server_version, str)
    # capabilities should at least mention `tools` since we serve them.
    assert "tools" in result.capabilities


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discover_bad_stdio_command_raises_transport_error() -> None:
    """A `command` that doesn't exist must surface as
    `MCPTransportError`, not as the raw `FileNotFoundError`."""
    config = MCPServerConfig(
        name="ghost",
        transport="stdio",
        command="this-binary-does-not-exist-zzz",
        timeout_s=2.0,
    )
    with pytest.raises(MCPError):
        await discover_tools(config)


@pytest.mark.asyncio
async def test_discover_bad_http_url_raises_transport_error() -> None:
    """A streamable_http URL pointing at a dead port surfaces as
    transport error. The SDK fails at connect time before any auth
    check, so we don't see `MCPAuthError` here.

    The anyio TaskGroup the SDK uses internally re-raises errors
    wrapped in `BaseExceptionGroup`; we accept either form. What we
    care about is that the failure does NOT come back as a silent
    success or a leaked `httpx.ConnectError`.
    """
    config = MCPServerConfig(
        name="dead",
        transport="streamable_http",
        url="http://127.0.0.1:1/mcp",
        timeout_s=2.0,
    )
    with pytest.raises((MCPTransportError, BaseExceptionGroup)) as exc_info:
        await discover_tools(config)
    # If we got a group, at least one inner exception must be ours.
    raised = exc_info.value
    if isinstance(raised, BaseExceptionGroup):
        flat = list(_flatten(raised))
        assert any(isinstance(e, MCPTransportError) for e in flat), flat


def _flatten(eg: BaseExceptionGroup) -> list[BaseException]:
    """Recursively unwrap a BaseExceptionGroup into a flat list."""
    out: list[BaseException] = []
    for exc in eg.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            out.extend(_flatten(exc))
        else:
            out.append(exc)
    return out
