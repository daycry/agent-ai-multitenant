"""Sesión persistente del Claude Agent SDK por run (ADR 0097).

El **hilo conversacional por run** es UNA capacidad del contrato, con dos
transportes según el proveedor (restricción del operador: nada exclusivo del
SDK):

* **HTTP** (azure_foundry / copilot / ollama) — el cliente re-envía el hilo de
  mensajes en cada turno y el proveedor reusa su KV-cache (ADR 0110).
* **claude_sdk** (aquí) — el transporte es un CLI con estado propio: re-enviar
  el hilo no reusaría nada, así que se mantiene **una sesión SDK viva** durante
  el run y cada turno es un mensaje más de la MISMA conversación.

Ambos caminos exponen el MISMO `LLMProvider.complete()` y el MISMO contrato del
grafo (un ACT por turno, la observación del host en el turno siguiente), así que
el resto del sistema no distingue el transporte.

Mediación del host — sin cambios (principios 2 y 10): las tools se anuncian como
un MCP server in-process y `can_use_tool` DENIEGA su ejecución, pero **sin
interrupt** (spike 2026-07-13 con credencial viva): el turno cierra limpio, con
`ResultMessage` y usage íntegros, el host ejecuta la tool con su
ToolRegistry/approvals/loop-detection, y su observación llega en el siguiente
mensaje del hilo. El `interrupt=True` del camino one-shot es justamente lo que
cortaba el `ResultMessage` y dejaba runs con `total_tokens=0` y `cost>0`.

El `complete()` sin `conversation_session=True` (p.ej. el review, o el flag
global OFF) delega en el camino one-shot heredado: byte a byte el comportamiento
histórico.
"""

from __future__ import annotations

import asyncio
import contextlib
import queue
import threading
from collections.abc import Sequence
from typing import Any

from shared_llm.providers.claude_agent import (
    _HOST_TOOLS_SERVER,
    _SDK_NATIVE_TOOLS,
    ClaudeAgentProvider,
    _harvest_stop_reason,
    _harvest_tool_calls,
    _json_schema_to_tool_schema,
    _run_error,
    _unwrap_tool_schemas,
)
from shared_llm.types import CompletionResponse, Message

# Lo que el modelo lee cuando pide una tool: el host la ejecuta FUERA de la
# sesión, así que no debe reintentarla — el resultado llega en el siguiente
# mensaje del hilo (es la observación que el grafo ya envía hoy).
HOST_EXECUTED_DENY = (
    "Esta tool la ejecuta el HOST fuera de esta sesión (host-executed "
    "tool-calling). NO la reintentes y NO llames a otra tool: termina tu turno "
    "ahora. El resultado llegará como observación en el siguiente mensaje."
)


def deny_payload() -> dict[str, Any]:
    """Los campos del ``PermissionResultDeny`` de la mediación host.

    Función aparte (y no el objeto del SDK) para poder fijar en un test —sin el
    SDK instalado— lo único que importa del contrato: **jamás `interrupt`**."""
    return {"message": HOST_EXECUTED_DENY, "interrupt": False}


class ClaudeAgentSessionProvider(ClaudeAgentProvider):
    """`ClaudeAgentProvider` con una sesión SDK viva reutilizada entre turnos.

    La sesión vive en un **event loop de fondo propio**: el runtime puentea cada
    llamada con `asyncio.run()` (un loop NUEVO por turno) y el transporte del
    SDK —subprocess + streams anyio— queda ligado al loop que lo creó, así que
    una sesión creada en el loop del turno N estaría muerta en el N+1. El loop
    de fondo (mismo patrón que el runner MCP del runtime) la mantiene viva; cada
    turno le envía trabajo y espera el resultado.

    Un fallo de turno DESTRUYE la sesión: el siguiente turno abre otra con el
    historial completo que el cliente sigue trayendo en `messages` — auto-sanado
    sin pérdida de contexto.
    """

    def __init__(
        self,
        *,
        client_factory: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # Seam de test: `(options) -> ClaudeSDKClient`. En producción es el SDK.
        self._client_factory = client_factory
        self._session: Any | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- event loop de fondo (la sesión debe sobrevivir al asyncio.run del turno)
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            loop = self._loop
            if loop is None:
                loop = self._start_loop()
                self._loop = loop
            return loop

    def _start_loop(self) -> asyncio.AbstractEventLoop:
        ready: queue.Queue[asyncio.AbstractEventLoop] = queue.Queue(maxsize=1)

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            ready.put(loop)
            loop.run_forever()
            loop.close()

        self._loop_thread = threading.Thread(
            target=_run, name="claude-sdk-session-loop", daemon=True
        )
        self._loop_thread.start()
        try:
            return ready.get(timeout=5.0)
        except queue.Empty as exc:  # pragma: no cover — defensivo
            raise RuntimeError("no se pudo arrancar el loop de la sesión SDK") from exc

    async def _in_session_loop(self, coro: Any) -> Any:
        """Ejecuta `coro` en el loop de la sesión desde el loop del turno."""
        loop = self._ensure_loop()
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))

    # ---- sesión ----------------------------------------------------------
    def _build_session_options(
        self,
        *,
        system: str | None,
        model: str | None,
        specs: list[dict[str, Any]],
        effort: str | None,
        allowed_tools: list[str] | None,
        max_turns: int,
    ) -> Any:
        from claude_agent_sdk import (  # lazy — extra opcional `claude`
            ClaudeAgentOptions,
            PermissionResultDeny,
            create_sdk_mcp_server,
            tool,
        )

        async def _stub(args: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001 — firma del SDK
            # Nunca se ejecuta: `can_use_tool` deniega antes (la corre el host).
            return {"content": [{"type": "text", "text": ""}]}

        sdk_tools = [
            tool(
                spec["name"],
                spec.get("description") or spec["name"],
                _json_schema_to_tool_schema(spec.get("parameters")),
            )(_stub)
            for spec in specs
        ]
        server = create_sdk_mcp_server(name=_HOST_TOOLS_SERVER, version="1.0.0", tools=sdk_tools)

        async def _deny_host_executed(
            tool_name: str,  # noqa: ARG001 — firma de can_use_tool
            tool_input: dict[str, Any],  # noqa: ARG001
            context: Any,  # noqa: ARG001
        ) -> Any:
            return PermissionResultDeny(**deny_payload())

        extra: dict[str, Any] = {}
        if effort:
            extra["effort"] = effort
        if allowed_tools:
            extra["allowed_tools"] = list(allowed_tools)
        disabled = [n for n in _SDK_NATIVE_TOOLS if n not in set(allowed_tools or ())]
        if disabled:
            extra["disallowed_tools"] = disabled
        return ClaudeAgentOptions(
            model=model or self._default_model,
            system_prompt=system if system is not None else self._default_system_prompt,
            mcp_servers={_HOST_TOOLS_SERVER: server},
            can_use_tool=_deny_host_executed,
            max_turns=max_turns,
            **extra,
        )

    def _new_session(self, options: Any) -> Any:
        if self._client_factory is not None:
            return self._client_factory(options)
        from claude_agent_sdk import ClaudeSDKClient  # lazy

        return ClaudeSDKClient(options=options)

    async def _turn(self, session: Any, prompt: str) -> list[Any]:
        await session.query(prompt)
        collected: list[Any] = []
        async for msg in session.receive_response():
            collected.append(msg)
        return collected

    async def _drop_session(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        with contextlib.suppress(Exception):  # el cierre jamás rompe un run
            await self._in_session_loop(session.disconnect())

    # ---- LLMProvider -----------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        session_turn = bool(kwargs.pop("conversation_session", False))
        if not session_turn or not tools or self._query_fn is not None:
            # Camino histórico one-shot (review, flag OFF, o tests con query_fn).
            return await super().complete(
                messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                **kwargs,
            )

        system, transcript = self._flatten(messages)
        fresh = self._session is None
        if fresh:
            options = (
                None  # modo test: la sesión inyectada acepta lo que le demos (sin SDK)
                if self._client_factory is not None
                else self._build_session_options(
                    system=system,
                    model=model,
                    specs=_unwrap_tool_schemas(tools),
                    effort=kwargs.pop("effort", None),
                    allowed_tools=kwargs.pop("allowed_tools", None),
                    max_turns=int(kwargs.pop("max_turns", 8)),
                )
            )
            session = self._new_session(options)
            await self._in_session_loop(session.connect())
            self._session = session
        # Sesión NUEVA → todo el contexto; sesión viva → solo el mensaje nuevo
        # (el historial ya está dentro de la sesión: eso es lo que se reusa).
        prompt = transcript if fresh else self._last_user_content(messages)

        collected: list[Any] = []
        try:
            collected = await self._in_session_loop(self._turn(self._session, prompt))
        except BaseException as exc:
            # CUALQUIER interrupción (incl. CancelledError de un timeout del turno,
            # que hereda de BaseException, NO de Exception) deja la sesión a
            # medias: hay que DESCARTARLA o el turno siguiente la reusaría
            # envenenada. Solo los Exception se tipan a LLMError; una cancelación
            # / KeyboardInterrupt / SystemExit se re-propaga tal cual (envolverla
            # rompería la semántica de asyncio y del apagado).
            await self._drop_session()  # sesión envenenada → se reabre en el turno siguiente
            if isinstance(exc, Exception):
                raise _run_error(exc, collected) from exc
            raise

        text_parts, usage = self._harvest(collected)
        tool_calls = _harvest_tool_calls(collected)
        # Un turno de tool call no aporta respuesta: el texto ("voy a leer…",
        # el aviso del deny) NO es la respuesta al usuario — misma regla que el
        # camino one-shot.
        content = "" if tool_calls else "".join(text_parts)
        return CompletionResponse(
            content=content,
            model=model or self._default_model,
            provider=self.name,
            usage=usage,
            tool_calls=tool_calls or None,
            raw=collected,
            stop_reason=_harvest_stop_reason(collected),
        )

    @staticmethod
    def _last_user_content(messages: Sequence[Message]) -> str:
        for m in reversed(messages):
            if m.role == "user":
                return str(m.content)
        return ""

    async def aclose(self) -> None:
        await self._drop_session()
        loop, self._loop = self._loop, None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)


__all__ = ["HOST_EXECUTED_DENY", "ClaudeAgentSessionProvider", "deny_payload"]
