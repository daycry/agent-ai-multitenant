"""Curated embedding-model catalog + Ollama /api/tags discovery (ADR 0056).

Pure logic + a mocked httpx transport — no DB, no real Ollama. Pins:
  * the catalog's dim lookup (family base + size-specific tag, :latest tolerated),
  * unknown (chat) models are NOT treated as embedders,
  * only 768-dim models are "compatible"/"recommended",
  * /api/tags discovery filters to known embedders, marks active + compatible,
  * an unreachable Ollama degrades to reachable=False (never raises).
"""

from __future__ import annotations

import httpx
import pytest
from api_server.db.knowledge import CHUNK_EMBEDDING_DIM
from api_server.ingestion.embedding_models import (
    KNOWN_EMBEDDING_MODELS,
    discover_embedding_models,
    fetch_installed_models,
    is_compatible,
    known_dim,
    recommended_models,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Catalog logic
# ---------------------------------------------------------------------------
def test_nomic_is_the_compatible_default() -> None:
    assert KNOWN_EMBEDDING_MODELS["nomic-embed-text"] == CHUNK_EMBEDDING_DIM
    assert is_compatible("nomic-embed-text")
    assert is_compatible("nomic-embed-text:latest")  # :latest tolerated


def test_known_dim_matches_family_and_size_tag() -> None:
    assert known_dim("mxbai-embed-large") == 1024
    assert known_dim("mxbai-embed-large:latest") == 1024
    # A size-specific tag overrides the family default dim.
    assert known_dim("snowflake-arctic-embed") == 1024
    assert known_dim("snowflake-arctic-embed:110m") == 768


def test_unknown_model_is_not_an_embedder() -> None:
    assert known_dim("llama3.1") is None
    assert known_dim("llama3.1:8b") is None
    assert is_compatible("llama3.1") is False


def test_incompatible_dims_excluded() -> None:
    assert is_compatible("mxbai-embed-large") is False  # 1024
    assert is_compatible("all-minilm") is False  # 384


def test_recommended_are_all_768() -> None:
    rec = recommended_models()
    assert "nomic-embed-text" in rec
    assert all(known_dim(n) == CHUNK_EMBEDDING_DIM for n in rec)
    assert "mxbai-embed-large" not in rec  # 1024 → not recommended


# ---------------------------------------------------------------------------
# /api/tags fetch
# ---------------------------------------------------------------------------
def _tags_client(
    models: list[str] | None = None,
    *,
    status_code: int = 200,
    raise_conn: bool = False,
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        if raise_conn:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(status_code, json={"models": [{"name": m} for m in (models or [])]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_installed_models_parses_names() -> None:
    client = _tags_client(["nomic-embed-text:latest", "llama3.1:8b"])
    try:
        names = await fetch_installed_models(base_url="http://test", client=client)
    finally:
        await client.aclose()
    assert names == ["nomic-embed-text:latest", "llama3.1:8b"]


@pytest.mark.asyncio
async def test_fetch_installed_models_none_when_unreachable() -> None:
    client = _tags_client(raise_conn=True)
    try:
        names = await fetch_installed_models(base_url="http://test", client=client)
    finally:
        await client.aclose()
    assert names is None


@pytest.mark.asyncio
async def test_fetch_installed_models_none_on_non_2xx() -> None:
    client = _tags_client(status_code=500)
    try:
        names = await fetch_installed_models(base_url="http://test", client=client)
    finally:
        await client.aclose()
    assert names is None


# ---------------------------------------------------------------------------
# Discovery aggregation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discover_filters_to_known_embedders_and_marks_active() -> None:
    from api_server.config import Settings

    cfg = Settings(embedding_model="nomic-embed-text")
    client = _tags_client(["nomic-embed-text:latest", "mxbai-embed-large:latest", "llama3.1:8b"])
    try:
        result = await discover_embedding_models(settings=cfg, client=client)
    finally:
        await client.aclose()

    assert result.ollama_reachable is True
    assert result.active_model == "nomic-embed-text"
    assert result.required_dim == CHUNK_EMBEDDING_DIM

    by_name = {e.name: e for e in result.installed}
    # The chat model is NOT a known embedder → excluded entirely.
    assert "llama3.1:8b" not in by_name
    # Known embedders are present, flagged for compatibility.
    assert by_name["nomic-embed-text:latest"].compatible is True
    assert by_name["nomic-embed-text:latest"].active is True
    assert by_name["mxbai-embed-large:latest"].compatible is False
    assert by_name["mxbai-embed-large:latest"].active is False
    assert "nomic-embed-text" in result.recommended


@pytest.mark.asyncio
async def test_discover_unreachable_reports_false_but_keeps_guidance() -> None:
    from api_server.config import Settings

    cfg = Settings(embedding_model="nomic-embed-text")
    client = _tags_client(raise_conn=True)
    try:
        result = await discover_embedding_models(settings=cfg, client=client)
    finally:
        await client.aclose()

    assert result.ollama_reachable is False
    assert result.installed == []
    assert result.active_model == "nomic-embed-text"
    # Still tells the operator what they could pull.
    assert "nomic-embed-text" in result.recommended
