"""Embedder contract test (Plan 04 task_04_14).

The embedder is a thin abstraction so the ingestion pipeline can
swap between Ollama (production), sentence-transformers (alternative),
or a deterministic fake (tests). This file pins the contract:

  - `HashEmbedder` returns unit-norm 768-d vectors keyed by content,
  - `OllamaEmbedder` round-trips via a mocked `httpx.AsyncClient`
    against Ollama's `POST /api/embed` shape (we don't need Ollama
    running for the test),
  - `EmbeddingError` fires on wrong dim / non-200 / unparseable
    response.
"""

from __future__ import annotations

import httpx
import pytest
from api_server.db.knowledge import CHUNK_EMBEDDING_DIM
from api_server.ingestion.embeddings import (
    EmbeddingError,
    HashEmbedder,
    OllamaEmbedder,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# HashEmbedder — deterministic fake
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_hash_embedder_returns_unit_norm_vectors() -> None:
    emb = HashEmbedder()
    vecs = await emb.embed(["hello", "world"])
    assert len(vecs) == 2
    for v in vecs:
        assert len(v) == CHUNK_EMBEDDING_DIM
        norm = sum(x * x for x in v) ** 0.5
        assert abs(norm - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_hash_embedder_is_deterministic() -> None:
    emb = HashEmbedder()
    a = await emb.embed(["same text"])
    b = await emb.embed(["same text"])
    assert a == b


@pytest.mark.asyncio
async def test_hash_embedder_different_text_different_vector() -> None:
    emb = HashEmbedder()
    a = (await emb.embed(["foo"]))[0]
    b = (await emb.embed(["bar"]))[0]
    assert a != b


# ---------------------------------------------------------------------------
# OllamaEmbedder — wire shape against a mocked httpx client
# ---------------------------------------------------------------------------
def _mock_ollama(
    *,
    embeddings: list[list[float]] | None = None,
    status_code: int = 200,
    body: dict | None = None,
) -> httpx.AsyncClient:
    """Build an `httpx.AsyncClient` whose `MockTransport` answers
    `POST /api/embed` with the requested shape."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        return httpx.Response(
            status_code,
            json=body if body is not None else {"embeddings": embeddings or []},
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_ollama_embedder_round_trips() -> None:
    canned = [[0.1] * CHUNK_EMBEDDING_DIM, [0.2] * CHUNK_EMBEDDING_DIM]
    client = _mock_ollama(embeddings=canned)
    emb = OllamaEmbedder(
        model_id="nomic-embed-text-v1.5",
        base_url="http://test",
        client=client,
    )
    try:
        out = await emb.embed(["a", "b"])
    finally:
        await client.aclose()

    assert out == canned
    assert emb.dim == CHUNK_EMBEDDING_DIM
    assert emb.model_id == "nomic-embed-text-v1.5"


@pytest.mark.asyncio
async def test_ollama_embedder_empty_input_skips_request() -> None:
    """Empty input must not hit the network — embedders are on every
    chunk-write hot path."""
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"embeddings": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = OllamaEmbedder(base_url="http://test", client=client)
    try:
        out = await emb.embed([])
    finally:
        await client.aclose()
    assert out == []
    assert called is False


@pytest.mark.asyncio
async def test_ollama_embedder_raises_on_non_2xx() -> None:
    client = _mock_ollama(status_code=500, body={"error": "boom"})
    emb = OllamaEmbedder(base_url="http://test", client=client)
    try:
        with pytest.raises(EmbeddingError, match="500"):
            await emb.embed(["x"])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ollama_embedder_raises_on_mismatched_count() -> None:
    client = _mock_ollama(embeddings=[[0.0] * CHUNK_EMBEDDING_DIM])
    emb = OllamaEmbedder(base_url="http://test", client=client)
    try:
        with pytest.raises(EmbeddingError, match="2 inputs"):
            await emb.embed(["a", "b"])
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ollama_embedder_raises_on_wrong_dim() -> None:
    bad = [[0.0] * (CHUNK_EMBEDDING_DIM - 1)]
    client = _mock_ollama(embeddings=bad)
    emb = OllamaEmbedder(base_url="http://test", client=client)
    try:
        with pytest.raises(EmbeddingError, match="expected"):
            await emb.embed(["x"])
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# Configurable embedding model (ADR 0056)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ollama_embedder_default_model_is_real_registry_name() -> None:
    """With no explicit ``model_id``, the embedder asks Ollama for the model
    named in settings — whose default is the REAL registry name
    ``nomic-embed-text`` (not the ``-v1.5`` suffix that yields ``model not
    found``). ADR 0056 N-B."""
    from api_server.config import Settings

    cfg = Settings()
    client = _mock_ollama(embeddings=[[0.0] * CHUNK_EMBEDDING_DIM])
    emb = OllamaEmbedder(base_url="http://test", client=client, settings=cfg)
    try:
        assert emb.model_id == "nomic-embed-text"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_ollama_embedder_honours_configured_model() -> None:
    """``API_SERVER_EMBEDDING_MODEL`` (settings.embedding_model) drives the
    model the embedder sends when no explicit override is given."""
    from api_server.config import Settings

    cfg = Settings(embedding_model="mxbai-embed-large")
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.0] * CHUNK_EMBEDDING_DIM]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = OllamaEmbedder(base_url="http://test", client=client, settings=cfg)
    try:
        assert emb.model_id == "mxbai-embed-large"
        await emb.embed(["x"])
    finally:
        await client.aclose()
    assert sent["model"] == "mxbai-embed-large"


@pytest.mark.asyncio
async def test_ollama_embedder_explicit_model_id_overrides_settings() -> None:
    """An explicit ``model_id`` still wins over the configured default —
    callers that pin a model (per-KB re-embed) keep that power."""
    from api_server.config import Settings

    cfg = Settings(embedding_model="nomic-embed-text")
    client = _mock_ollama(embeddings=[[0.0] * CHUNK_EMBEDDING_DIM])
    emb = OllamaEmbedder(
        model_id="text-embedding-3-small", base_url="http://test", client=client, settings=cfg
    )
    try:
        assert emb.model_id == "text-embedding-3-small"
    finally:
        await client.aclose()
