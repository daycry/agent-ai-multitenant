"""Ollama: local y cloud.

Ollama expone un endpoint compatible OpenAI en `/v1/chat/completions`,
así que el código es casi idéntico al de Azure Foundry.

Local:  base_url="http://localhost:11434/v1"  (sin api_key)
Cloud:  base_url="https://ollama.com/v1"      (api_key requerida)

El endpoint nativo de Ollama (`/api/chat`) tiene un formato distinto y soporta
algunas opciones extra (keep_alive, num_ctx, etc.); si las necesitas, ver el
método `complete_native` al final.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from ..exceptions import AuthError, ProviderError, RateLimitError
from ..types import CompletionResponse, Message, StreamChunk, Usage


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,  # requerido para ollama.com, opcional en local
        default_model: str = "llama3.1",
        timeout: float = 120.0,  # local puede tardar más en modelos grandes
    ):
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)

    @classmethod
    def local(cls, *, host: str = "http://localhost:11434", **kw) -> OllamaProvider:
        return cls(base_url=f"{host}/v1", **kw)

    @classmethod
    def cloud(cls, *, api_key: str, **kw) -> OllamaProvider:
        return cls(base_url="https://ollama.com/v1", api_key=api_key, **kw)

    def _to_openai_messages(self, messages: Sequence[Message]) -> list[dict[str, Any]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def _check(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise AuthError(f"Auth Ollama: {resp.status_code} {resp.text}")
        if resp.status_code == 429:
            raise RateLimitError(resp.text)
        if resp.status_code >= 400:
            raise ProviderError(resp.text, status_code=resp.status_code)

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
            "model": model or self.default_model,
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        resp = await self._client.post(f"{self.base_url}/chat/completions", json=body)
        self._check(resp)
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        usage_d = data.get("usage", {}) or {}
        return CompletionResponse(
            content=content,
            model=body["model"],
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
            "model": model or self.default_model,
            "messages": self._to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        async with self._client.stream(
            "POST", f"{self.base_url}/chat/completions", json=body
        ) as resp:
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

    async def list_models(self) -> list[str]:
        """Útil para descubrir qué modelos tienes (local) o están disponibles (cloud)."""
        resp = await self._client.get(f"{self.base_url}/models")
        self._check(resp)
        return [m["id"] for m in resp.json().get("data", [])]

    async def aclose(self) -> None:
        await self._client.aclose()
