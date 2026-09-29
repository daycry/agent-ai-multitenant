"""Transporte Responses de OpenAI (ADR 0169, `task_cm_06`).

Codex y varios GPT-5.x sólo se sirven por ``/responses`` en Copilot y en Azure. Este
módulo traduce el contrato de la capa —`Message`, tools en formato chat,
`CompletionResponse`— a y desde esa API, para que el proveedor cambie de transporte
sin que el resto de la capa se entere.

Dos decisiones que conviene tener escritas:

* **Sin estado** (``store: false``). El runtime ya manda el historial completo en
  cada turno, como texto; no se usa ``previous_response_id``. Así una conversación no
  depende de que el servidor recuerde nada, y reintentar es seguro.
* **Lista blanca de kwargs.** Los kwargs que la capa pasa pensando en
  ``/chat/completions`` (``stream_options``, por ejemplo) no existen aquí y harían un
  400. Sólo se traducen los que tienen equivalente.

Módulo puro, sin red.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from shared_llm.exceptions import ProviderError
from shared_llm.types import CompletionResponse, Message, StreamChunk, ToolCall, Usage

#: Kwargs de la capa que pasan tal cual a la Responses API.
_PASSTHROUGH = ("parallel_tool_calls", "top_p", "user", "metadata")


def is_responses_payload(data: Any) -> bool:
    """¿Es ``data`` una respuesta de la Responses API y no de chat/completions?"""
    return isinstance(data, dict) and (
        data.get("object") == "response" or ("output" in data and "choices" not in data)
    )


def _flat_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Una tool del formato chat (``{"type", "function": {...}}``) en formato plano."""
    fn = tool.get("function")
    if not isinstance(fn, dict):
        return tool
    flat: dict[str, Any] = {"type": "function", "name": fn.get("name")}
    if "description" in fn:
        flat["description"] = fn["description"]
    if "parameters" in fn:
        flat["parameters"] = fn["parameters"]
    return flat


def _flat_tool_choice(choice: Any) -> Any:
    if isinstance(choice, dict) and isinstance(choice.get("function"), dict):
        return {"type": "function", "name": choice["function"].get("name")}
    return choice


def to_responses_body(
    messages: Sequence[Message],
    *,
    model: str,
    max_tokens: int | None,
    temperature: float | None,
    tools: list[dict[str, Any]] | None,
    **kwargs: Any,
) -> dict[str, Any]:
    """El cuerpo de ``POST /responses`` equivalente a una llamada de chat."""
    instructions = "\n\n".join(m.content for m in messages if m.role == "system" and m.content)
    items: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            continue
        # El runtime no manda tool calls del asistente en el historial, así que un
        # mensaje `tool` no tendría `function_call` al que responder: se entrega como
        # texto del usuario en vez de romper la petición.
        role = m.role if m.role in ("user", "assistant") else "user"
        content = m.content if m.role != "tool" else f"Tool result:\n{m.content}"
        items.append({"role": role, "content": content})
    body: dict[str, Any] = {"model": model, "input": items, "store": False}
    if instructions:
        body["instructions"] = instructions
    if max_tokens:
        body["max_output_tokens"] = max_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = [_flat_tool(t) for t in tools]
    if "tool_choice" in kwargs:
        body["tool_choice"] = _flat_tool_choice(kwargs["tool_choice"])
    effort = kwargs.get("reasoning_effort")
    if effort:
        body["reasoning"] = {"effort": effort}
    for key in _PASSTHROUGH:
        if key in kwargs:
            body[key] = kwargs[key]
    return body


def _function_calls(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in data.get("output") or []
        if isinstance(item, dict) and item.get("type") == "function_call"
    ]


def responses_args_checked(raw: Any) -> tuple[dict[str, Any], bool]:
    """``(args, malformed)`` de los ``arguments`` de un ``function_call``."""
    if isinstance(raw, dict):
        return raw, False
    if not raw:
        return {}, False
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, True
    return (parsed, False) if isinstance(parsed, dict) else ({}, True)


def responses_truncated(data: dict[str, Any]) -> bool:
    """¿Se cortó la respuesta en el tope de salida?"""
    details = data.get("incomplete_details")
    reason = details.get("reason") if isinstance(details, dict) else None
    return data.get("status") == "incomplete" and reason == "max_output_tokens"


def responses_corrupt_indexes(data: dict[str, Any]) -> set[int]:
    """Índices, entre las tool calls, de las que traen argumentos corruptos."""
    return {
        idx
        for idx, call in enumerate(_function_calls(data))
        if responses_args_checked(call.get("arguments"))[1]
    }


def parse_responses(
    data: dict[str, Any], *, provider: str, fallback_model: str
) -> CompletionResponse:
    """Una respuesta de la Responses API como `CompletionResponse`."""
    if not is_responses_payload(data):
        raise ProviderError(f"{provider}: respuesta de /responses sin 'output'", raw=data)
    texts: list[str] = []
    for item in data.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    texts.append(str(part.get("text") or ""))
    calls = [
        ToolCall(
            id=str(call.get("call_id") or call.get("id") or ""),
            name=str(call.get("name") or ""),
            arguments=responses_args_checked(call.get("arguments"))[0],
        )
        for call in _function_calls(data)
    ]
    raw_usage = data.get("usage")
    usage_d: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    details = usage_d.get("input_tokens_details")
    usage = Usage(
        input_tokens=int(usage_d.get("input_tokens", 0) or 0),
        output_tokens=int(usage_d.get("output_tokens", 0) or 0),
        cache_read_tokens=int(details.get("cached_tokens", 0) or 0)
        if isinstance(details, dict)
        else 0,
    )
    status = data.get("status")
    return CompletionResponse(
        content="".join(texts),
        model=str(data.get("model") or fallback_model),
        provider=provider,
        usage=usage,
        tool_calls=calls or None,
        raw=data,
        # `length` es lo que F32 y la guarda de acción leen como truncado.
        stop_reason="length" if responses_truncated(data) else (str(status) if status else None),
    )


def whole_as_chunks(resp: CompletionResponse) -> list[StreamChunk]:
    """Una respuesta entera como los trozos de un stream: el texto y el cierre.

    Es lo que hace ``stream()`` con un modelo que sólo sirve la Responses API: se
    pierde la entrega incremental, no el contenido, el uso ni las tool calls.
    """
    chunks = [StreamChunk(delta=resp.content)] if resp.content else []
    chunks.append(StreamChunk(delta="", done=True, usage=resp.usage, tool_calls=resp.tool_calls))
    return chunks


__all__ = [
    "is_responses_payload",
    "parse_responses",
    "responses_args_checked",
    "responses_corrupt_indexes",
    "responses_truncated",
    "to_responses_body",
    "whole_as_chunks",
]
