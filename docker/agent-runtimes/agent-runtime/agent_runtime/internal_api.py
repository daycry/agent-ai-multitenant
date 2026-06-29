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
import time
from dataclasses import dataclass
from typing import Any

import httpx

# Defaults — overridden by env in :func:`from_env`.
_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_API_URL = "http://api-server:8000"

# Reachability probe retry policy (F22 / audit C5). A single GET that hit a
# transient hiccup (a connect race while the api-server's network alias settles,
# a momentary refusal) used to tear down the whole run; a short bounded retry
# absorbs the hiccup before declaring the API down.
_DEFAULT_REACHABLE_ATTEMPTS = 3
_DEFAULT_REACHABLE_BACKOFF_S = 0.5


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


class InternalAPIUnreachableError(InternalAPIError):
    """The internal API host did not answer at all (connect/timeout). Raised by
    :meth:`InternalAgentAPI.ensure_reachable` so a production boot fails loudly
    instead of silently degrading (Plan prod-01 task_11 / sandbox-4)."""


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
            # trust_env=False: /internal/agent/* must reach the api-server
            # DIRECTLY over the internal network, NEVER through the
            # HTTP(S)_PROXY (the deny-by-default egress-proxy has no api-server
            # allow entry) (Plan prod-01 task_11 / sandbox-4).
            self.client = httpx.Client(timeout=self.timeout_s, trust_env=False)

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

    def ensure_reachable(
        self,
        *,
        attempts: int = _DEFAULT_REACHABLE_ATTEMPTS,
        backoff_s: float = _DEFAULT_REACHABLE_BACKOFF_S,
    ) -> None:
        """Fail LOUDLY at boot if the api-server's internal API is not reachable
        (Plan prod-01 task_11 / sandbox-4). A bare run with no token never gets
        here (``from_env`` raised first); when a token WAS injected we are a
        production run with an assigned agent, so an unreachable API is a hard
        error — not a silent skip of the knowledge/memory families.

        Probes the unauthenticated ``/healthz`` (a route, not the proxy) so a
        network/route misconfiguration surfaces immediately. A single GET is too
        brittle (F22): a transient connect race tumbles the whole run, so we
        retry ``attempts`` times with a short linear backoff and only raise
        :class:`InternalAPIUnreachableError` once every attempt has failed.
        """
        if self.client is None:
            raise InternalAPIError("client has been closed")
        last_exc: httpx.HTTPError | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = self.client.get(f"{self.base_url}/healthz")
                response.raise_for_status()
                return
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt < attempts:
                    # Transient hiccup — back off (linearly) and retry before
                    # declaring the API down.
                    time.sleep(backoff_s * attempt)
        raise InternalAPIUnreachableError(
            f"internal API at {self.base_url} is not reachable after {attempts} "
            f"attempt(s): {last_exc!r}. The sandbox needs a network route to api-server "
            "(agentic-agents) and must bypass the egress-proxy (trust_env=False)."
        ) from last_exc

    def _post(
        self, path: str, json: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        if self.client is None:
            raise InternalAPIError("client has been closed")
        url = f"{self.base_url}{path}"
        # ``timeout`` overrides the client default per request — a stack command
        # (composer install) can take minutes, far longer than the 15s default.
        response = self.client.post(
            url,
            json=json,
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=timeout if timeout is not None else self.timeout_s,
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

    def run_stack(self, *, task_id: str, command: str, timeout_s: int = 600) -> dict[str, Any]:
        """Run a stack command (``composer install`` / ``vendor/bin/phpunit`` /
        ``php spark``) in the project's runtime template via the worker (ADR 0093).

        The sandbox cannot launch containers; this asks the worker, which has
        Docker. Returns ``{exit_code, logs, timed_out}``. Waits the command's own
        budget + a margin (the HTTP call blocks until the worker finishes)."""
        return self._post(
            "/internal/agent/run-stack",
            {"task_id": task_id, "command": command, "timeout_s": int(timeout_s)},
            timeout=float(timeout_s) + 120.0,
        )

    def document_convert(self, *, document_id: str) -> dict[str, Any]:
        """Structured chunks of an existing Document. v1 reads them
        from the chunks table; full re-parse mode lands with chat-
        file-upload in Plan 07."""
        return self._post("/internal/agent/document-convert", {"document_id": document_id})

    def promote_to_kb(
        self,
        *,
        document_id: str,
        target_kb_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Copy a Document + its chunks into a different KB."""
        body: dict[str, Any] = {"document_id": document_id, "target_kb_id": target_kb_id}
        if title is not None:
            body["title"] = title
        return self._post("/internal/agent/promote-to-kb", body)


__all__ = [
    "InternalAPIConfigError",
    "InternalAPIError",
    "InternalAPIHTTPError",
    "InternalAgentAPI",
]
