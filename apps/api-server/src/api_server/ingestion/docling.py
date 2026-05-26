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
    """Real client. Hits ``POST /v1/convert`` on docling-serve and
    flattens the structured response into :class:`DoclingChunk`.

    Tests don't touch this — they pass a fake. The real-vs-fake
    contract is enforced by mypy via the :class:`DoclingClient`
    Protocol.
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
                f"{self._base_url}/v1/convert",
                files={"file": (filename, data, content_type)},
                # Ask docling to return its structured representation
                # *with* per-section bboxes for the citation viewer.
                data={"output_format": "json", "include_bbox": "true"},
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
    """Best-effort projection from docling-serve's response onto the
    `DoclingChunk` list.

    docling-serve ships several response shapes between minor
    versions. We walk a few well-known keys and tolerate the
    differences. Anything we can't recognise falls through silently —
    a document that yields zero chunks is still a valid (empty)
    indexed document; the operator sees ``status=indexed`` with
    ``page_count=0`` and re-uploads.
    """
    # Annotated as `list[Any]` (not `list[dict]`) so the per-item
    # `isinstance(raw, dict)` check stays *reachable* — docling-serve
    # has been known to mix strings into the section list in older
    # versions.
    chunks: list[Any] = []
    # Common shapes:
    #   {"chunks": [...]} (newer docling-serve)
    #   {"document": {"chunks": [...]}}
    #   {"document": {"sections": [...]}} (older variants)
    if isinstance(body.get("chunks"), list):
        chunks = body["chunks"]
    elif isinstance(body.get("document"), dict):
        doc = body["document"]
        if isinstance(doc.get("chunks"), list):
            chunks = doc["chunks"]
        elif isinstance(doc.get("sections"), list):
            chunks = doc["sections"]

    out: list[DoclingChunk] = []
    for ordinal, raw in enumerate(chunks):
        if not isinstance(raw, dict):
            continue
        text = raw.get("text") or raw.get("content")
        if not isinstance(text, str) or not text.strip():
            continue
        bbox_raw = raw.get("bbox") or raw.get("bounding_box")
        bbox = bbox_raw if isinstance(bbox_raw, dict) else None
        meta = {
            k: v for k, v in raw.items() if k not in {"text", "content", "bbox", "bounding_box"}
        }
        out.append(
            DoclingChunk(
                ordinal=ordinal,
                content=text.strip(),
                bbox=bbox,
                metadata=meta,
            )
        )
    return out


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
    "DoclingChunk",
    "DoclingClient",
    "DoclingParseError",
    "HttpDoclingClient",
    "StaticDoclingClient",
]
