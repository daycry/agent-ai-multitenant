"""HTTP client for `/internal/agent/*` (Plan 04.5 task_04_5_03).

ADR 0012: the agent-runtime sandbox holds no DB credentials and no
direct platform access. Anything that needs the platform — memory
recall, memory store, RAG search, document convert, promote-to-kb —
goes over HTTP to the api-server's `/internal/agent/*` endpoints,
carrying a short-lived bearer token the worker mints just before
launching the container.

Two env vars wire the client:

  * ``AGENTIC_API_URL``           base URL of the api-server, e.g.
                                  ``http://api-server:8000``.
  * ``AGENTIC_INTERNAL_TOKEN``    bearer token minted by the worker
                                  via :func:`mint_agent_token`.

The client is a thin wrapper over ``httpx.Client`` — sync, because
the tool registry the agent loop uses is sync too. Errors fold into
typed exceptions so the per-tool adapters can map them to
``ToolResult.error`` lines.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

# Defaults — overridden by env in :func:`from_env`.
_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_API_URL = "http://api-server:8000"


class InternalAPIError(RuntimeError):
    """Base for anything that goes wrong calling /internal/agent/*."""


class InternalAPIConfigError(InternalAPIError):
    """Missing or malformed env vars."""


class InternalAPIHTTPError(InternalAPIError):
    """The server returned a non-2xx status."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"HTTP {status_code}: {body}")
        self.status_code = status_code
        self.body = body


@dataclass
class InternalAgentAPI:
    """Bound to one (base_url, bearer_token) pair.

    A single instance is shared across all per-tool adapters within
    one run; on teardown the agent loop calls :meth:`close` so the
    httpx client releases its sockets.
    """

    base_url: str
    bearer_token: str
    timeout_s: float = _DEFAULT_TIMEOUT_S
    client: httpx.Client | None = None

    def __post_init__(self) -> None:
        if not self.base_url:
            raise InternalAPIConfigError("base_url is required")
        if not self.bearer_token:
            raise InternalAPIConfigError("bearer_token is required")
        self.base_url = self.base_url.rstrip("/")
        if self.client is None:
            self.client = httpx.Client(timeout=self.timeout_s)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> InternalAgentAPI:
        """Build the client from ``AGENTIC_API_URL`` + ``AGENTIC_INTERNAL_TOKEN``.

        Raises :class:`InternalAPIConfigError` if the token is missing —
        the worker is expected to inject it. Missing ``AGENTIC_API_URL``
        is more lenient (defaults to the platform's standard hostname)
        so a sandbox run can still self-test offline.
        """
        env = env if env is not None else dict(os.environ)
        token = env.get("AGENTIC_INTERNAL_TOKEN") or ""
        if not token:
            raise InternalAPIConfigError(
                "AGENTIC_INTERNAL_TOKEN is missing — the worker must inject it"
            )
        return cls(
            base_url=env.get("AGENTIC_API_URL") or _DEFAULT_API_URL,
            bearer_token=token,
        )

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None

    def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        if self.client is None:
            raise InternalAPIError("client has been closed")
        url = f"{self.base_url}{path}"
        response = self.client.post(
            url,
            json=json,
            headers={"Authorization": f"Bearer {self.bearer_token}"},
        )
        if response.status_code >= 400:
            raise InternalAPIHTTPError(response.status_code, response.text)
        decoded: dict[str, Any] = response.json()
        return decoded

    # -- Endpoint adapters -------------------------------------------------
    def memory_recall(
        self,
        *,
        query: str,
        scopes: list[str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Hybrid recall. Returns the list of hits."""
        body: dict[str, Any] = {"query": query, "limit": limit}
        if scopes is not None:
            body["scopes"] = scopes
        payload = self._post("/internal/agent/memory-recall", body)
        hits: list[dict[str, Any]] = payload.get("hits") or []
        return hits

    def memory_store(
        self,
        *,
        content: str,
        type_: str = "semantic",
        scope: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Persist one memory. Returns ``{memory_id, scope, type}``."""
        body: dict[str, Any] = {"content": content, "type": type_}
        if scope is not None:
            body["scope"] = scope
        if tags:
            body["tags"] = tags
        return self._post("/internal/agent/memory-store", body)

    def rag_search(
        self,
        *,
        query: str,
        limit: int = 5,
        recall_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Project-scoped RAG over KB chunks. Returns the list of hits."""
        body: dict[str, Any] = {"query": query, "limit": limit, "recall_k": recall_k}
        payload = self._post("/internal/agent/rag-search", body)
        hits: list[dict[str, Any]] = payload.get("hits") or []
        return hits


__all__ = [
    "InternalAPIConfigError",
    "InternalAPIError",
    "InternalAPIHTTPError",
    "InternalAgentAPI",
]
