"""Docling client (Plan 04 task_04_11).

Wraps the docling-serve HTTP API behind a small Protocol so tests
inject a fake and production uses the real service. The contract is
intentionally narrow: feed bytes + filename, get structured chunks
back. Page numbers and bounding boxes ride on each chunk so the
citation viewer (Plan 04 task_04_25) can scroll to them.

We don't model the full docling-serve response — its `DocumentJson`
schema is rich (sections, captions, tables, formulas) and evolves
between minor versions. For now we extract chunks + their structural
breadcrumbs and treat the rest as metadata stashed on each chunk.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

logger = structlog.get_logger(__name__)

# G-01 (auditoría proyecto 2026-07-17): la ruta REAL de docling-serve 1.20.x.
# El cliente llamaba a `/v1/convert` (retirada → 404); el servicio expone
# `/v1/convert/{source,file}` (conversión cruda) y `/v1/chunk/hybrid/file`
# (conversión + chunking listo-para-embedding en una llamada). Usamos el
# chunker híbrido: nos da directamente los chunks que el pipeline indexa, sin
# re-implementar el troceo. El contract-test cruza esta constante con el
# snapshot del openapi pineado (tests/unit/fixtures/), así un bump que retire
# la ruta rompe en CI en vez de en producción (la lección del hot-fix perdido).
DOCLING_CHUNK_ROUTE = "/v1/chunk/hybrid/file"


class DoclingParseError(RuntimeError):
    """Raised when docling-serve fails to parse a document. The
    pipeline catches it and flips the document to ``failed``."""


@dataclass(frozen=True)
class DoclingChunk:
    """One structural chunk produced by docling-serve.

    `ordinal` is 0-indexed (preserves docling's order). `bbox` is the
    same shape Plan 04 task_04_07 documented:
    ``{"page": int, "x": float, "y": float, "w": float, "h": float}``
    in normalised page coords. NULL for unpaginated sources.
    """

    ordinal: int
    content: str
    bbox: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class DoclingClient(Protocol):
    """Convert bytes → structured chunks.

    Implementations must accept any MIME type docling-serve supports
    (PDF, DOCX, HTML, Markdown, audio/wav, audio/mp3, …) — routing
    happens server-side.
    """

    async def convert(
        self,
        *,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> list[DoclingChunk]: ...

    async def aclose(self) -> None: ...


# ---------------------------------------------------------------------------
# Real implementation
# ---------------------------------------------------------------------------
class HttpDoclingClient:
    """Real client. Hits ``POST /v1/chunk/hybrid/file`` on docling-serve
    (convert + hybrid chunking in one call) and flattens the
    ``ChunkDocumentResponse`` into :class:`DoclingChunk`.

    Tests don't touch this — they pass a fake. The real-vs-fake
    contract is enforced by mypy via the :class:`DoclingClient`
    Protocol; the route is contract-tested against the pinned openapi
    snapshot (G-01).
    """

    def __init__(
        self,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def convert(
        self,
        *,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> list[DoclingChunk]:
        try:
            response = await self._client.post(
                f"{self._base_url}{DOCLING_CHUNK_ROUTE}",
                files={"files": (filename, data, content_type)},
                # JSON body (not the zip variant); merge peer chunks so short
                # sibling paragraphs land together — the default the embedder
                # wants. Everything else stays on docling-serve's defaults.
                data={"target_type": "inbody", "chunking_merge_peers": "true"},
            )
        except httpx.HTTPError as exc:
            raise DoclingParseError(f"docling-serve request failed: {exc}") from exc
        if response.status_code >= 400:
            raise DoclingParseError(f"docling-serve {response.status_code}: {response.text[:500]}")
        body = response.json()
        return _flatten_chunks(body)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _flatten_chunks(body: dict[str, Any]) -> list[DoclingChunk]:
    """Project a ``ChunkDocumentResponse`` onto the `DoclingChunk` list.

    Shape (docling-serve 1.20.x ``/v1/chunk/hybrid/file``):
    ``{"chunks": [{"chunk_index", "text"|"raw_text", "headings"?,
    "captions"?, "page_numbers"?, "doc_items"?, "metadata"?}, ...]}``.
    The chunking endpoint carries NO coordinates, so the bbox degrades to
    page-only (``x/y/w/h = 0``) from the first ``page_numbers`` entry — the
    citation viewer still groups by page and scrolls; the overlay is
    zero-sized. Unpaginated sources (no ``page_numbers``) yield ``bbox=None``.

    ``list[Any]`` (not ``list[dict]``) keeps the per-item ``isinstance`` guard
    reachable — a flaky gateway can mix non-dicts into the array. Zero
    recognised chunks is still a valid (empty) indexed document.
    """
    raw_chunks = body.get("chunks")
    chunks: list[Any] = raw_chunks if isinstance(raw_chunks, list) else []

    out: list[DoclingChunk] = []
    ordinal = 0
    for raw in chunks:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text") or raw.get("raw_text")
        if not isinstance(text, str) or not text.strip():
            continue
        bbox = _page_bbox(raw.get("page_numbers"))
        # Everything that isn't the chunk text becomes citation metadata
        # (headings/captions/doc_items/num_tokens/…), minus the raw duplicates.
        meta = {k: v for k, v in raw.items() if k not in {"text", "raw_text", "chunk_index"}}
        out.append(
            DoclingChunk(
                ordinal=ordinal,
                content=text.strip(),
                bbox=bbox,
                metadata=meta,
            )
        )
        ordinal += 1
    return out


def _page_bbox(page_numbers: Any) -> dict[str, Any] | None:
    """A page-only bbox from a chunk's ``page_numbers`` (coords 0 — the
    chunking endpoint doesn't return geometry). ``None`` when unpaginated."""
    if isinstance(page_numbers, list) and page_numbers and isinstance(page_numbers[0], int):
        return {"page": page_numbers[0], "x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    return None


# ---------------------------------------------------------------------------
# Test fake
# ---------------------------------------------------------------------------
class StaticDoclingClient:
    """Deterministic fake that returns a fixed chunk list.

    Used by the ingestion integration tests so they don't need
    docling-serve up. Implements :class:`DoclingClient` structurally.
    """

    def __init__(self, chunks: Sequence[DoclingChunk]) -> None:
        self._chunks = list(chunks)
        self.calls: list[dict[str, Any]] = []

    async def convert(
        self,
        *,
        filename: str,
        content_type: str,
        data: bytes,
    ) -> list[DoclingChunk]:
        self.calls.append({"filename": filename, "content_type": content_type, "size": len(data)})
        return list(self._chunks)

    async def aclose(self) -> None:  # pragma: no cover
        pass


__all__ = [
    "DOCLING_CHUNK_ROUTE",
    "DoclingChunk",
    "DoclingClient",
    "DoclingParseError",
    "HttpDoclingClient",
    "StaticDoclingClient",
]
