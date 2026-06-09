"""Embedder for chunks + memory query (Plan 04 task_04_14).

Two callers consume this:

  - the ingestion worker — embeds every chunk after Docling parses
    the document;
  - `memory_recall` / `rag_search` — embed the query at search time.

A Protocol abstracts the backend so tests inject a deterministic
:class:`HashEmbedder` and production wires the real Ollama HTTP
client. The model defaults to ``settings.embedding_model``
(``nomic-embed-text``, 768 dims; ADR 0056). The hash-based fake
produces stable vectors keyed by content, which is all the
integration test needs to assert ranking behaviour.

Why a separate module from `shared-llm`:

  - `shared-llm` is intentionally narrow (Chat-only LLMProvider
    protocol; ADR 0021). Embedders have a different shape and we
    don't want to widen the LLM Protocol.
  - The embedder is a hot path (one call per chunk on ingest, one
    per query at search). The thin wrapper here lets us add caching
    and batching without touching shared-llm.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import Any, Protocol

import httpx
import structlog

from api_server.config import Settings, get_settings
from api_server.db.knowledge import CHUNK_EMBEDDING_DIM

logger = structlog.get_logger(__name__)


class EmbeddingError(RuntimeError):
    """Raised by the real embedder when the backend is unreachable
    or returns an unexpected shape."""


class Embedder(Protocol):
    """Async-friendly batch embedder."""

    @property
    def model_id(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Real implementation: Ollama
# ---------------------------------------------------------------------------
class OllamaEmbedder:
    """Hits Ollama's ``POST /api/embed`` endpoint.

    Ollama's modern API accepts a list of inputs and returns a list
    of vectors — we use the batch form so a 200-chunk document is
    one round-trip, not 200.
    """

    def __init__(
        self,
        *,
        model_id: str | None = None,
        dim: int = CHUNK_EMBEDDING_DIM,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 60.0,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        # An explicit model_id wins (per-KB re-embed); otherwise fall back to
        # the configured default (ADR 0056 — the real registry name, no -v1.5).
        self._model_id = model_id or cfg.embedding_model
        self._dim = dim
        self._base_url = (base_url or cfg.ollama_url).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = await self._client.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model_id, "input": list(texts)},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"ollama embed request failed: {exc}") from exc
        if response.status_code >= 400:
            raise EmbeddingError(f"ollama embed {response.status_code}: {response.text[:300]}")
        body: dict[str, Any] = response.json()
        embeddings = body.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(texts):
            raise EmbeddingError(
                f"ollama embed returned {len(embeddings) if isinstance(embeddings, list) else '?'}"
                f" vectors for {len(texts)} inputs"
            )
        out: list[list[float]] = []
        for vec in embeddings:
            if not isinstance(vec, list) or len(vec) != self._dim:
                raise EmbeddingError(
                    f"ollama returned a {len(vec) if isinstance(vec, list) else '?'}-dim vector,"
                    f" expected {self._dim}"
                )
            out.append([float(x) for x in vec])
        return out

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


# ---------------------------------------------------------------------------
# Test fake
# ---------------------------------------------------------------------------
class HashEmbedder:
    """Deterministic, content-addressable fake.

    Produces a unit-norm 768-d vector keyed by SHA256(text). Same
    text → same vector → predictable cosine ranking for tests. NOT
    a real embedding model — only useful for assertions about wiring.
    """

    def __init__(self, *, dim: int = CHUNK_EMBEDDING_DIM, model_id: str = "fake-hash") -> None:
        self._dim = dim
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    async def aclose(self) -> None:  # pragma: no cover
        pass

    def _one(self, text: str) -> list[float]:
        # Hash the text, then expand the 32-byte digest into `dim`
        # floats via a rolling SHA256 chain. L2-normalise so cosine
        # similarity is well-behaved.
        bytes_needed = self._dim * 4  # 4 bytes per float32
        seed = text.encode("utf-8")
        buf = bytearray()
        h = hashlib.sha256(seed).digest()
        while len(buf) < bytes_needed:
            buf.extend(h)
            h = hashlib.sha256(h).digest()
        floats: list[float] = []
        for i in range(self._dim):
            word = buf[i * 4 : i * 4 + 4]
            # Map to [-1, 1].
            n = int.from_bytes(word, "big")
            floats.append((n / 2**32) * 2.0 - 1.0)
        norm = math.sqrt(sum(x * x for x in floats)) or 1.0
        return [x / norm for x in floats]


__all__ = [
    "Embedder",
    "EmbeddingError",
    "HashEmbedder",
    "OllamaEmbedder",
]
