"""Azure AI Foundry behind Azure API Management (ADR 0021).

APIM forwards calls to a Foundry deployment using the standard Azure
OpenAI URL layout:

    {apim_base_url}/openai/deployments/{deployment}
                  /chat/completions?api-version={api_version}

Auth on APIM is typically `Ocp-Apim-Subscription-Key` (subscription
quotas / billing) OR a `Bearer` token (when APIM validates a JWT).
The provider accepts either.

For organisations that prefer to skip APIM and hit Azure OpenAI
directly, the same provider works with `apim_base_url` pointing at the
Azure OpenAI endpoint (`https://<resource>.openai.azure.com`) and a
`bearer_token` from AAD — the URL shape is identical.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import httpx

from shared_llm.providers._openai_compat import (
    check_status,
    iter_sse_chunks,
    parse_chat_completion,
    to_openai_messages,
)
from shared_llm.types import CompletionResponse, Message, StreamChunk


class AzureFoundryAPIMProvider:
    name = "azure_foundry_apim"

    def __init__(
        self,
        *,
        apim_base_url: str,
        deployment: str,
        subscription_key: str | None = None,
        bearer_token: str | None = None,
        api_version: str = "2024-10-21",
        extra_headers: dict[str, str] | None = None,
        timeout: float = 60.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not subscription_key and not bearer_token:
            raise ValueError(
                "AzureFoundryAPIMProvider needs either subscription_key or bearer_token"
            )
        self.base_url = apim_base_url.rstrip("/")
        self.deployment = deployment
        self.api_version = api_version
        self._subscription_key = subscription_key
        self._bearer_token = bearer_token
        self._extra_headers = dict(extra_headers or {})
        self._timeout = timeout

        if http_client is not None:
            self._client: httpx.AsyncClient | None = http_client
            self._owns_client = False
        else:
            # Owned client created PER CALL (see `_acquire`): a single cached client
            # bound to one event loop breaks when the provider is used across loops
            # (e.g. the planning bridge calls complete() via asyncio.run per step).
            self._client = None
            self._owns_client = True

    @asynccontextmanager
    async def _acquire(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield an httpx client valid for THIS call. Owned → fresh client bound to the
        current loop (closed on exit); injected → the caller's client (untouched)."""
        if self._owns_client:
            client = httpx.AsyncClient(timeout=self._timeout)
            try:
                yield client
            finally:
                await client.aclose()
        else:
            assert self._client is not None
            yield self._client

    def _headers(self) -> dict[str, str]:
        """Auth + content-type per request — applied even when an
        external http_client (test transport) is injected."""
        h = {"Content-Type": "application/json"}
        if self._subscription_key:
            h["Ocp-Apim-Subscription-Key"] = self._subscription_key
        if self._bearer_token:
            h["Authorization"] = f"Bearer {self._bearer_token}"
        h.update(self._extra_headers)
        return h

    def _url(self) -> str:
        return (
            f"{self.base_url}/openai/deployments/{self.deployment}"
            f"/chat/completions?api-version={self.api_version}"
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
        body: dict[str, Any] = {
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
        async with self._acquire() as client:
            resp = await client.post(self._url(), json=body, headers=self._headers())
            check_status(resp, provider=self.name)
            return parse_chat_completion(
                resp.json(), provider=self.name, fallback_model=model or self.deployment
            )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,  # noqa: ARG002 — APIM URL pins the model
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        body: dict[str, Any] = {
            "messages": to_openai_messages(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            **kwargs,
        }
        if tools:
            body["tools"] = tools
        async with (
            self._acquire() as client,
            client.stream("POST", self._url(), json=body, headers=self._headers()) as resp,
        ):
            check_status(resp, provider=self.name)
            # iter_sse_chunks wraps the body iteration so a mid-stream
            # network/transport error becomes a typed ProviderError.
            async for chunk in iter_sse_chunks(resp, provider=self.name):
                yield chunk

    async def aclose(self) -> None:
        # Owned clients are per-call (closed in `_acquire`); injected clients belong to
        # the caller. Nothing persistent to release.
        return None


__all__ = ["AzureFoundryAPIMProvider"]
