"""Plan 05 task_05_01 — round-trip tests for the unified MCP client.

Each transport (stdio / sse / streamable_http) is exercised against
the same toy server (`_toy_mcp_server.py`) that exposes `echo` + `add`
tools. The test asserts:

  - `list_tools()` returns the two tools the server advertises;
  - `call_tool('add', {a, b})` round-trips and returns the integer
    sum as text;
  - `call_tool(...)` on a nonexistent tool surfaces as MCPToolError or
    MCPTransportError (no silent failure).

The stdio transport spawns a fresh Python subprocess. The two HTTP
transports launch the server in-process on a randomly-allocated free
port and tear it down on test exit.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from shared_mcp import (
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPToolError,
)

pytestmark = pytest.mark.integration

# Path to the toy server script — used by stdio mode as the subprocess
# entry point, and imported by HTTP modes to build a server in-process.
_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _free_port() -> int:
    """Find a free TCP port on localhost. Not race-safe (the OS could
    re-give the port between this check and the bind), but for one
    test process it's fine."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _wait_until_listening(port: int, timeout_s: float = 10.0) -> None:
    """Poll the port every 100ms until a TCP connect succeeds or the
    timeout fires. Used to wait for the in-process HTTP server to
    start before pointing the client at it."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise RuntimeError(f"port {port} never started listening within {timeout_s}s")


async def _spawn_http_server(transport: str, port: int) -> asyncio.subprocess.Process:
    """Spawn the toy server in subprocess mode (sse / streamable_http).

    Subprocess (instead of in-process task) so the test event loop
    stays clean and the server's uvicorn runs in its own loop.
    """
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(_TOY_SERVER),
        "--transport",
        transport,
        "--port",
        str(port),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await _wait_until_listening(port)
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def stdio_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="toy-stdio",
        transport="stdio",
        command=sys.executable,
        args=(str(_TOY_SERVER), "--transport", "stdio"),
        timeout_s=15.0,
    )


@pytest_asyncio.fixture
async def sse_server() -> AsyncIterator[MCPServerConfig]:
    port = _free_port()
    proc = await _spawn_http_server("sse", port)
    try:
        yield MCPServerConfig(
            name="toy-sse",
            transport="sse",
            url=f"http://127.0.0.1:{port}/sse",
            timeout_s=15.0,
        )
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:  # - cleanup path
            proc.kill()
            await proc.wait()


@pytest_asyncio.fixture
async def streamable_http_server() -> AsyncIterator[MCPServerConfig]:
    port = _free_port()
    proc = await _spawn_http_server("streamable_http", port)
    try:
        yield MCPServerConfig(
            name="toy-streamable",
            transport="streamable_http",
            url=f"http://127.0.0.1:{port}/mcp",
            timeout_s=15.0,
        )
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()


# ---------------------------------------------------------------------------
# Round-trip per transport
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stdio_list_tools_then_call(stdio_config: MCPServerConfig) -> None:
    async with MCPClient.connect(stdio_config) as session:
        tools = await session.list_tools()
        names = {t.name for t in tools}
        assert {"echo", "add"}.issubset(names), names

        # `echo` round-trip
        echoed = await session.call_tool("echo", {"text": "hello"})
        assert "hello" in echoed.content
        assert echoed.is_error is False
        # mcp-tools-3: the untrusted raw JSON-RPC payload is no longer
        # retained on the result.
        assert not hasattr(echoed, "raw")

        # `add` round-trip — server returns the sum as text
        summed = await session.call_tool("add", {"a": 2, "b": 3})
        assert "5" in summed.content


@pytest.mark.asyncio
async def test_sse_list_tools_then_call(sse_server: MCPServerConfig) -> None:
    async with MCPClient.connect(sse_server) as session:
        tools = await session.list_tools()
        assert {"echo", "add"}.issubset({t.name for t in tools})
        result = await session.call_tool("add", {"a": 7, "b": 4})
        assert "11" in result.content


@pytest.mark.asyncio
async def test_streamable_http_list_tools_then_call(
    streamable_http_server: MCPServerConfig,
) -> None:
    async with MCPClient.connect(streamable_http_server) as session:
        tools = await session.list_tools()
        assert {"echo", "add"}.issubset({t.name for t in tools})
        result = await session.call_tool("echo", {"text": "via http"})
        assert "via http" in result.content


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_call_unknown_tool_raises(stdio_config: MCPServerConfig) -> None:
    """A tool the server doesn't expose surfaces as MCPToolError
    (server returned isError=True). It must NOT be swallowed."""
    async with MCPClient.connect(stdio_config) as session:
        with pytest.raises(MCPToolError):
            await session.call_tool("does_not_exist", {})


@pytest.mark.asyncio
async def test_bad_stdio_command_raises_transport_error() -> None:
    """A `command` that doesn't exist must raise MCPTransportError, not
    propagate the raw OSError."""
    config = MCPServerConfig(
        name="ghost",
        transport="stdio",
        command="this-binary-does-not-exist-zzz",
        timeout_s=2.0,
    )
    with pytest.raises(MCPError):
        async with MCPClient.connect(config) as _session:
            pass  # pragma: no cover - we expect the enter to fail


# ---------------------------------------------------------------------------
# Config validation (pure)
# ---------------------------------------------------------------------------
def test_stdio_config_requires_command() -> None:
    with pytest.raises(ValueError, match="requires `command`"):
        MCPServerConfig(name="x", transport="stdio")


def test_sse_config_requires_url() -> None:
    with pytest.raises(ValueError, match="requires `url`"):
        MCPServerConfig(name="x", transport="sse")


def test_streamable_http_config_rejects_command() -> None:
    with pytest.raises(ValueError, match="must not set `command`"):
        MCPServerConfig(
            name="x",
            transport="streamable_http",
            url="http://x",
            command="ghost",
        )
