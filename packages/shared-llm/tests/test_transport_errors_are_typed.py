"""AUD16 colateral A/H-7 (auditoría 2026-07-16): un error de TRANSPORTE en
``complete()`` de los providers HTTP sale TIPADO, nunca httpx crudo.

El 07-13 un turno del córtex murió con ``httpx.ReadTimeout`` de ollama →
``api.unhandled_exception`` → 500 crudo al usuario: el router del córtex ya
captura ``LLMError``/``AuthError``/``RateLimitError``, pero el provider dejaba
escapar el error de httpx sin envolver (el ``stream()`` sí lo envolvía vía
``iter_sse_chunks``; el ``complete()`` no).
"""

from __future__ import annotations

import httpx
import pytest
from shared_llm.exceptions import ProviderError
from shared_llm.providers.azure_foundry import AzureFoundryAPIMProvider
from shared_llm.providers.copilot import CopilotProvider
from shared_llm.providers.ollama import OllamaProvider
from shared_llm.types import Message

_MSGS = [Message(role="user", content="hola")]


def _timeout_client() -> httpx.AsyncClient:
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(_boom))


@pytest.mark.asyncio
async def test_ollama_complete_wraps_transport_errors() -> None:
    provider = OllamaProvider(
        base_url="http://localhost:11434/v1",
        default_model="gemma",
        http_client=_timeout_client(),
    )
    with pytest.raises(ProviderError):
        await provider.complete(_MSGS)


@pytest.mark.asyncio
async def test_azure_complete_wraps_transport_errors() -> None:
    provider = AzureFoundryAPIMProvider(
        apim_base_url="https://x.azure-api.net/foundry",
        deployment="gpt-4o",
        subscription_key="k",
        http_client=_timeout_client(),
    )
    with pytest.raises(ProviderError):
        await provider.complete(_MSGS)


@pytest.mark.asyncio
async def test_copilot_complete_wraps_transport_errors() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/copilot_internal/v2/token"):
            return httpx.Response(200, json={"token": "jwt", "expires_at": 9_999_999_999})
        raise httpx.ReadTimeout("read timed out", request=request)

    provider = CopilotProvider(
        github_token="gho_x",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    with pytest.raises(ProviderError):
        await provider.complete(_MSGS)
