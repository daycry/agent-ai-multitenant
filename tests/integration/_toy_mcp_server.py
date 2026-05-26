"""A tiny MCP server used by tests/integration/test_mcp_client.py.

The server exposes two tools (`echo` and `add`) and supports the three
canonical transports (stdio / sse / streamable_http). The test code
either spawns this as a subprocess (stdio mode) or imports FastMCP
directly and serves it in an asyncio task (HTTP modes).

Why not a fixture inside the test file? The stdio transport spawns a
fresh Python process; that process needs to be able to import the
server entry-point without dragging in pytest. So this lives as a
plain script invocable with ``python tests/integration/_toy_mcp_server.py
--transport stdio``.
"""

from __future__ import annotations

import argparse
import sys

from mcp.server.fastmcp import FastMCP


def build_server() -> FastMCP:
    """Build a FastMCP with two deterministic tools.

    Tests assert that `list_tools` sees both names and that
    `call_tool` round-trips the result.
    """
    server: FastMCP = FastMCP(name="toy-mcp-server")

    @server.tool(description="Echo the input string back.")
    def echo(text: str) -> str:
        return text

    @server.tool(description="Add two integers.")
    def add(a: int, b: int) -> int:
        return a + b

    return server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable_http"),
        default="stdio",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Only used by sse / streamable_http modes.",
    )
    args = parser.parse_args()

    server = build_server()
    if args.transport == "stdio":
        server.run("stdio")
    elif args.transport == "sse":
        server.settings.host = "127.0.0.1"
        server.settings.port = args.port
        server.run("sse")
    elif args.transport == "streamable_http":
        server.settings.host = "127.0.0.1"
        server.settings.port = args.port
        server.run("streamable-http")
    return 0


if __name__ == "__main__":
    sys.exit(main())
