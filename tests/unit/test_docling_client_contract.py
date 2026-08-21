"""G-01 (auditoría proyecto 2026-07-17): el cliente docling habla con rutas
que EXISTEN en docling-serve 1.20.x.

El cliente llamaba a ``POST /v1/convert`` — ruta retirada (404 en el servicio
vivo, que solo expone ``/v1/convert/{source,file}`` y ``/v1/chunk/*``): TODO
upload de KB moría en ``failed``. El hot-fix del 2026-06-25 nunca se comiteó
y un rebuild lo borró. Contrato nuevo: ``POST /v1/chunk/hybrid/file``
(chunking server-side listo para embedding), respuesta ``ChunkDocumentResponse``
(``{chunks: [{filename, chunk_index, text, headings?, page_numbers?, ...}]}``).
El bbox degrada a página-solo (el endpoint de chunking no devuelve
coordenadas): ``{"page": N, "x": 0, "y": 0, "w": 0, "h": 0}`` — la agrupación
por página y el scroll del visor de citas siguen funcionando; el overlay
queda de tamaño cero.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from api_server.ingestion.docling import DoclingParseError, HttpDoclingClient

pytestmark = pytest.mark.unit


def _chunk_response() -> dict[str, Any]:
    return {
        "chunks": [
            {
                "filename": "manual.pdf",
                "chunk_index": 0,
                "text": "CodeIgniter 4 requiere la extensión intl.",
                "num_tokens": 12,
                "headings": ["Instalación"],
                "captions": None,
                "doc_items": ["#/texts/0"],
                "page_numbers": [3],
                "metadata": None,
            },
            {
                "filename": "manual.pdf",
                "chunk_index": 1,
                "text": "Los tests corren con vendor/bin/phpunit.",
                "doc_items": ["#/texts/1"],
                "page_numbers": None,
            },
        ],
        "documents": [],
        "processing_time": 0.42,
    }


def _client_with(handler: Any) -> HttpDoclingClient:
    return HttpDoclingClient(
        base_url="http://docling-serve:5001",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.mark.asyncio
async def test_convert_posts_multipart_to_hybrid_chunk_endpoint() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["content_type"] = request.headers.get("content-type", "")
        seen["body"] = request.read()
        return httpx.Response(200, json=_chunk_response())

    client = _client_with(handler)
    chunks = await client.convert(
        filename="manual.pdf", content_type="application/pdf", data=b"%PDF-1.4 fake"
    )

    assert seen["path"] == "/v1/chunk/hybrid/file"
    assert seen["content_type"].startswith("multipart/form-data")
    # El campo multipart se llama `files` (contrato Body_Chunk_files_…).
    assert b'name="files"' in seen["body"]
    assert b"manual.pdf" in seen["body"]
    assert len(chunks) == 2


@pytest.mark.asyncio
async def test_chunk_items_map_onto_docling_chunks() -> None:
    client = _client_with(lambda _req: httpx.Response(200, json=_chunk_response()))
    chunks = await client.convert(filename="manual.pdf", content_type="application/pdf", data=b"x")

    first, second = chunks
    assert first.ordinal == 0
    assert first.content == "CodeIgniter 4 requiere la extensión intl."
    # Página del primer page_number, coords a cero (el chunking no las trae).
    assert first.bbox == {"page": 3, "x": 0.0, "y": 0.0, "w": 0.0, "h": 0.0}
    assert first.metadata.get("headings") == ["Instalación"]
    # Sin page_numbers → sin bbox (fuente no paginada).
    assert second.ordinal == 1
    assert second.bbox is None


@pytest.mark.asyncio
async def test_http_error_becomes_docling_parse_error() -> None:
    client = _client_with(lambda _req: httpx.Response(500, text="boom"))
    with pytest.raises(DoclingParseError):
        await client.convert(filename="f.pdf", content_type="application/pdf", data=b"x")


@pytest.mark.asyncio
async def test_route_constant_matches_pinned_openapi_snapshot() -> None:
    """Contract-test: la ruta que usa el cliente debe existir en el snapshot
    del openapi de la imagen pineada (docling-serve 1.20.x). Si un bump del
    servicio retira la ruta, actualizar snapshot y cliente JUNTOS."""
    from api_server.ingestion.docling import DOCLING_CHUNK_ROUTE

    snapshot = json.loads(
        __import__("pathlib")
        .Path(__file__)
        .parent.joinpath("fixtures", "docling_serve_openapi_paths.json")
        .read_text(encoding="utf-8")
    )
    assert DOCLING_CHUNK_ROUTE in snapshot["paths"], (
        f"la ruta {DOCLING_CHUNK_ROUTE} no existe en el openapi pineado de docling-serve"
    )
