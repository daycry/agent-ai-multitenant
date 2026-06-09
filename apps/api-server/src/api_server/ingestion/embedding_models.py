"""Curated catalog of Ollama embedding models + discovery against ``/api/tags``
(ADR 0056, option S-C).

Why a curated allowlist instead of "use whatever Ollama returns":

  * The chunk/memory vector column is fixed at ``CHUNK_EMBEDDING_DIM`` (768) and
    a KB's ``embedding_model_id`` is immutable once it has chunks (mass re-embed
    is deferred to Plan 12). So ONLY 768-dim embedders are usable — a 1024-dim
    (``mxbai``/``bge``) or 384-dim (``all-minilm``) model would make the embedder
    raise ``EmbeddingError`` or break the schema.
  * Ollama's native ``GET /api/tags`` lists installed models but does NOT label
    which are embedders vs chat models. We intersect that list with this curated
    map (name -> output dimension) so the admin panel can surface which INSTALLED
    models are valid embedders, which are 768-compatible, and which to pull.

The selected model is FIXED via ``settings.embedding_model`` (env
``API_SERVER_EMBEDDING_MODEL``) — this module is read-only discovery, not a
live swap (changing the model with existing KBs is the Plan 12 re-embed job).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import structlog

from api_server.config import Settings, get_settings
from api_server.db.knowledge import CHUNK_EMBEDDING_DIM

logger = structlog.get_logger(__name__)

# Curated allowlist of known Ollama embedding models -> output dimension, keyed
# by the canonical ``ollama pull`` reference. Where a family ships several sizes
# with different dims, the size-specific tag is listed explicitly and wins over
# the family default. Only entries whose dim == CHUNK_EMBEDDING_DIM are usable
# by this platform today (see ``recommended_models``).
KNOWN_EMBEDDING_MODELS: dict[str, int] = {
    "nomic-embed-text": 768,
    "mxbai-embed-large": 1024,
    "bge-m3": 1024,
    "bge-large": 1024,
    "all-minilm": 384,
    "snowflake-arctic-embed": 1024,  # default tag (335m)
    "snowflake-arctic-embed:110m": 768,
    "snowflake-arctic-embed:137m": 768,
    "snowflake-arctic-embed2": 1024,
    "granite-embedding": 384,  # default tag (30m)
    "granite-embedding:278m": 768,
    "paraphrase-multilingual": 768,
}


def _normalise(name: str) -> str:
    """Strip a trailing ``:latest`` so ``/api/tags`` names match catalog keys.

    Ollama reports installed models as ``nomic-embed-text:latest``; the catalog
    keys the family without that implicit tag."""
    return name[:-7] if name.endswith(":latest") else name


def known_dim(name: str) -> int | None:
    """The catalog dimension for an Ollama model ref, or ``None`` when the model
    is not a known embedder.

    Matches the full ref first (so a size-specific tag like
    ``snowflake-arctic-embed:110m`` wins over the family default), then falls
    back to the family base name (the part before ``:``)."""
    ref = _normalise(name)
    if ref in KNOWN_EMBEDDING_MODELS:
        return KNOWN_EMBEDDING_MODELS[ref]
    base = ref.split(":", 1)[0]
    return KNOWN_EMBEDDING_MODELS.get(base)


def is_compatible(name: str) -> bool:
    """Whether a known embedder emits ``CHUNK_EMBEDDING_DIM`` (768) vectors —
    the only kind this platform can store without a schema change."""
    return known_dim(name) == CHUNK_EMBEDDING_DIM


def recommended_models() -> list[str]:
    """The catalog's 768-compatible embedders (sorted) — what an operator can
    safely ``ollama pull`` and select as ``API_SERVER_EMBEDDING_MODEL``."""
    return sorted(n for n, d in KNOWN_EMBEDDING_MODELS.items() if d == CHUNK_EMBEDDING_DIM)


@dataclass(frozen=True)
class InstalledEmbedder:
    """An installed Ollama model that the curated catalog recognises as an
    embedder (chat models are filtered out before this is built)."""

    name: str
    dim: int
    compatible: bool
    active: bool


@dataclass(frozen=True)
class EmbeddingModelDiscovery:
    """What ``GET /admin/embeddings/available-models`` returns: the installed
    embedders, the active model, the required dim, and what to pull."""

    ollama_reachable: bool
    active_model: str
    required_dim: int
    installed: list[InstalledEmbedder] = field(default_factory=list)
    recommended: list[str] = field(default_factory=list)


async def fetch_installed_models(
    *,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    timeout_seconds: float = 10.0,
) -> list[str] | None:
    """Return the model names Ollama has installed (native ``GET /api/tags``),
    or ``None`` when Ollama is unreachable.

    Never raises on a network/parse error — the discovery layer maps ``None`` to
    ``ollama_reachable=False`` so a dead Ollama degrades the panel, not the
    request. An injected ``client`` is NOT closed here (the caller owns it)."""
    cfg = settings or get_settings()
    url = (base_url or cfg.ollama_url).rstrip("/") + "/api/tags"
    owns_client = client is None
    cli = client or httpx.AsyncClient(timeout=timeout_seconds)
    try:
        response = await cli.get(url)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("embedding_models.tags_unreachable", url=url, error=str(exc))
        return None
    finally:
        if owns_client:
            await cli.aclose()
    models = body.get("models")
    if not isinstance(models, list):
        return []
    return [m["name"] for m in models if isinstance(m, dict) and isinstance(m.get("name"), str)]


async def discover_embedding_models(
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> EmbeddingModelDiscovery:
    """Cross the Ollama-installed models with the curated catalog.

    Filters the installed list to KNOWN embedders (a chat model like
    ``llama3.1`` is dropped — we can't store it as embeddings), flags which are
    768-compatible and which is the active model (``settings.embedding_model``),
    and always reports the recommended models to pull. When Ollama is
    unreachable the installed list is empty and ``ollama_reachable`` is False —
    the panel still shows the active + recommended models."""
    cfg = settings or get_settings()
    active = cfg.embedding_model
    installed_names = await fetch_installed_models(settings=cfg, client=client)
    reachable = installed_names is not None

    installed: list[InstalledEmbedder] = []
    for name in installed_names or []:
        dim = known_dim(name)
        if dim is None:
            continue  # not a known embedder (likely a chat model) — skip it
        installed.append(
            InstalledEmbedder(
                name=name,
                dim=dim,
                compatible=dim == CHUNK_EMBEDDING_DIM,
                active=_normalise(name) == active or name == active,
            )
        )

    return EmbeddingModelDiscovery(
        ollama_reachable=reachable,
        active_model=active,
        required_dim=CHUNK_EMBEDDING_DIM,
        installed=installed,
        recommended=recommended_models(),
    )


__all__ = [
    "KNOWN_EMBEDDING_MODELS",
    "EmbeddingModelDiscovery",
    "InstalledEmbedder",
    "discover_embedding_models",
    "fetch_installed_models",
    "is_compatible",
    "known_dim",
    "recommended_models",
]
