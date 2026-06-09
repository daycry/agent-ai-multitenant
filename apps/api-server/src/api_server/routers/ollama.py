"""``/admin/ollama`` — native Ollama model management (ADR 0056, option U-B).

The governed, in-product alternative to bundling Open WebUI: a System-Admin
surface for the Ollama operations an operator actually needs, reusing the
platform's auth (``require_system_admin``) instead of a parallel, un-tenant-aware
UI. All calls go to the in-stack Ollama's native API at ``settings.ollama_url``.

  * ``GET    /admin/ollama/models``       list installed models (``/api/tags``)
  * ``POST   /admin/ollama/models/pull``  pull a model (``/api/pull``)
  * ``DELETE /admin/ollama/models``       delete a model (``/api/delete``)

The Ollama client is an injectable seam (:func:`get_ollama_client`) defaulting to
``None`` (each handler builds + closes its own httpx client with an appropriate
timeout); tests override it with a ``MockTransport`` so no network is hit. A
network error maps to 503 (Ollama unreachable), an Ollama 4xx/5xx to 502.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api_server.auth.deps import AuthPrincipal, require_system_admin
from api_server.config import get_settings

admin_router = APIRouter(prefix="/admin/ollama", tags=["admin", "ollama"])

# A model pull can take minutes (hundreds of MB to GB). Give it a generous
# timeout; the embedders the platform recommends are small, but a local LLM is
# not. The list/delete calls use a short timeout.
_PULL_TIMEOUT_SECONDS = 1800.0
_QUICK_TIMEOUT_SECONDS = 10.0


def get_ollama_client() -> httpx.AsyncClient | None:
    """Dependency seam for the Ollama management client.

    Returns ``None`` in production so each handler builds (and closes) its own
    httpx client with the right timeout. Tests override this with a
    ``MockTransport`` client to drive the API deterministically."""
    return None


class OllamaModel(BaseModel):
    name: str = Field(description="Model ref as Ollama reports it (e.g. nomic-embed-text:latest).")
    size_bytes: int | None = Field(default=None, description="On-disk size in bytes, if reported.")
    modified_at: str | None = Field(default=None, description="Last-modified timestamp, if any.")


class OllamaModelsResponse(BaseModel):
    ollama_reachable: bool = Field(description="Whether Ollama's /api/tags responded.")
    models: list[OllamaModel] = Field(default_factory=list, description="Installed models.")


class PullModelRequest(BaseModel):
    name: str = Field(
        min_length=1, max_length=200, description="Model to pull (e.g. 'nomic-embed-text')."
    )


class DeleteModelRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Installed model to delete.")


class OllamaActionResponse(BaseModel):
    ok: bool = Field(description="Whether the action succeeded.")
    detail: str = Field(description="Human-readable result/status.")


def _base_url() -> str:
    return get_settings().ollama_url.rstrip("/")


async def _request(
    client: httpx.AsyncClient | None,
    method: str,
    path: str,
    *,
    timeout: float,
    json: dict[str, Any] | None = None,
) -> httpx.Response:
    """Call the Ollama API, owning a client when none is injected.

    Translates a transport error into a 503 (Ollama unreachable). The caller
    inspects the returned response's status for Ollama-side 4xx/5xx."""
    owns = client is None
    cli = client or httpx.AsyncClient(timeout=timeout)
    try:
        return await cli.request(method, f"{_base_url()}{path}", json=json)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Ollama unreachable: {exc}",
        ) from exc
    finally:
        if owns:
            await cli.aclose()


@admin_router.get("/models", response_model=OllamaModelsResponse)
async def list_models(
    _: AuthPrincipal = Depends(require_system_admin),
    client: httpx.AsyncClient | None = Depends(get_ollama_client),
) -> OllamaModelsResponse:
    """List installed Ollama models (System Admin only).

    Degrades gracefully: an unreachable Ollama yields ``ollama_reachable=false``
    with an empty list rather than a 5xx, so the panel can render a clear state."""
    owns = client is None
    cli = client or httpx.AsyncClient(timeout=_QUICK_TIMEOUT_SECONDS)
    try:
        response = await cli.get(f"{_base_url()}/api/tags")
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        return OllamaModelsResponse(ollama_reachable=False, models=[])
    finally:
        if owns:
            await cli.aclose()

    raw = body.get("models")
    models: list[OllamaModel] = []
    if isinstance(raw, list):
        for m in raw:
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                size = m.get("size")
                modified = m.get("modified_at")
                models.append(
                    OllamaModel(
                        name=m["name"],
                        size_bytes=size if isinstance(size, int) else None,
                        modified_at=modified if isinstance(modified, str) else None,
                    )
                )
    return OllamaModelsResponse(ollama_reachable=True, models=models)


@admin_router.post("/models/pull", response_model=OllamaActionResponse)
async def pull_model(
    payload: PullModelRequest,
    _: AuthPrincipal = Depends(require_system_admin),
    client: httpx.AsyncClient | None = Depends(get_ollama_client),
) -> OllamaActionResponse:
    """Pull a model into the in-stack Ollama (System Admin only).

    Blocking (``stream: false``) with a generous timeout — a large model can take
    minutes. An Ollama-side failure (unknown model, etc.) maps to 502."""
    response = await _request(
        client,
        "POST",
        "/api/pull",
        timeout=_PULL_TIMEOUT_SECONDS,
        json={"model": payload.name, "stream": False},
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama pull failed ({response.status_code}): {response.text[:300]}",
        )
    return OllamaActionResponse(ok=True, detail=f"pulled {payload.name}")


@admin_router.delete("/models", response_model=OllamaActionResponse)
async def delete_model(
    payload: DeleteModelRequest,
    _: AuthPrincipal = Depends(require_system_admin),
    client: httpx.AsyncClient | None = Depends(get_ollama_client),
) -> OllamaActionResponse:
    """Delete an installed model (System Admin only).

    A missing model maps to 404; any other Ollama-side failure to 502."""
    response = await _request(
        client,
        "DELETE",
        "/api/delete",
        timeout=_QUICK_TIMEOUT_SECONDS,
        json={"model": payload.name},
    )
    if response.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"model not found: {payload.name}",
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Ollama delete failed ({response.status_code}): {response.text[:300]}",
        )
    return OllamaActionResponse(ok=True, detail=f"deleted {payload.name}")
