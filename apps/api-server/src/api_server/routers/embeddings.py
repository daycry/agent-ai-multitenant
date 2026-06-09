"""``/admin/embeddings`` — embedding-model discovery (ADR 0056, option S-C).

A read-only System-Admin surface that answers "which of the embedders my local
Ollama has installed can I actually use?". The model itself is FIXED via
``API_SERVER_EMBEDDING_MODEL`` (chosen at install/config time, pulled by the
``ollama-bootstrap`` one-shot); this endpoint does NOT switch it live — changing
the model with existing KBs is the Plan 12 re-embed job, not this.

  * ``GET /admin/embeddings/available-models`` — cross Ollama's native
    ``/api/tags`` with the curated embedder catalog, returning which INSTALLED
    models are embedders, which are 768-compatible (the only kind the pgvector
    schema can store), the active model, and what to pull.

The Ollama probe client is an injectable seam (:func:`get_ollama_probe_client`)
defaulting to ``None`` (the discovery layer builds + closes its own httpx
client); tests override it with a ``MockTransport`` so no network is hit.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api_server.auth.deps import AuthPrincipal, require_system_admin
from api_server.ingestion.embedding_models import discover_embedding_models

admin_router = APIRouter(prefix="/admin/embeddings", tags=["admin", "embeddings"])


def get_ollama_probe_client() -> httpx.AsyncClient | None:
    """Dependency seam for the Ollama ``/api/tags`` probe.

    Returns ``None`` in production so the discovery layer builds (and closes) its
    own short-lived httpx client. Tests override this with a ``MockTransport``
    client to drive the probe deterministically without a network."""
    return None


class InstalledEmbedderModel(BaseModel):
    name: str = Field(
        description="Installed model ref as Ollama reports it (e.g. 'nomic-embed-text:latest')."
    )
    dim: int = Field(description="The model's output embedding dimension (curated catalog).")
    compatible: bool = Field(description="Whether dim matches the platform's required dim (768).")
    active: bool = Field(description="Whether this is the currently-configured embedding model.")


class EmbeddingModelsResponse(BaseModel):
    ollama_reachable: bool = Field(description="Whether Ollama's /api/tags responded.")
    active_model: str = Field(
        description="The configured embedding model (API_SERVER_EMBEDDING_MODEL)."
    )
    required_dim: int = Field(description="Embedding dimension the pgvector schema requires (768).")
    installed: list[InstalledEmbedderModel] = Field(
        default_factory=list,
        description="Installed models the catalog recognises as embedders (chat models excluded).",
    )
    recommended: list[str] = Field(
        default_factory=list,
        description="Catalog embedders that are 768-compatible — safe to pull and select.",
    )


@admin_router.get("/available-models", response_model=EmbeddingModelsResponse)
async def available_models(
    _: AuthPrincipal = Depends(require_system_admin),
    client: httpx.AsyncClient | None = Depends(get_ollama_probe_client),
) -> EmbeddingModelsResponse:
    """Discover usable embedders from the local Ollama (System Admin only).

    Crosses Ollama's installed models with the curated catalog: chat models are
    dropped, known embedders are flagged 768-compatible-or-not, the active model
    is marked, and the recommended (pullable) models are always returned. An
    unreachable Ollama yields ``ollama_reachable=false`` with an empty installed
    list — the panel still shows the active + recommended models."""
    discovery = await discover_embedding_models(client=client)
    return EmbeddingModelsResponse(
        ollama_reachable=discovery.ollama_reachable,
        active_model=discovery.active_model,
        required_dim=discovery.required_dim,
        installed=[
            InstalledEmbedderModel(name=e.name, dim=e.dim, compatible=e.compatible, active=e.active)
            for e in discovery.installed
        ],
        recommended=discovery.recommended,
    )
