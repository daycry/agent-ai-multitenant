"""MCP tools adapter for the agent loop (Plan 05 task_05_03).

The agent loop's `ToolRegistry` is **sync**: it stores
`ToolFn = Callable[[dict], ToolResult]` and the `act` graph node calls
them directly. The MCP client is **async** and lives behind an
``async with`` context manager. Connecting these two worlds is what
this module does.

How it works:

  - :class:`MCPToolRunner` owns a **background thread** with its own
    asyncio loop. The thread starts at :meth:`MCPToolRunner.start`
    and dies at :meth:`MCPToolRunner.close`.
  - :meth:`connect` opens one or more MCP sessions inside that loop
    using an `AsyncExitStack`, so they all close together at shutdown.
  - :meth:`call_tool` is sync from the caller's view: under the hood
    it submits the coroutine to the background loop via
    ``asyncio.run_coroutine_threadsafe`` and blocks until the
    timeout-bounded future resolves.
  - :func:`register_mcp_server` walks a server's tool list (from
    `MCPSession.list_tools()`) and registers each tool on the
    `ToolRegistry` as a `ToolFn` that delegates to the runner.

Why a background thread instead of `asyncio.run()`? Because the agent
loop may already be inside an event loop (the worker's
`conduct_execution` is an async function, and the LangGraph stream
runs inside it). Nesting `asyncio.run` from there raises
``RuntimeError: This event loop is already running``. A separate
thread with its own loop sidesteps the nesting entirely.

Naming: the registered tool name is ``<server>.<tool>`` (e.g.
``github.search_repos``). Namespacing avoids collisions when two
servers expose tools with the same name (very common —
``filesystem-mcp`` and ``gdrive-mcp`` both have ``read_file``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from concurrent.futures import Future
from contextlib import AsyncExitStack
from typing import Any

from shared_mcp import (
    MCPAuthError,
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPSession,
    MCPTool,
    MCPToolError,
    MCPTransportError,
)

from agent_runtime.tools import ToolFn, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sync-over-async bridge
# ---------------------------------------------------------------------------
class MCPToolRunner:
    """Holds N open MCP sessions and exposes them to sync callers.

    Lifecycle:

        runner = MCPToolRunner()
        runner.start()
        try:
            runner.connect(github_config)
            runner.connect(filesystem_config)
            register_mcp_server(registry, runner, "github", runner.tools("github"))
            register_mcp_server(registry, runner, "filesystem", runner.tools("filesystem"))
            # ... agent loop runs, calls registry.call("github.search_repos", ...)
        finally:
            runner.close()

    Not safe to share across threads other than the one driving the
    sync registry (typically the agent loop's thread).
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stack: AsyncExitStack | None = None
        self._sessions: dict[str, MCPSession] = {}
        self._tools: dict[str, list[MCPTool]] = {}
        self._started = False
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        """Spin up the background asyncio loop. Idempotent."""
        with self._lock:
            if self._started:
                return
            ready = threading.Event()

            def _run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()
                loop.close()

            self._thread = threading.Thread(target=_run, name="mcp-tool-runner-loop", daemon=True)
            self._thread.start()
            ready.wait(timeout=5.0)
            assert self._loop is not None, "background loop never started"
            self._stack = AsyncExitStack()

            async def _enter_stack() -> None:
                assert self._stack is not None
                await self._stack.__aenter__()

            self._submit(_enter_stack()).result(timeout=5.0)
            self._started = True

    def close(self) -> None:
        """Close every open session and stop the background loop.
        Idempotent; safe to call after start failures."""
        with self._lock:
            if not self._started:
                return
            try:
                if self._stack is not None:
                    self._submit(self._stack.__aexit__(None, None, None)).result(timeout=10.0)
            except Exception:
                logger.exception("error closing MCP exit stack")
            finally:
                self._stack = None
                self._sessions.clear()
                self._tools.clear()
                if self._loop is not None and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._loop.stop)
                if self._thread is not None:
                    self._thread.join(timeout=5.0)
                self._loop = None
                self._thread = None
                self._started = False

    def __enter__(self) -> MCPToolRunner:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- per-server ops ----------------------------------------------------
    def connect(self, config: MCPServerConfig) -> list[MCPTool]:
        """Open a session against `config` and pre-fetch its tool list.

        Returns the tools so the caller can register them on the agent's
        `ToolRegistry`. Subsequent calls to :meth:`call_tool` reuse the
        same session — much cheaper than re-opening per call.
        """
        if not self._started:
            raise RuntimeError("MCPToolRunner.start() must be called first")
        if config.name in self._sessions:
            raise ValueError(f"server {config.name!r} is already connected")

        async def _do() -> tuple[MCPSession, list[MCPTool]]:
            assert self._stack is not None
            session = await self._stack.enter_async_context(MCPClient.connect(config))
            tools = await session.list_tools()
            return session, tools

        session, tools = self._submit(_do()).result(timeout=config.timeout_s * 2)
        self._sessions[config.name] = session
        self._tools[config.name] = tools
        return tools

    def tools(self, server_name: str) -> list[MCPTool]:
        """Return the cached tools for an already-connected server."""
        return list(self._tools.get(server_name, []))

    def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        """Synchronous call. Returns the tool's text output.

        Raises:
            KeyError: `server_name` was never connected.
            MCPToolError / MCPTransportError / MCPAuthError: propagated
                from the underlying async session unchanged.
        """
        session = self._sessions.get(server_name)
        if session is None:
            raise KeyError(f"server {server_name!r} not connected")

        async def _do() -> str:
            result = await session.call_tool(tool_name, arguments)
            return result.content

        future: Future[str] = self._submit(_do())
        try:
            return future.result(timeout=session.config.timeout_s)
        except TimeoutError as exc:
            future.cancel()
            raise MCPTransportError(
                f"tool {server_name}.{tool_name} timed out after " f"{session.config.timeout_s}s"
            ) from exc

    # -- internal ----------------------------------------------------------
    def _submit(self, coro: Any) -> Future[Any]:
        """Submit a coroutine to the background loop. Returns a
        concurrent.futures.Future the caller blocks on."""
        loop = self._loop
        if loop is None:
            raise RuntimeError("background loop not running")
        return asyncio.run_coroutine_threadsafe(coro, loop)


# ---------------------------------------------------------------------------
# Adapter: MCP tool → ToolFn registered on the agent's ToolRegistry
# ---------------------------------------------------------------------------
def register_mcp_server(
    registry: ToolRegistry,
    runner: MCPToolRunner,
    server_name: str,
    tools: list[MCPTool] | None = None,
    *,
    namespace_separator: str = ".",
) -> list[str]:
    """Register every tool of an already-connected MCP server.

    The registered name is ``<server_name><sep><tool_name>`` (default
    separator is ``.``). Tools whose name already collides with
    something in the registry are replaced — this is intentional so
    re-connecting the same server is idempotent (you can rebuild the
    registry without leftover stale entries).

    Returns the list of registered tool names so the caller can log /
    test what landed.
    """
    if tools is None:
        tools = runner.tools(server_name)
    names: list[str] = []
    for tool in tools:
        full_name = f"{server_name}{namespace_separator}{tool.name}"
        registry.register(full_name, _make_tool_fn(runner, server_name, tool.name))
        names.append(full_name)
    return names


def _make_tool_fn(runner: MCPToolRunner, server_name: str, tool_name: str) -> ToolFn:
    """Closure that turns one MCP tool into a sync `ToolFn`.

    Error mapping:

      - `MCPToolError` (server said `isError=True`)   → ToolResult(ok=False)
      - `MCPTransportError` (network / timeout / SDK) → ToolResult(ok=False)
      - `MCPAuthError`                                → ToolResult(ok=False)
      - any other Exception                           → ToolResult(ok=False)

    The agent loop never sees a raised exception from the tool — it
    just gets a `ToolResult` with `ok=False` and a short error string.
    """

    def _tool(args: dict[str, Any]) -> ToolResult:
        try:
            text = runner.call_tool(server_name, tool_name, args)
        except MCPToolError as exc:
            return ToolResult(ok=False, error=f"mcp tool error: {exc}")
        except MCPAuthError as exc:
            return ToolResult(ok=False, error=f"mcp auth error: {exc}")
        except MCPTransportError as exc:
            return ToolResult(ok=False, error=f"mcp transport error: {exc}")
        except MCPError as exc:
            return ToolResult(ok=False, error=f"mcp error: {exc}")
        # Tools commonly return JSON-as-text; pre-parse so the agent
        # gets a dict rather than a raw string when possible.
        output: Any
        try:
            output = json.loads(text)
        except (ValueError, TypeError):
            output = text
        return ToolResult(ok=True, output=output)

    return _tool


__all__ = ["MCPToolRunner", "register_mcp_server"]
