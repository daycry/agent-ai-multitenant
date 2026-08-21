"""`stream()` reporta lo mismo que `complete()` (prod-07 task_prod07_04, llm-6).

El hallazgo
-----------
El contrato de ``StreamChunk`` (``types.py``) promete que el chunk final
(``done=True``) lleva el ``usage`` «si el proveedor lo reporta»… y los tres
providers OpenAI-compat NO lo pedían: sin ``stream_options.include_usage`` en el
body, OpenAI y compatibles NO emiten el chunk de usage, así que todo turno
servido por streaming contabilizaba **0 tokens y 0 coste**. El acumulador del
asistente (`decide_stream`) sumaba ceros y los budgets del proyecto no veían el
gasto de ningún turno en streaming.

Los ``tool_calls`` sí se acumulaban ya (AUD16-06); lo que faltaba era el usage y
—descubierto al escribir estos tests— el chunk final cuando el servidor cierra
el stream SIN mandar ``[DONE]``: entonces no se emitía ningún ``done=True`` y se
perdían usage Y tool_calls a la vez.

La forma del test es PARIDAD: el mismo turno servido por los dos caminos tiene
que contabilizar igual. Un test que solo mirara el streaming podría bendecir
cualquier número.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from shared_llm.base import LLMProvider
from shared_llm.providers import AzureFoundryAPIMProvider, CopilotProvider, OllamaProvider
from shared_llm.types import Message, StreamChunk

# El MISMO turno, en los dos formatos que hablan los proveedores.
_USAGE = {
    "prompt_tokens": 11,
    "completion_tokens": 7,
    "prompt_tokens_details": {"cached_tokens": 4},
}

_COMPLETE_BODY: dict[str, Any] = {
    "model": "m",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "Hola",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "echo", "arguments": '{"a": 1}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": _USAGE,
}

_SSE_LINES = [
    b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""}}]}\n',
    b'data: {"choices":[{"index":0,"delta":{"content":"Hola"}}]}\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1",'
    b'"function":{"name":"echo","arguments":"{\\"a\\": "}}]}}]}\n',
    b'data: {"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,'
    b'"function":{"arguments":"1}"}}]}}]}\n',
    # El chunk de usage que solo llega con stream_options.include_usage: viene
    # con `choices: []`, después del contenido y antes del terminador.
    b'data: {"choices":[],"usage":' + json.dumps(_USAGE).encode() + b"}\n",
    b"data: [DONE]\n",
]


class _Body(httpx.AsyncByteStream):
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for line in self._lines:
            yield line

    async def aclose(self) -> None:
        return None


def _dual_client(
    sse_lines: list[bytes] | None = None,
) -> tuple[httpx.AsyncClient, list[dict[str, Any]]]:
    """Cliente que sirve `complete()` y `stream()`, y registra los bodies."""
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "copilot_internal" in str(req.url):
            return httpx.Response(200, json={"token": "jwt-x", "expires_at": 9e9})
        body = json.loads(req.content)
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(200, stream=_Body(sse_lines or list(_SSE_LINES)))
        return httpx.Response(200, json=_COMPLETE_BODY)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


def _ollama(client: httpx.AsyncClient) -> OllamaProvider:
    return OllamaProvider.cloud(api_key="sk-x", http_client=client)


def _azure(client: httpx.AsyncClient) -> AzureFoundryAPIMProvider:
    return AzureFoundryAPIMProvider(
        apim_base_url="https://apim.example",
        deployment="gpt-4o",
        subscription_key="k",
        http_client=client,
    )


def _copilot(client: httpx.AsyncClient) -> CopilotProvider:
    return CopilotProvider(github_token="gho_x", http_client=client)


# Los tres providers OpenAI-compat, tipados como el Protocol común: verificar uno
# y extrapolar es justo lo que este plan prohíbe.
_BUILDERS: dict[str, Callable[[httpx.AsyncClient], LLMProvider]] = {
    "ollama": _ollama,
    "azure_foundry_apim": _azure,
    "github_copilot": _copilot,
}


async def _drain(provider: LLMProvider) -> tuple[str, StreamChunk]:
    text = ""
    final: StreamChunk | None = None
    async for chunk in provider.stream([Message(role="user", content="hi")]):
        text += chunk.delta
        if chunk.done:
            final = chunk
    assert final is not None, "el stream no emitió chunk final done=True"
    return text, final


# ---------------------------------------------------------------------------
# 1. Se PIDE el usage — sin esto el proveedor no lo manda
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_BUILDERS))
async def test_stream_body_asks_for_usage(kind: str) -> None:
    client, seen = _dual_client()
    await _drain(_BUILDERS[kind](client))
    assert seen, "no se capturó ningún body"
    assert seen[-1]["stream_options"] == {"include_usage": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_BUILDERS))
async def test_caller_can_override_stream_options(kind: str) -> None:
    """Escotilla: un endpoint que no soporte el campo puede desactivarlo sin
    tocar el provider (Ollama antiguo, por ejemplo)."""
    client, seen = _dual_client()
    provider = _BUILDERS[kind](client)
    async for _chunk in provider.stream([Message(role="user", content="hi")], stream_options=None):
        pass
    assert seen[-1]["stream_options"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_BUILDERS))
async def test_complete_does_not_send_stream_options(kind: str) -> None:
    """`stream_options` en una llamada NO-stream es un 400 en Azure OpenAI."""
    client, seen = _dual_client()
    await _BUILDERS[kind](client).complete([Message(role="user", content="hi")])
    assert "stream_options" not in seen[-1]


# ---------------------------------------------------------------------------
# 2. Paridad complete() vs stream(): mismos tokens, mismos tool_calls
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_BUILDERS))
async def test_stream_usage_matches_complete(kind: str) -> None:
    client, _seen = _dual_client()
    provider = _BUILDERS[kind](client)
    completed = await provider.complete([Message(role="user", content="hi")])
    _text, final = await _drain(provider)
    assert final.usage is not None, "el chunk final no trae usage"
    assert final.usage.input_tokens == completed.usage.input_tokens == 11
    assert final.usage.output_tokens == completed.usage.output_tokens == 7
    assert final.usage.cache_read_tokens == completed.usage.cache_read_tokens == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_BUILDERS))
async def test_stream_tool_calls_match_complete(kind: str) -> None:
    client, _seen = _dual_client()
    provider = _BUILDERS[kind](client)
    completed = await provider.complete([Message(role="user", content="hi")])
    _text, final = await _drain(provider)
    assert completed.tool_calls is not None
    assert final.tool_calls is not None
    assert [(c.id, c.name, c.arguments) for c in final.tool_calls] == [
        (c.id, c.name, c.arguments) for c in completed.tool_calls
    ]
    # ...y los fragmentos de arguments llegaron ya reensamblados en dict.
    assert final.tool_calls[0].arguments == {"a": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(_BUILDERS))
async def test_stream_text_matches_complete_content(kind: str) -> None:
    client, _seen = _dual_client()
    provider = _BUILDERS[kind](client)
    completed = await provider.complete([Message(role="user", content="hi")])
    text, _final = await _drain(provider)
    assert text == completed.content == "Hola"


# ---------------------------------------------------------------------------
# 3. Casos límite del transporte
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_usage_survives_a_stream_that_never_sends_done() -> None:
    """Hallazgo de paso: algunos servidores cierran el stream sin ``[DONE]``.
    Antes NO se emitía chunk final y se perdían usage Y tool_calls; ahora el
    cierre del cuerpo también cierra el chunk."""
    lines = [line for line in _SSE_LINES if b"[DONE]" not in line]
    client, _seen = _dual_client(lines)
    _text, final = await _drain(_ollama(client))
    assert final.usage is not None
    assert final.usage.input_tokens == 11
    assert final.tool_calls is not None


@pytest.mark.asyncio
async def test_exactly_one_done_chunk_is_emitted() -> None:
    """La guarda del arreglo anterior: no puede emitirse el final dos veces
    (el caller acumularía el usage por duplicado y el coste saldría al doble)."""
    client, _seen = _dual_client()
    dones = [
        chunk
        async for chunk in _ollama(client).stream([Message(role="user", content="hi")])
        if chunk.done
    ]
    assert len(dones) == 1


@pytest.mark.asyncio
async def test_stream_without_usage_chunk_reports_none() -> None:
    """Honestidad: si el proveedor NO manda usage, el chunk final lo deja en
    ``None`` — nunca un cero inventado, que sería indistinguible de "gratis"."""
    lines = [line for line in _SSE_LINES if b'"usage"' not in line]
    client, _seen = _dual_client(lines)
    _text, final = await _drain(_ollama(client))
    assert final.usage is None


@pytest.mark.asyncio
async def test_usage_cost_is_read_when_the_gateway_adds_it() -> None:
    """Una policy de APIM puede añadir ``cost`` al usage; si viene, viaja."""
    priced = dict(_USAGE, cost=0.0042)
    lines = [
        b'data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n',
        b'data: {"choices":[],"usage":' + json.dumps(priced).encode() + b"}\n",
        b"data: [DONE]\n",
    ]
    client, _seen = _dual_client(lines)
    _text, final = await _drain(_azure(client))
    assert final.usage is not None
    assert final.usage.cost_usd == pytest.approx(0.0042)
