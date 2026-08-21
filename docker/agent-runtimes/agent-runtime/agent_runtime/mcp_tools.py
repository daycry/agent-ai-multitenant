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
  - :meth:`connect` opens each MCP session inside that loop in a
    DEDICATED owner task (`_ServerHandle`) that holds the transport's
    ``async with`` from enter to exit — anyio cancel scopes must exit in
    the task that entered them, so no other shape survives an HTTP
    transport's teardown.
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
from collections.abc import Callable, Generator
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

import httpx
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


class MediatedBearerAuth(httpx.Auth):
    """``httpx.Auth`` que pide el access token a la plataforma, no a Vault.

    La mitad de sandbox de la opción C del ADR 0131. El contenedor no confiable
    no guarda credenciales: por cada sesión MCP pide a ``/internal/agent/
    mcp-oauth-token`` un access token acotado a un servidor, y ahí se acaba lo
    que tiene. Ni token de Vault (la llave del almacén) ni refresh token (la
    credencial de larga duración) bajan hasta aquí.

    El refresco lo dispara un **401 real** del servidor remoto, no una cuenta
    atrás: la plataforma canjea el refresh token, lo persiste y devuelve uno
    nuevo, y la petición se reintenta UNA vez. Un segundo 401 se propaga — si el
    token recién emitido tampoco vale, reintentar es girar en vacío.
    """

    # httpx tiene que leer el cuerpo de la respuesta antes de devolvernos el
    # control, o el `yield` de reintento se quedaría con un stream a medias.
    requires_response_body = True

    def __init__(self, fetch: Callable[[bool], tuple[str, str]], server_name: str) -> None:
        self._fetch = fetch
        self._server_name = server_name
        self._cached: tuple[str, str] | None = None

    def _token(self, *, refresh: bool) -> tuple[str, str]:
        if refresh or self._cached is None:
            self._cached = self._fetch(refresh)
        return self._cached

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        token_type, token = self._token(refresh=False)
        request.headers["Authorization"] = f"{token_type} {token}"
        response = yield request
        if response.status_code != 401:
            return
        token_type, token = self._token(refresh=True)
        request.headers["Authorization"] = f"{token_type} {token}"
        yield request


def build_oauth_auth(config: MCPServerConfig, *, api: Any | None) -> Any:
    """The ``httpx.Auth`` for a server connected via OAuth 2.1, or ``None``.

    The last hop of ADR 0127 (task_wf_12, B-03). The interactive flow was
    complete — discovery, DCR, PKCE, token + registration in Vault — but the run
    opened the session WITHOUT ``auth=``, so the remote server answered 401 and
    the whole feature delivered nothing to autonomous execution, the one case it
    was designed for.

    ADR 0131 decidió POR DÓNDE llega esa credencial. No por Vault: el diseño
    anterior pedía un ``AGENT_VAULT_TOKEN`` dentro del sandbox —una llave del
    almacén de secretos en el contenedor que ejecuta código no controlado, justo
    lo que el principio 2 de CLAUDE.md prohíbe— y por eso esa variable no se fijó
    nunca en ningún sitio y la función no llegó a funcionar. Ahora se pide a la
    plataforma por el API interno que el runtime ya tiene cableado, igual que
    ``stack_exec`` pide al worker lo que el sandbox no puede hacer.

    ``None`` cuando el servidor no usa OAuth (sin ``oauth_ref``), así que el
    resto conecta exactamente igual que antes. Lanza :class:`MCPAuthError` cuando
    el servidor SÍ declara OAuth y no hay API interno: sin él no hay token, y
    conectar igualmente saldría como un 401 opaco del remoto en vez de decir qué
    falta.

    Se manda el NOMBRE del servidor, no el ``oauth_ref``: la ruta de Vault la
    construye el servidor con el tenant del token y el proyecto del run. Mandar
    la ruta sería entregarle al sandbox la capacidad de pedir la credencial de
    otro proyecto cambiando una cadena.
    """
    if not config.oauth_ref:
        return None
    if api is None:
        raise MCPAuthError(
            f"server {config.name!r} uses OAuth but this run has no internal API "
            "(no AGENTIC_INTERNAL_TOKEN) — its access token cannot be requested"
        )

    def _fetch(refresh: bool) -> tuple[str, str]:
        payload = api.mcp_oauth_token(server=config.name, refresh=refresh)
        return str(payload.get("token_type") or "Bearer"), str(payload["access_token"])

    return MediatedBearerAuth(_fetch, config.name)


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
@dataclass
class _ServerHandle:
    """Una sesión MCP viva y la task del loop de fondo que la POSEE.

    anyio exige que un cancel scope salga en la MISMA task que lo entró; los
    transportes del SDK (`streamablehttp_client`, `sse_client`) crean task
    groups en su ``__aenter__``, así que entrar el context manager en la task
    de ``connect()`` y salirlo en la de ``close()`` (el patrón AsyncExitStack
    anterior) reventaba con "Attempted to exit cancel scope in a different
    task" en cada teardown HTTP. Ahora ``_serve()`` es la única dueña del
    ``async with`` completo: entra, publica (session, tools), espera ``stop``
    y sale — todo en una task. ``close()`` solo señala ``stop`` y espera el
    join.
    """

    join: Future[Any]
    stop: asyncio.Event
    session: MCPSession
    tools: list[MCPTool] = field(default_factory=list)


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

    def __init__(
        self, vault_resolver: VaultResolver | None = None, *, api: Any | None = None
    ) -> None:
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
        # One owning task PER connected server (F23 kept the per-server
        # isolation; the owner-task shape is what fixes the anyio cross-task
        # cancel-scope violation — see _ServerHandle).
        self._handles: dict[str, _ServerHandle] = {}
        self._sessions: dict[str, MCPSession] = {}
        self._tools: dict[str, list[MCPTool]] = {}
        self._started = False
        self._lock = threading.Lock()
        self._vault_resolver = vault_resolver
        # Cliente del API interno para la credencial OAuth (ADR 0131 opción C).
        self._api = api

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
                # Close each server INDEPENDENTLY with its own timeout budget
                # (F23): signal its owner task to unwind (the `async with` exits
                # INSIDE that task — anyio-safe) and join it. One server that
                # hangs on teardown cannot starve the close of the others.
                for name, handle in list(self._handles.items()):
                    try:
                        if self._loop is not None:
                            self._loop.call_soon_threadsafe(handle.stop.set)
                        handle.join.result(timeout=10.0)
                    except Exception:
                        logger.exception("error closing MCP session for %r", name)
            finally:
                self._handles.clear()
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

        # `_serve` OWNS the whole `async with` (enter → publish → wait → exit)
        # so every anyio cancel scope enters and exits in this one task. The
        # thread-safe `started` future hands (session, tools, stop) back to the
        # sync caller; on failure it carries the exception instead.
        started: Future[tuple[MCPSession, list[MCPTool], asyncio.Event]] = Future()

        auth = build_oauth_auth(config, api=self._api)

        async def _serve() -> None:
            try:
                async with MCPClient.connect(
                    config, vault_resolver=self._vault_resolver, auth=auth
                ) as session:
                    tools = await session.list_tools()
                    stop = asyncio.Event()
                    started.set_result((session, tools, stop))
                    await stop.wait()
            except BaseException as exc:  # — must reach the sync caller
                if not started.done():
                    started.set_exception(exc)
                    return
                raise

        join: Future[Any] = self._submit(_serve())
        try:
            session, tools, stop = started.result(timeout=config.timeout_s * 2)
        except TimeoutError as exc:
            # Cancelling `join` cancels the serve task; the `async with` then
            # unwinds INSIDE that task (anyio-safe), closing whatever the
            # partial connect opened. This server never reaches `_handles`, so
            # it cannot stall `close()` (F23).
            join.cancel()
            raise MCPTransportError(
                f"server {config.name!r} connect timed out after {config.timeout_s * 2}s"
            ) from exc
        self._handles[config.name] = _ServerHandle(
            join=join, stop=stop, session=session, tools=tools
        )
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
                f"tool {server_name}.{tool_name} timed out after {session.config.timeout_s}s"
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
