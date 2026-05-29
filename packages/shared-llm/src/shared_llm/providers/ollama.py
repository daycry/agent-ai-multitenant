"""Ollama provider — local and cloud share the same wrapper (ADR 0021).

Ollama exposes an OpenAI-compatible endpoint at `/v1/chat/completions`.
Two typical deployments:

  - **Local** (the host runs `ollama serve`):
      `base_url = "http://localhost:11434/v1"`, no api_key.
  - **Cloud** (Ollama-managed inference at `ollama.com`):
      `base_url = "https://ollama.com/v1"`, api_key required.

The factory classmethods `.local()` and `.cloud()` cover both.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from shared_llm.providers._openai_compat import (
    check_status,
    iter_sse_chunks,
    parse_chat_completion,
    to_openai_messages,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk


class OllamaProvider:
    """OpenAI-compatible client for Ollama (local or cloud)."""

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434/v1",
        api_key: str | None = None,
        default_model: str = "llama3.1",
        timeout: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._api_key = api_key
        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            self._client = httpx.AsyncClient(timeout=timeout)
            self._owns_client = True

    def _headers(self) -> dict[str, str]:
        """Auth + content-type per request — works with both owned and
        injected http clients (a test transport that does not set the
        defaults still gets the right Authorization)."""
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    # ------------------------------------------------------------------
    # Constructors for the two common deployments
    # ------------------------------------------------------------------
    @classmethod
    def local(
        cls,
        *,
        host: str = "http://localhost:11434",
        default_model: str = "llama3.1",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> OllamaProvider:
        return cls(
            base_url=f"{host}/v1",
            api_key=None,
            default_model=default_model,
            timeout=timeout,
            http_client=http_client,
        )

    @classmethod
    def cloud(
        cls,
        *,
        api_key: str,
        default_model: str = "llama3.1",
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> OllamaProvider:
        return cls(
            base_url="https://ollama.com/v1",
            api_key=api_key,
            default_model=default_model,
            timeout=timeout,
            http_client=http_client,
        )

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------
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
        model_id = model or self.default_model
        body: dict[str, Any] = {
            "model": model_id,
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
        resp = await self._client.post(
            f"{self.base_url}/chat/completions", json=body, headers=self._headers()
        )
        check_status(resp, provider=self.name)
        return parse_chat_completion(resp.json(), provider=self.name, fallback_model=model_id)

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        model_id = model or self.default_model
        body: dict[str, Any] = {
            "model": model_id,
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
        async with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            json=body,
            headers=self._headers(),
        ) as resp:
            check_status(resp, provider=self.name)
            # iter_sse_chunks wraps the body iteration so a mid-stream
            # network/transport error becomes a typed ProviderError.
            async for chunk in iter_sse_chunks(resp, provider=self.name):
                yield chunk

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Ollama-specific helper — handy for the admin-panel "pick a model"
    # ------------------------------------------------------------------
    async def list_models(self) -> list[str]:
        resp = await self._client.get(f"{self.base_url}/models", headers=self._headers())
        check_status(resp, provider=self.name)
        return [m["id"] for m in resp.json().get("data", [])]


__all__ = ["OllamaProvider"]
