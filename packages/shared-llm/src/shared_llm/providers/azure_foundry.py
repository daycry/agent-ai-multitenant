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

from shared_llm.providers._adaptive import AdaptationMemo, adapt_body, needs_responses_api
from shared_llm.providers._openai_compat import (
    build_chat_body,
    check_status,
    check_stream_status,
    error_text,
    iter_sse_chunks,
    parse_chat_completion,
    post_adaptive,
    typed_transport_errors,
)
from shared_llm.providers._openai_responses import (
    parse_responses,
    to_responses_body,
    whole_as_chunks,
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
        # ADR 0169 D2: lo que el despliegue rechazó, para no volver a pedírselo.
        self._adapt = AdaptationMemo()

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

    def _responses_url(self) -> str:
        """El endpoint v1 de la Responses API (ADR 0169, `task_cm_06`).

        La v1 no lleva ``api-version`` y recibe el despliegue en ``model``. Es la vía
        que Microsoft documenta para Codex, que no acepta ``/chat/completions``.
        """
        return f"{self.base_url}/openai/v1/responses"

    async def _post_json(self, url: str, body: dict[str, Any]) -> httpx.Response:
        async with self._acquire() as client:
            return await client.post(url, json=body, headers=self._headers())

    async def _complete_responses(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int | None,
        temperature: float | None,
        tools: list[dict[str, Any]] | None,
        **kwargs: Any,
    ) -> CompletionResponse:
        adaptations = self._adapt.for_model(self.deployment)
        body = to_responses_body(
            messages,
            model=self.deployment,
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            **kwargs,
        )
        async with typed_transport_errors(provider=self.name):
            resp = await post_adaptive(
                lambda b: self._post_json(self._responses_url(), b),
                body,
                adaptations,
                may_switch_transport=False,
            )
            assert resp is not None
            check_status(resp, provider=self.name)
            return parse_responses(resp.json(), provider=self.name, fallback_model=self.deployment)

    # ------------------------------------------------------------------
    # LLMProvider Protocol
    # ------------------------------------------------------------------
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        adaptations = self._adapt.for_model(self.deployment)
        if adaptations.responses_api:
            return await self._complete_responses(
                messages, max_tokens=max_tokens, temperature=temperature, tools=tools, **kwargs
            )
        body = build_chat_body(
            messages,
            model=None,  # la URL de APIM fija el despliegue
            max_tokens=max_tokens,
            temperature=temperature,
            tools=tools,
            **kwargs,
        )
        async with typed_transport_errors(provider=self.name):
            resp = await post_adaptive(
                lambda b: self._post_json(self._url(), b),
                body,
                adaptations,
                may_switch_transport=True,
            )
            if resp is None:
                return await self._complete_responses(
                    messages, max_tokens=max_tokens, temperature=temperature, tools=tools, **kwargs
                )
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
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        adaptations = self._adapt.for_model(self.deployment)
        if not adaptations.responses_api:
            body = adaptations.apply(
                build_chat_body(
                    messages,
                    model=None,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tools=tools,
                    stream=True,
                    # prod-07 task_prod07_04: sin `include_usage` el proveedor NO manda el
                    # chunk de usage y el turno se contabiliza a 0 tokens (llm-6). Va antes
                    # de `**kwargs` para que un caller pueda desactivarlo con None.
                    **{"stream_options": {"include_usage": True}, **kwargs},
                )
            )
            for attempt in (0, 1):
                async with (
                    self._acquire() as client,
                    client.stream("POST", self._url(), json=body, headers=self._headers()) as resp,
                ):
                    if resp.status_code == 400:
                        # ADR 0169 D2: se lee el error ANTES de ceder nada, así que
                        # adaptar y reabrir no duplica texto en el caller.
                        await resp.aread()
                        text = error_text(resp)
                        if needs_responses_api(text):
                            adaptations.responses_api = True
                            break
                        fixed = adapt_body(body, text) if attempt == 0 else None
                        if fixed is not None:
                            adaptations.learn(body, fixed)
                            body = fixed
                            continue
                    # prod-07 task_prod07_03: lee el cuerpo antes de clasificar un no-2xx.
                    await check_stream_status(resp, provider=self.name)
                    async for chunk in iter_sse_chunks(resp, provider=self.name):
                        yield chunk
                    return
        # Modelo sólo Responses: sin streaming, la respuesta entera en un trozo.
        whole = await self._complete_responses(
            messages, max_tokens=max_tokens, temperature=temperature, tools=tools, **kwargs
        )
        for chunk in whole_as_chunks(whole):
            yield chunk

    async def aclose(self) -> None:
        # Owned clients are per-call (closed in `_acquire`); injected clients belong to
        # the caller. Nothing persistent to release.
        return None


__all__ = ["AzureFoundryAPIMProvider"]
