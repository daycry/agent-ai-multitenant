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

import jsonschema
from shared_mcp import (
    MCPAuthError,
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPSession,
    MCPTool,
    MCPToolError,
    MCPTransportError,
    VaultResolver,
)

from agent_runtime.tools import ToolFn, ToolRegistry, ToolResult

logger = logging.getLogger(__name__)

# Fallback output ceiling (bytes, UTF-8) used when a tool's owning server
# config is unavailable (e.g. a tool registered against a closed runner).
# The live path reads the per-server `MCPServerConfig.max_output_bytes`
# (default 65536) instead — this constant only guards the degenerate case
# so output is *never* returned to the LLM completely unbounded.
_DEFAULT_MAX_OUTPUT_BYTES = 65536


def _truncate_output(text: str, max_bytes: int) -> str:
    """Cap a tool's text output at `max_bytes` UTF-8 bytes (mcp-tools-2).

    Mirrors :func:`agent_runtime.shell_exec._truncate` but is byte- (not
    char-) bounded because the audit caps MCP output in bytes and tool
    payloads are frequently non-ASCII JSON. The cut is made on a UTF-8
    encoding and decoded back ignoring a possibly split trailing
    multibyte sequence, then a visible marker is appended so both the
    agent and the model know data was omitted.
    """
    if max_bytes <= 0:
        return text
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    head = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{head}\n…[output truncated at {max_bytes} bytes]"


def _validate_args(args: dict[str, Any], input_schema: dict[str, Any]) -> str | None:
    """Validate `args` against a tool's JSON Schema (mcp-tools-1).

    Returns ``None`` when the args are valid (or when there is no
    meaningful schema to check against — an absent/empty schema, or one
    with neither ``properties`` nor ``type``, is treated as "anything
    goes" so tools that publish no schema still work). Otherwise returns
    a short human-readable error string describing the first violation.
    A malformed schema is *not* fatal: we log and skip rather than
    block a tool because the server published a bad schema.
    """
    if not input_schema or not isinstance(input_schema, dict):
        return None
    if "type" not in input_schema and "properties" not in input_schema:
        return None
    try:
        jsonschema.validate(instance=args, schema=input_schema)
    except jsonschema.ValidationError as exc:
        # `exc.message` is the human-facing reason; `exc.json_path`
        # (e.g. "$.query") points at the offending field.
        return f"{exc.json_path}: {exc.message}"
    except jsonschema.SchemaError as exc:
        logger.warning("ignoring malformed MCP tool input_schema: %s", exc)
        return None
    return None


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

    def __init__(self, vault_resolver: VaultResolver | None = None) -> None:
        """
        Args:
            vault_resolver: optional secret resolver. When a connected
                server declares ``auth_ref``, this resolver supplies
                the secret that gets merged into the runtime config
                (env for stdio, headers for http). Pass ``None`` (the
                default) when no connected server uses ``auth_ref`` —
                connecting one that does will then raise MCPAuthError,
                surfacing the misconfiguration instead of silently
                opening an unauthenticated session.
        """
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # One AsyncExitStack PER connected server (F23). A shared stack meant a
        # single server's half-open session (e.g. a connect that timed out
        # mid-`enter_async_context`) lived in the same stack as everyone else, so
        # the global teardown blocked ~10s on it. Isolating each session lets a
        # timeout be treated as that server's failure without penalising the
        # teardown of the servers that did connect cleanly.
        self._session_stacks: dict[str, AsyncExitStack] = {}
        self._sessions: dict[str, MCPSession] = {}
        self._tools: dict[str, list[MCPTool]] = {}
        self._started = False
        self._lock = threading.Lock()
        self._vault_resolver = vault_resolver

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
            # No global stack to enter: each server gets its own stack at
            # :meth:`connect` time (F23).
            self._started = True

    def close(self) -> None:
        """Close every open session and stop the background loop.
        Idempotent; safe to call after start failures."""
        with self._lock:
            if not self._started:
                return
            try:
                # Close each server's stack INDEPENDENTLY with its own timeout
                # budget (F23): one server that hangs on teardown cannot starve
                # the close of the others, and a server whose connect timed out
                # is not in this dict at all, so it never penalises teardown.
                for name, stack in list(self._session_stacks.items()):
                    try:
                        self._submit(stack.aclose()).result(timeout=10.0)
                    except Exception:
                        logger.exception("error closing MCP session stack for %r", name)
            finally:
                self._session_stacks.clear()
                self._sessions.clear()
                self._tools.clear()
                # Drain any still-in-flight tasks (a cancelled connect coroutine,
                # an out-of-band stack-discard from a timed-out server) so the
                # loop stops with no pending tasks — otherwise the interpreter
                # logs "Task was destroyed but it is pending" on teardown (F23).
                if self._loop is not None and self._loop.is_running():
                    try:
                        self._submit(self._drain_pending_tasks()).result(timeout=5.0)
                    except Exception:
                        logger.exception("error draining MCP loop tasks")
                    self._loop.call_soon_threadsafe(self._loop.stop)
                if self._thread is not None:
                    self._thread.join(timeout=5.0)
                self._loop = None
                self._thread = None
                self._started = False

    @staticmethod
    async def _drain_pending_tasks() -> None:
        """Cancel + await every other task on the loop (teardown hygiene, F23)."""
        current = asyncio.current_task()
        pending = [task for task in asyncio.all_tasks() if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

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

        # This server's own stack — the half-open session a timed-out connect
        # leaves behind stays isolated here, never in the global teardown path.
        stack = AsyncExitStack()

        async def _do() -> tuple[MCPSession, list[MCPTool]]:
            session = await stack.enter_async_context(
                MCPClient.connect(config, vault_resolver=self._vault_resolver)
            )
            tools = await session.list_tools()
            return session, tools

        future: Future[tuple[MCPSession, list[MCPTool]]] = self._submit(_do())
        try:
            session, tools = future.result(timeout=config.timeout_s * 2)
        except TimeoutError as exc:
            # Cancel the coroutine so it stops mid-`enter_async_context` instead
            # of leaking a subprocess/transport, then best-effort close whatever
            # the partial entry registered — without blocking (F23). The global
            # teardown is untouched: this server never made it into
            # `_session_stacks`, so it cannot stall `close()`.
            future.cancel()
            self._discard_stack(config.name, stack)
            raise MCPTransportError(
                f"server {config.name!r} connect timed out after {config.timeout_s * 2}s"
            ) from exc
        self._session_stacks[config.name] = stack
        self._sessions[config.name] = session
        self._tools[config.name] = tools
        return tools

    def tools(self, server_name: str) -> list[MCPTool]:
        """Return the cached tools for an already-connected server."""
        return list(self._tools.get(server_name, []))

    def max_output_bytes(self, server_name: str) -> int:
        """Per-server output ceiling (bytes) from its `MCPServerConfig`.

        Falls back to :data:`_DEFAULT_MAX_OUTPUT_BYTES` when the server is
        no longer connected (e.g. after :meth:`close`) so a tool's output
        is never returned to the LLM completely unbounded (mcp-tools-2).
        """
        session = self._sessions.get(server_name)
        if session is None:
            return _DEFAULT_MAX_OUTPUT_BYTES
        return session.config.max_output_bytes

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
    def _discard_stack(self, server_name: str, stack: AsyncExitStack) -> None:
        """Fire-and-forget teardown of a failed server's stack (F23).

        Scheduled on the background loop after a connect timeout; we do NOT
        block on it (the run must proceed) but attach a callback that drains any
        exception so it never surfaces as an "exception never retrieved"
        warning.
        """
        loop = self._loop
        if loop is None:
            return
        try:
            future = asyncio.run_coroutine_threadsafe(stack.aclose(), loop)
        except RuntimeError:  # loop already stopping — nothing to clean up
            return

        def _drain(fut: Future[Any]) -> None:
            try:
                fut.result()
            except Exception:
                logger.warning("error discarding timed-out MCP stack for %r", server_name)

        future.add_done_callback(_drain)

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
        registry.register(full_name, _make_tool_fn(runner, server_name, tool))
        names.append(full_name)
    return names


def _make_tool_fn(runner: MCPToolRunner, server_name: str, tool: MCPTool | str) -> ToolFn:
    """Closure that turns one MCP tool into a sync `ToolFn`.

    `tool` is the :class:`MCPTool` (so the closure can validate args
    against its ``input_schema``); a bare tool-name string is also
    accepted for callers/tests that only know the name (validation is
    then skipped for that tool).

    Two guards run *before* and *after* the wire call (task_06_14_12):

      - **pre (mcp-tools-1):** args are validated against the tool's
        declared JSON Schema; invalid args fold into
        ``ToolResult(ok=False)`` with a clear message and the wire call
        is never made — garbage never reaches the server.
      - **post (mcp-tools-2):** the tool's text output is capped at the
        owning server's ``max_output_bytes`` with a visible marker
        before it is parsed/returned, so a chatty or malicious server
        cannot exhaust the LLM context window.

    Error mapping:

      - `MCPToolError` (server said `isError=True`)   → ToolResult(ok=False)
      - `MCPTransportError` (network / timeout / SDK) → ToolResult(ok=False)
      - `MCPAuthError`                                → ToolResult(ok=False)
      - any other Exception                           → ToolResult(ok=False)

    The agent loop never sees a raised exception from the tool — it
    just gets a `ToolResult` with `ok=False` and a short error string.
    """
    tool_name = tool if isinstance(tool, str) else tool.name
    input_schema: dict[str, Any] = {} if isinstance(tool, str) else tool.input_schema

    def _tool(args: dict[str, Any]) -> ToolResult:
        violation = _validate_args(args, input_schema)
        if violation is not None:
            return ToolResult(
                ok=False,
                error=f"invalid arguments for {server_name}.{tool_name}: {violation}",
            )
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
        text = _truncate_output(text, runner.max_output_bytes(server_name))
        # Tools commonly return JSON-as-text; pre-parse so the agent
        # gets a dict rather than a raw string when possible. Truncated
        # output usually won't parse as JSON, so it stays a (capped)
        # string — exactly what we want the model to see.
        output: Any
        try:
            output = json.loads(text)
        except (ValueError, TypeError):
            output = text
        return ToolResult(ok=True, output=output)

    return _tool


__all__ = ["MCPToolRunner", "register_mcp_server"]
