"""Un status no-2xx en `stream()` tiene que dar el error TIPADO (prod-07 task_prod07_03).

El hallazgo llm-5, exacto
-------------------------
En el camino de streaming el cuerpo NO se ha leído cuando llegan las cabeceras:
``client.stream()`` entrega la respuesta con el body pendiente. ``check_status``
interpola ``resp.text`` en el mensaje del error, y ``resp.text`` sobre un cuerpo
sin leer lanza ``httpx.ResponseNotRead`` — así que un 401/429/500 en streaming
no producía ``AuthError``/``RateLimitError``/``ProviderError``, producía un
``ResponseNotRead`` opaco que ningún caller captura (todos capturan los tipos
del layer). El operador veía un error de httpx sin relación con la causa.

Por qué los tests existentes no lo veían
----------------------------------------
``test_ollama_provider.test_stream_401_is_auth_error`` (y sus gemelos de azure y
copilot) construyen ``httpx.Response(401, text="bad key")``: eso PRECARGA el
cuerpo, así que ``resp.text`` funciona y el test pasa sobre un camino que en
producción no se recorre nunca. Aquí el cuerpo va como ``AsyncByteStream``, que
es lo que hace httpcore de verdad.

Se cubren los TRES providers OpenAI-compat: azure y copilot tenían el mismo
defecto que ollama, y verificar solo uno era extrapolar.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.providers import AzureFoundryAPIMProvider, CopilotProvider, OllamaProvider
from shared_llm.types import Message


class _UnreadStream(httpx.AsyncByteStream):
    """Cuerpo NO precargado — como el que sirve httpcore en un stream real."""

    def __init__(self, payload: bytes = b'{"error":"nope"}') -> None:
        self._payload = payload
        self.read_count = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.read_count += 1
        yield self._payload

    async def aclose(self) -> None:
        return None


def _client(status: int, *, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, stream=_UnreadStream(), headers=headers or {})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _premise_check(status: int) -> None:
    """La premisa del fichero, comprobada en vez de asumida: sobre un cuerpo
    así, ``.text`` REVIENTA. Si algún día httpx cambiara, estos tests pasarían
    vacíamente y este check lo delataría."""
    resp = httpx.Response(status, stream=_UnreadStream())
    with pytest.raises(httpx.ResponseNotRead):
        _ = resp.text


def test_premise_unread_body_raises_response_not_read() -> None:
    _premise_check(401)
    _premise_check(429)


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ollama_stream_401_is_auth_error_with_real_unread_body() -> None:
    provider = OllamaProvider.cloud(api_key="sk-x", http_client=_client(401))
    with pytest.raises(AuthError):
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass


@pytest.mark.asyncio
async def test_ollama_stream_429_is_rate_limit_with_retry_after() -> None:
    """Y el hint de back-off del proveedor sobrevive al camino de streaming."""
    provider = OllamaProvider.cloud(
        api_key="sk-x", http_client=_client(429, headers={"Retry-After": "4"})
    )
    with pytest.raises(RateLimitError) as info:
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass
    assert info.value.retry_after == 4.0


@pytest.mark.asyncio
async def test_ollama_stream_500_is_provider_error_carrying_the_body() -> None:
    """El cuerpo se lee ANTES de construir el error, así que el payload del
    proveedor llega al mensaje — que es el punto: diagnosticar sin adivinar."""
    provider = OllamaProvider.cloud(api_key="sk-x", http_client=_client(500))
    with pytest.raises(ProviderError) as info:
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass
    assert info.value.status_code == 500
    assert "nope" in str(info.value)


# ---------------------------------------------------------------------------
# Azure Foundry (APIM)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_azure_stream_401_is_auth_error_with_real_unread_body() -> None:
    provider = AzureFoundryAPIMProvider(
        apim_base_url="https://apim.example",
        deployment="gpt-4o",
        subscription_key="k",
        http_client=_client(401),
    )
    with pytest.raises(AuthError):
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass


@pytest.mark.asyncio
async def test_azure_stream_429_is_rate_limit() -> None:
    provider = AzureFoundryAPIMProvider(
        apim_base_url="https://apim.example",
        deployment="gpt-4o",
        subscription_key="k",
        http_client=_client(429),
    )
    with pytest.raises(RateLimitError):
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass


# ---------------------------------------------------------------------------
# Copilot — con su re-mint del JWT por medio
# ---------------------------------------------------------------------------
def _copilot_client(chat_status: int) -> httpx.AsyncClient:
    """Mint del JWT OK (cuerpo precargado, es un POST normal) + chat en error
    con cuerpo SIN leer."""

    def handler(req: httpx.Request) -> httpx.Response:
        if "copilot_internal" in str(req.url):
            return httpx.Response(200, json={"token": "jwt-x", "expires_at": 9e9})
        return httpx.Response(chat_status, stream=_UnreadStream())

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_copilot_stream_429_is_rate_limit_with_real_unread_body() -> None:
    provider = CopilotProvider(github_token="gho_x", http_client=_copilot_client(429))
    with pytest.raises(RateLimitError):
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass


@pytest.mark.asyncio
async def test_copilot_stream_401_retries_then_raises_auth_error() -> None:
    """El 401 del chat dispara UN re-mint y reintento; si vuelve 401, el error
    tiene que ser ``AuthError`` tipado — el segundo camino (``retry_resp``)
    también leía el cuerpo sin haberlo leído."""
    mints: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if "copilot_internal" in str(req.url):
            mints.append(1)
            return httpx.Response(200, json={"token": "jwt-x", "expires_at": 9e9})
        return httpx.Response(401, stream=_UnreadStream())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = CopilotProvider(github_token="gho_x", http_client=client)
    with pytest.raises(AuthError):
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass
    assert len(mints) == 2  # mint inicial + re-mint tras el 401


# ---------------------------------------------------------------------------
# No-regresión: el camino 2xx sigue entregando deltas
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_healthy_stream_still_yields_its_deltas() -> None:
    """La guarda: leer el cuerpo SOLO debe pasar en el camino de error. Si se
    leyera siempre, el streaming dejaría de ser streaming."""
    body = (
        b'data: {"choices":[{"delta":{"content":"ho"}}]}\n\n'
        b'data: {"choices":[{"delta":{"content":"la"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    stream = _UnreadStream(body)

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    provider = OllamaProvider.cloud(
        api_key="sk-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    deltas = [
        chunk.delta
        async for chunk in provider.stream([Message(role="user", content="hi")])
        if chunk.delta
    ]
    assert deltas == ["ho", "la"]


@pytest.mark.asyncio
async def test_error_body_is_read_exactly_once() -> None:
    """Y en el camino de error se lee UNA vez (no se re-lee por cada branch)."""
    stream = _UnreadStream(json.dumps({"error": "quota"}).encode())

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, stream=stream)

    provider = OllamaProvider.cloud(
        api_key="sk-x", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(RateLimitError) as info:
        async for _chunk in provider.stream([Message(role="user", content="hi")]):
            pass
    assert stream.read_count == 1
    assert "quota" in str(info.value)
