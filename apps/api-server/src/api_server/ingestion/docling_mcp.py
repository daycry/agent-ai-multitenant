"""docling-mcp client + `document_convert` (Plan 04 task_04_22).

`docling-mcp` is the MCP-protocol surface of Docling. The agent
runtime calls it through the platform's MCP wrapper; humans can also
invoke it from the chat to drop a file into the conversation context
without persisting it to a KB.

The Plan 02 placeholder `document_convert` returned 501. This module
gives it a real body: a thin `DoclingMCPClient` Protocol with a
`HttpDoclingMCPClient` (talks the MCP HTTP transport surface — same
endpoint shape as docling-serve's `/v1/convert` for our purposes) and
a `StaticDoclingMCPClient` for tests.

`document_convert` is intentionally pure — it does NOT touch the DB.
The companion `promote_to_kb` (task_04_23) takes the in-flight result
and turns it into a Document + chunks if the human decides to keep
it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
import structlog

from api_server.config import Settings, get_settings
from api_server.ingestion.docling import DoclingChunk, DoclingParseError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ConvertResult:
    """In-flight result of a `document_convert` call.

    The chunks live in memory only. The agent can read them, render
    them, paste them into the chat, or push them into a KB via
    :func:`api_server.ingestion.promote.promote_to_kb`.

    ``page_count`` is the inferred max page from chunk bboxes — 0
    for unpaginated sources (HTML/Markdown/audio).
    """

    filename: str
    content_type: str
    chunks: list[DoclingChunk]
    page_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class DoclingMCPClient(Protocol):
    """Convert bytes → structured chunks via docling-mcp."""

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
class HttpDoclingMCPClient:
    """Real client. Hits the MCP HTTP transport's `tools/call` endpoint
    for the `convert` tool.

    For Plan 04 we use a simple POST-with-multipart shape — the same
    docling-serve exposes — because docling-mcp wraps the same parser
    underneath. As MCP transport matures we'll switch to the proper
    JSON-RPC envelope; the Protocol contract stays the same.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 120.0,
        settings: Settings | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self._base_url = (base_url or cfg.docling_mcp_url).rstrip("/")
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
                f"{self._base_url}/tools/call/convert",
                files={"file": (filename, data, content_type)},
                data={"output_format": "json", "include_bbox": "true"},
            )
        except httpx.HTTPError as exc:
            raise DoclingParseError(f"docling-mcp request failed: {exc}") from exc
        if response.status_code >= 400:
            raise DoclingParseError(f"docling-mcp {response.status_code}: {response.text[:500]}")
        body = response.json()
        return _flatten_mcp_chunks(body)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _flatten_mcp_chunks(body: dict[str, Any]) -> list[DoclingChunk]:
    """Project the MCP response onto :class:`DoclingChunk`. Shape:

        {"result": {"chunks": [{"text": "...", "bbox": {...}, ...}]}}

    We tolerate the same legacy shapes the docling-serve client
    accepts so the two stay swappable during the MCP transport
    transition."""
    payload: Any = body.get("result") or body
    chunks: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("chunks"), list):
            chunks = payload["chunks"]
        elif isinstance(payload.get("document"), dict):
            doc = payload["document"]
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
class StaticDoclingMCPClient:
    """Deterministic fake. Returns a fixed list of chunks and records
    the calls so tests can assert routing."""

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


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------
async def document_convert(
    *,
    filename: str,
    content_type: str,
    data: bytes,
    client: DoclingMCPClient,
) -> ConvertResult:
    """Convert a document in-flight (Plan 04 task_04_22).

    Does NOT touch the database. The caller can:
      - hand the result back to the agent / human as JSON,
      - pass the result to `promote_to_kb` to persist it.

    Failures bubble up as :class:`DoclingParseError`; the caller
    decides whether to surface them to the agent or retry.
    """
    chunks = await client.convert(filename=filename, content_type=content_type, data=data)
    pages = {
        c.bbox["page"]
        for c in chunks
        if isinstance(c.bbox, dict) and isinstance(c.bbox.get("page"), int)
    }
    return ConvertResult(
        filename=filename,
        content_type=content_type,
        chunks=chunks,
        page_count=max(pages) + 1 if pages else 0,
    )


__all__ = [
    "ConvertResult",
    "DoclingMCPClient",
    "HttpDoclingMCPClient",
    "StaticDoclingMCPClient",
    "document_convert",
]
