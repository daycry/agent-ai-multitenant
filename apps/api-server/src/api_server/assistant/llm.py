"""LLM-backed ``AssistantModelClient`` adapter (Plan 10 task_10_14).

Bridges the assistant sub-graph's synchronous ``decide`` seam onto an
async ``shared_llm.LLMProvider`` (ADR 0021). The graph stays
provider-agnostic; this is the single place that knows about
``LLMProvider`` and how to translate its ``CompletionResponse`` /
``ToolCall`` shapes into the graph's ``ModelTurn`` / ``ToolInvocation``.

Tests do NOT use this adapter — they inject a ``ScriptedAssistantModel``
through the router's ``get_assistant_model`` dependency override, so no
real provider is ever contacted (the established chat-test pattern).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, cast

from shared_llm.base import LLMProvider
from shared_llm.retry import DEFAULT_ATTEMPTS, RetryEvent, with_retries
from shared_llm.types import CompletionResponse, Role
from shared_llm.types import Message as LLMMessage

from api_server.assistant.graph import AssistantState, ModelTurn, ToolInvocation
from api_server.assistant.tools import tool_schemas

_log = logging.getLogger(__name__)

# Función que traduce las tools habilitadas a sus JSON schemas. El default es el
# catálogo del ASISTENTE; el córtex inyecta el suyo (``cortex_tool_schemas``) —
# antes usaba SIEMPRE el del asistente, que no conoce las tools del córtex, así
# que toda ``complete()`` del córtex iba con ``tools=None`` (hallazgo #10e).
SchemaFn = Callable[[tuple[str, ...]], list[dict[str, Any]]]


@dataclass
class LLMAssistantModel:
    """Adapt an ``LLMProvider`` to the assistant graph's ``decide`` seam.

    ``decide`` is async: the graph node awaits it on the request's event
    loop, so the async ``provider.complete()`` runs on that same loop. (It
    used to be sync and bridge to async via a worker-thread ``asyncio.run``
    per call — but that closed a fresh loop each round, and a provider's
    pooled httpx connection from round 1 then blew up on round 2 with
    "Event loop is closed" on Windows. Awaiting directly avoids the whole
    cross-loop problem.) Each call sends the system prompt + chat history +
    accumulated tool results and lets the model either call more tools or
    answer.
    """

    provider: LLMProvider
    model: str | None = None
    max_tokens: int = 1024
    temperature: float = 0.3
    # ADR 0070: kwargs de razonamiento ya traducidos al proveedor (effort /
    # reasoning_effort); se vuelcan al `complete()` del provider.
    extra_call_kwargs: dict[str, Any] = field(default_factory=dict)
    # Metadatos de la resolución (ADR 0070/0074), opcionales: el asistente no los
    # estampa (None); el córtex sí — permiten auditar el effort del turno (antes
    # se persistía NULL) y que la política afectiva lo module por-request.
    reasoning_effort: str | None = None
    provider_kind: str | None = None
    # Catálogo de schemas a enviar (hallazgo #10e). Default = asistente; el córtex
    # pasa ``cortex_tool_schemas``. Es un field de INSTANCIA (no method), así que
    # ``dataclasses.replace`` en ``apply_effort_decision`` lo preserva sin binding.
    schema_fn: SchemaFn = field(default=tool_schemas)
    # ADR 0116: acumuladores de usage del REQUEST (una instancia por request via
    # get_assistant_model / build_cortex_model). El router los persiste
    # best-effort en llm_usage_events al cerrar el turno.
    usage_input_tokens: int = 0
    usage_output_tokens: int = 0
    usage_cost_usd: float = 0.0
    usage_calls: int = 0
    # prod-07 task_prod07_01 (llm-2): presupuesto de reintentos de `decide()`.
    # 1 = sin reintentos (comportamiento previo, escotilla de escape por si un
    # despliegue necesita volver atrás sin tocar código).
    retry_attempts: int = DEFAULT_ATTEMPTS
    # Inyectable SOLO para los tests: evita esperar el backoff de verdad.
    retry_sleep: Callable[[float], Awaitable[None]] | None = None

    async def decide(self, state: AssistantState) -> ModelTurn:
        messages = self._build_messages(state)
        schemas = self.schema_fn(state.enabled_tools)
        # Wrap each schema in the OpenAI-style {type:function,function:{...}}
        # envelope most providers expect; harmless for those that ignore it.
        tools = [{"type": "function", "function": s} for s in schemas] if schemas else None

        # prod-07 task_prod07_01 (llm-2): un 429 puntual o un socket cortado ya no
        # matan el turno. La política (qué se reintenta y cuánto se espera) vive en
        # `shared_llm.retry`, ÚNICA para todos los consumidores; aquí solo se aplica.
        # `complete()` es idempotente desde nuestro lado, así que repetirla es
        # seguro — el coste de los tokens duplicados queda VISIBLE en el log.
        async def _call() -> CompletionResponse:
            return await self.provider.complete(
                messages,
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                tools=tools,
                **self.extra_call_kwargs,
            )

        response = await with_retries(
            _call,
            attempts=self.retry_attempts,
            provider=getattr(self.provider, "name", "") or "",
            sleep=self.retry_sleep,
            on_retry=_log_retry,
        )
        usage = getattr(response, "usage", None)
        if usage is not None:
            self.usage_input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
            self.usage_output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
            self.usage_cost_usd += float(getattr(usage, "cost_usd", 0.0) or 0.0)
        self.usage_calls += 1
        if response.tool_calls:
            calls = tuple(
                ToolInvocation(name=tc.name, arguments=dict(tc.arguments))
                for tc in response.tool_calls
            )
            return ModelTurn(content=response.content or None, tool_calls=calls)
        return ModelTurn(content=response.content or "", tool_calls=())

    async def decide_stream(self, state: AssistantState) -> Any:
        """Sintesis final SIN tools en streaming (A2 fase 2 / ADR 0073 F2).

        Async generator de deltas de texto via ``provider.stream()`` (los 4
        kinds lo implementan; sin tools no aplica el caveat de claude_sdk).
        Acumula usage del chunk final. Solo lo invoca el nodo ``finish`` en el
        camino FINISH_NUDGE cuando el caller pidio deltas (``on_delta``).

        SIN reintentos, a diferencia de ``decide()`` (prod-07 task_prod07_01):
        reintentar un stream reemitiría los deltas que el usuario YA vio en
        pantalla. El fallo sube tipado y el router decide cómo mostrarlo."""
        messages = self._build_messages(state)
        async for chunk in self.provider.stream(
            messages,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            **self.extra_call_kwargs,
        ):
            if chunk.delta:
                yield chunk.delta
            if chunk.done and chunk.usage is not None:
                usage = chunk.usage
                self.usage_input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
                self.usage_output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
                self.usage_cost_usd += float(getattr(usage, "cost_usd", 0.0) or 0.0)
        self.usage_calls += 1

    def _build_messages(self, state: AssistantState) -> list[LLMMessage]:
        messages: list[LLMMessage] = [LLMMessage(role="system", content=state.system_prompt)]
        for entry in state.chat_history:
            raw_role = str(entry.get("role", "user"))
            role: Role = (
                cast(Role, raw_role)
                if raw_role in ("user", "assistant", "system", "tool")
                else "user"
            )
            messages.append(LLMMessage(role=role, content=str(entry.get("content", ""))))
        # Feed accumulated tool results back as a system note so the model
        # can ground its answer on real data.
        if state.tool_results:
            summary = "\n".join(f"[{r['tool']}] {r['result']}" for r in state.tool_results)
            messages.append(
                LLMMessage(
                    role="system",
                    content=f"Resultados de herramientas:\n{summary}",
                )
            )
        # Orden imperativa del turno de cierre (FINISH_NUDGE) — va como ÚLTIMO
        # mensaje (recencia) para forzar la redacción final cuando el modelo no ha
        # comprometido respuesta. Solo la fija el nodo `finish`; None en el resto.
        if state.final_instruction:
            messages.append(LLMMessage(role="system", content=state.final_instruction))
        return messages


def _log_retry(event: RetryEvent) -> None:
    """Deja rastro de CADA reintento (prod-07 task_prod07_01).

    Un reintento silencioso esconde dos cosas a la vez: que el proveedor está
    inestable y que el turno pagó los tokens del prompt más de una vez. Los
    campos van en `extra` para que el log estructurado los indexe."""
    _log.warning(
        "LLM retry %s/%s tras %s (espera %.2fs)",
        event.attempt,
        event.attempts,
        type(event.error).__name__,
        event.delay,
        extra=event.as_log_extra(),
    )


__all__ = ["LLMAssistantModel"]
