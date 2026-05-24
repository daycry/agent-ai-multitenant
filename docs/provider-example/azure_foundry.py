"""Azure AI Foundry expuesto a través de Azure API Management.

APIM normalmente expone un endpoint compatible con OpenAI Chat Completions.
Auth típica: subscription key en header `Ocp-Apim-Subscription-Key` o bearer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from ..exceptions import AuthError, ProviderError, RateLimitError
from ..types import CompletionResponse, Message, StreamChunk, Usage


class AzureFoundryAPIMProvider:
    name = "azure_foundry_apim"

    def __init__(
        self,
        *,
        apim_base_url: str,  # ej: https://tu-apim.azure-api.net/foundry
        deployment: str,  # nombre del deployment / modelo en Foundry
        subscription_key: str | None = None,
        bearer_token: str | None = None,
        api_version: str = "2024-10-21",
        extra_headers: dict[str, str] | None = None,
        timeout: float = 60.0,
    ):
        if not subscription_key and not bearer_token:
            raise ValueError("Necesitas subscription_key o bearer_token")

        self.base_url = apim_base_url.rstrip("/")
        self.deployment = deployment
        self.api_version = api_version

        headers = {"Content-Type": "application/json"}
        if subscription_key:
            headers["Ocp-Apim-Subscription-Key"] = subscription_key
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
        if extra_headers:
            headers.update(extra_headers)

        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)

    def _url(self) -> str:
        # Patrón Azure OpenAI estándar; APIM suele reenviarlo igual
        return (
            f"{self.base_url}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _to_openai_messages(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        out = []
        for m in messages:
            d: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.name:
                d["name"] = m.name
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            out.append(d)
        return out

    def _check(self, resp: httpx.Response) -> None:
        if resp.status_code == 401 or resp.status_code == 403:
            raise AuthError(f"Auth APIM falló: {resp.status_code} {resp.text}")
        if resp.status_code == 429:
            raise RateLimitError(f"Rate limit APIM: {resp.text}")
        if resp.status_code >= 400:
            raise ProviderError(resp.text, status_code=resp.status_code, raw=resp.text)

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> CompletionResponse:
        body = {
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        resp = await self._client.post(self._url(), json=body)
        self._check(resp)
        data = resp.json()

        choice = data["choices"][0]["message"]["content"] or ""
        usage_d = data.get("usage", {}) or {}
        return CompletionResponse(
            content=choice,
            model=model or self.deployment,
            provider=self.name,
            usage=Usage(
                input_tokens=usage_d.get("prompt_tokens", 0),
                output_tokens=usage_d.get("completion_tokens", 0),
            ),
            raw=data,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        body = {
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        async with self._client.stream("POST", self._url(), json=body) as resp:
            self._check(resp)
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:].strip()
                if payload == "[DONE]":
                    yield StreamChunk(delta="", done=True)
                    return
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices", [{}])[0].get("delta", {}).get("content")) or ""
                if delta:
                    yield StreamChunk(delta=delta)

    async def aclose(self) -> None:
        await self._client.aclose()
