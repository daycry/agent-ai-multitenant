"""Integration test for `document_convert` (Plan 04 task_04_22).

The tool that was a 501 placeholder in Plan 02 now calls docling-mcp.
We don't need docling-mcp running — :class:`StaticDoclingMCPClient`
returns canned chunks so the test asserts:

  - the tool routes filename + content_type + bytes to the client
    untouched,
  - the chunk list comes back as :class:`ConvertResult`,
  - the `page_count` is inferred from chunk bboxes,
  - errors from the MCP client surface as `DoclingParseError`.
"""

from __future__ import annotations

import pytest
from api_server.ingestion import (
    ConvertResult,
    DoclingChunk,
    DoclingParseError,
    document_convert,
)
from api_server.ingestion.docling_mcp import StaticDoclingMCPClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_document_convert_returns_chunks_with_inferred_page_count() -> None:
    client = StaticDoclingMCPClient(
        chunks=[
            DoclingChunk(
                ordinal=0,
                content="Introduction.",
                bbox={"page": 0, "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.05},
            ),
            DoclingChunk(
                ordinal=1,
                content="Chapter 1.",
                bbox={"page": 2, "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.05},
            ),
        ]
    )
    result = await document_convert(
        filename="manual.pdf",
        content_type="application/pdf",
        data=b"%PDF-1.4 dummy",
        client=client,
    )

    assert isinstance(result, ConvertResult)
    assert result.filename == "manual.pdf"
    assert result.content_type == "application/pdf"
    assert len(result.chunks) == 2
    # max(page) + 1 with pages {0, 2} → 3.
    assert result.page_count == 3


@pytest.mark.asyncio
async def test_document_convert_routes_arguments_to_client_verbatim() -> None:
    """The agent runtime hands the tool whatever mime type the file
    carries; the tool must NOT rewrite it (docling-mcp picks the
    backend based on the content type)."""
    client = StaticDoclingMCPClient(chunks=[])
    await document_convert(
        filename="talk.wav",
        content_type="audio/wav",
        data=b"RIFF fake audio",
        client=client,
    )
    assert client.calls == [
        {"filename": "talk.wav", "content_type": "audio/wav", "size": len(b"RIFF fake audio")}
    ]


@pytest.mark.asyncio
async def test_document_convert_unpaginated_source_returns_zero_pages() -> None:
    """HTML / Markdown / audio chunks have no bbox; page_count must
    stay at 0."""
    client = StaticDoclingMCPClient(
        chunks=[
            DoclingChunk(ordinal=0, content="Heading", bbox=None),
            DoclingChunk(ordinal=1, content="Paragraph", bbox=None),
        ]
    )
    result = await document_convert(
        filename="page.html",
        content_type="text/html",
        data=b"<html></html>",
        client=client,
    )
    assert result.page_count == 0
    assert len(result.chunks) == 2


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------
class _ExplodingMCPClient:
    async def convert(self, **kwargs):  # type: ignore[no-untyped-def]
        raise DoclingParseError("docling-mcp returned 500")

    async def aclose(self) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_mcp_errors_propagate_as_docling_parse_error() -> None:
    """Failures surface to the caller (the agent runtime) rather
    than being swallowed — the agent has to decide whether to retry
    or report the parse failure to the human."""
    with pytest.raises(DoclingParseError, match="500"):
        await document_convert(
            filename="x.pdf",
            content_type="application/pdf",
            data=b"x",
            client=_ExplodingMCPClient(),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Response parsing — the real HTTP client's flattening logic
# ---------------------------------------------------------------------------
def test_flatten_mcp_chunks_tolerates_legacy_shapes() -> None:
    from api_server.ingestion.docling_mcp import _flatten_mcp_chunks

    # Newer shape: top-level chunks under "result".
    body_new = {
        "result": {
            "chunks": [
                {"text": "A", "bbox": {"page": 0, "x": 0.1, "y": 0.2, "w": 0.7, "h": 0.05}},
                {"text": "B"},
            ]
        }
    }
    out = _flatten_mcp_chunks(body_new)
    assert [c.content for c in out] == ["A", "B"]
    assert out[0].bbox is not None
    assert out[1].bbox is None

    # Legacy shape: document.sections.
    body_legacy = {
        "document": {
            "sections": [
                {"content": "Foo", "heading": "Intro"},
                {"content": "  "},  # empty after strip — skipped
                {"text": "Bar"},
            ]
        }
    }
    out_legacy = _flatten_mcp_chunks(body_legacy)
    assert [c.content for c in out_legacy] == ["Foo", "Bar"]
    # Metadata bag carries the non-text fields.
    assert out_legacy[0].metadata.get("heading") == "Intro"


def test_flatten_mcp_chunks_returns_empty_on_unrecognised_shape() -> None:
    from api_server.ingestion.docling_mcp import _flatten_mcp_chunks

    assert _flatten_mcp_chunks({"some": "garbage"}) == []
    assert _flatten_mcp_chunks({"result": []}) == []
