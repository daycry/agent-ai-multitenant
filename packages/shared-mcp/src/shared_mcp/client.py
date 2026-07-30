"""Async MCP client (Plan 05 task_05_01).

Thin wrapper over the official `mcp` SDK that hides which transport
(stdio / sse / streamable_http) a server uses behind one async API:

    cfg = MCPServerConfig(name="github", transport="stdio",
                          command="github-mcp", args=("--port", "0"))
    async with MCPClient.connect(cfg) as session:
        tools = await session.list_tools()
        result = await session.call_tool("search_repos",
                                         {"query": "agentic"})

Errors fold into the :mod:`shared_mcp.exceptions` hierarchy so the
agent-runtime tool adapter (task_05_03) can map them onto
``ToolResult.ok=False`` with sensible messages.

Vault-backed auth injection (task_05_05): when `config.auth_ref` is
set, pass a :class:`shared_mcp.auth.VaultResolver` to :meth:`connect`
and the resolver's key/value pairs get merged into ``env`` (stdio) or
``headers`` (http transports) before the transport is opened.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client

from shared_mcp.auth import VaultResolver, apply_vault_auth
from shared_mcp.exceptions import (
    MCPAuthError,
    MCPError,
    MCPToolError,
    MCPTransportError,
)
from shared_mcp.types import MCPServerConfig, MCPTool, MCPToolResult, Transport

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx


@dataclass
class MCPSession:
    """One live, initialised session against an MCP server.

    Returned by :meth:`MCPClient.connect`. The two public methods
    (`list_tools`, `call_tool`) cover what task_05_02 / task_05_03
    need — anything else from the SDK is available via `.raw`.

    `init_result` is the `InitializeResult` pydantic model the server
    returned during the handshake — useful for `discover_tools`
    (task_05_02) to surface `serverInfo` without re-querying.
    """

    config: MCPServerConfig
    raw: ClientSession
    init_result: Any = None

    async def list_tools(self) -> list[MCPTool]:
        """Return the tools the server advertises (`tools/list`).

        Raises :class:`MCPTransportError` if the SDK call fails. The
        SDK's `ToolsResult.tools` carries `name`, `description` and
        `inputSchema`; we project to our :class:`MCPTool`.
        """
        try:
            response = await self.raw.list_tools()
        except BaseExceptionGroup as eg:
            # Same reasoning as in MCPClient.connect — the SDK's
            # anyio TaskGroup can wrap connection errors in a
            # BaseExceptionGroup (BaseException, not Exception).
            inner = _first_inner(eg) or eg
            raise MCPTransportError(f"list_tools failed: {inner}") from eg
        except Exception as exc:
            raise MCPTransportError(f"list_tools failed: {exc}") from exc
        return [
            MCPTool(
                name=t.name,
                description=t.description,
                input_schema=t.inputSchema or {},
            )
            for t in response.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> MCPToolResult:
        """Invoke one tool by name (`tools/call`).

        Raises:
            MCPToolError: server returned `isError=True` (tool's
                business logic refused — bad args, no permission, etc.)
            MCPTransportError: anything else went wrong at the wire
                level (SDK exception, timeout, server crash).
        """
        try:
            response = await self.raw.call_tool(name, arguments=arguments or {})
        except BaseExceptionGroup as eg:
            inner = _first_inner(eg) or eg
            raise MCPTransportError(f"call_tool({name!r}) failed: {inner}") from eg
        except Exception as exc:
            raise MCPTransportError(f"call_tool({name!r}) failed: {exc}") from exc
        # Concatenate text blocks; multimedia/resource blocks are dropped.
        # We deliberately do NOT keep the raw payload (mcp-tools-3): it is
        # untrusted server data that nothing reads and would leak if the
        # result were ever logged or persisted.
        text_parts: list[str] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(getattr(block, "text", ""))
        text = "".join(text_parts)
        if response.isError:
            raise MCPToolError(f"tool {name!r} returned isError=True: {text[:200]}")
        return MCPToolResult(content=text, is_error=False)


class MCPClient:
    """Factory for :class:`MCPSession`. The class itself is a
    namespace — there's no instance state worth keeping; everything
    lives inside the `connect` async context manager.
    """

    @staticmethod
    @asynccontextmanager
    async def connect(
        config: MCPServerConfig,
        *,
        vault_resolver: VaultResolver | None = None,
        auth: httpx.Auth | None = None,
    ) -> AsyncIterator[MCPSession]:
        """Open + initialise a session, yield it, close on exit.

        Usage:

            async with MCPClient.connect(cfg) as session:
                tools = await session.list_tools()

        When ``cfg.auth_ref`` is set, pass a ``vault_resolver`` and
        the resolved secret is merged into ``env`` (stdio) or
        ``headers`` (http transports) *before* the transport opens —
        cleartext credentials never reach the on-disk config or the
        Project.mcp_servers JSONB blob.

        ``auth`` (ADR 0127) is an optional ``httpx.Auth`` handed to the
        HTTP transports (sse / streamable_http) — in practice the SDK's
        ``OAuthClientProvider`` from :func:`shared_mcp.oauth.build_oauth_provider`,
        which adds the bearer + auto-refreshes it. It is ignored for the
        stdio transport (no HTTP client to attach to). ``auth`` and a
        static ``auth_ref`` header are complementary; using both on the
        same server is a config smell (double credential).

        Errors are normalised:
            * Auth resolution → :class:`MCPAuthError`.
            * Anything in the connect path → :class:`MCPTransportError`
              (or :class:`MCPAuthError` if the SDK reports 401/403).
            * Anything during use propagates per `MCPSession` rules.
        """
        config = apply_vault_auth(config, vault_resolver)
        # Outer try/except wraps the ENTIRE async-with-AsyncExitStack
        # so cleanup errors (when streamablehttp_client's anyio
        # TaskGroup unwinds AFTER yield) also get normalised.
        # Without this outer net, the inner try/except blocks only
        # cover the body — but the SDK frequently surfaces connection
        # failures from cleanup (the TaskGroup's __aexit__ raises a
        # BaseExceptionGroup), which propagates past the AsyncExitStack
        # unchanged and reaches the caller as a raw ConnectError-group.
        try:
            async with AsyncExitStack() as stack:
                try:
                    read_stream, write_stream = await _open_streams(stack, config, auth=auth)
                except MCPError:
                    raise
                except BaseExceptionGroup as eg:
                    inner = _first_inner(eg) or eg
                    raise MCPTransportError(
                        f"failed to open {config.transport!r} transport "
                        f"for server {config.name!r}: {inner}"
                    ) from eg
                except Exception as exc:
                    raise MCPTransportError(
                        f"failed to open {config.transport!r} transport "
                        f"for server {config.name!r}: {exc}"
                    ) from exc

                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=config.timeout_s),
                    )
                )
                try:
                    init_result = await session.initialize()
                except MCPError:
                    raise
                except BaseExceptionGroup as eg:
                    inner = _first_inner(eg) or eg
                    raise MCPTransportError(
                        f"initialize() against {config.name!r} failed: {inner}"
                    ) from eg
                except Exception as exc:
                    msg = str(exc).lower()
                    if "401" in msg or "403" in msg or "unauthor" in msg or "forbidden" in msg:
                        raise MCPAuthError(
                            f"server {config.name!r} rejected our credentials: {exc}"
                        ) from exc
                    raise MCPTransportError(
                        f"initialize() against {config.name!r} failed: {exc}"
                    ) from exc
                yield MCPSession(config=config, raw=session, init_result=init_result)
        except MCPError:
            raise
        except BaseExceptionGroup as eg:
            # Cleanup-phase failure. The inner blocks may have already
            # raised an MCPError (which would land in eg.exceptions);
            # prefer that to keep the original context. Otherwise wrap.
            inner = _first_inner(eg) or eg
            if isinstance(inner, MCPError):
                raise inner from eg
            raise MCPTransportError(f"MCP session against {config.name!r} failed: {inner}") from eg


async def _open_streams(
    stack: AsyncExitStack,
    config: MCPServerConfig,
    *,
    auth: httpx.Auth | None = None,
) -> tuple[Any, Any]:
    """Open the right transport for `config.transport` and return its
    `(read_stream, write_stream)` pair. Registers cleanup on `stack`.

    `streamable_http` actually yields three values (the third is a
    `get_session_id` callback); we drop it because we don't expose
    HTTP-session-id resumption yet.

    ``auth`` (ADR 0127) is forwarded to the HTTP transports only; stdio
    has no HTTP client to carry an ``httpx.Auth``.
    """
    transport: Transport = config.transport

    if transport == "stdio":
        params = StdioServerParameters(
            command=config.command or "",
            args=list(config.args),
            env=dict(config.env) or None,
        )
        read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
        return read_stream, write_stream

    if transport == "sse":
        read_stream, write_stream = await stack.enter_async_context(
            sse_client(
                url=config.url or "",
                headers=dict(config.headers) or None,
                sse_read_timeout=config.timeout_s * 10,  # longer than per-call
                auth=auth,
            )
        )
        return read_stream, write_stream

    if transport == "streamable_http":
        read_stream, write_stream, _get_session_id = await stack.enter_async_context(
            streamablehttp_client(
                url=config.url or "",
                headers=dict(config.headers) or None,
                timeout=config.timeout_s,
                sse_read_timeout=config.timeout_s * 10,
                auth=auth,
            )
        )
        return read_stream, write_stream

    raise MCPTransportError(f"unknown transport: {transport!r}")


def _first_inner(eg: BaseExceptionGroup[BaseException]) -> BaseException | None:
    """Return the most informative non-group exception inside an
    ExceptionGroup, recursing into nested groups.

    Prefers an :class:`MCPError` if present anywhere in the tree —
    that's the wrapped error our inner try/except blocks already
    produced, and we want to surface it rather than the raw SDK
    cause that may also be in the group (e.g. when an
    AsyncExitStack cleanup combines our MCPTransportError with the
    SDK's BaseExceptionGroup[ConnectError]).
    """
    # First pass: look for an MCPError anywhere in the tree.
    for exc in _walk(eg):
        if isinstance(exc, MCPError):
            return exc
    # Second pass: return the first non-group leaf.
    for exc in _walk(eg):
        return exc
    return None


def _walk(eg: BaseExceptionGroup[BaseException]) -> Iterator[BaseException]:
    """Yield every non-group leaf exception in `eg`, depth-first."""
    for exc in eg.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            yield from _walk(exc)
        else:
            yield exc


__all__ = ["MCPClient", "MCPSession"]
