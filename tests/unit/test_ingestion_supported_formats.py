"""Formatos que acepta la ingesta (prod-13, task_prod13_04 / api-2).

La lista NO se inventa: se le pregunta a `docling-serve` al arrancar y se
cachea, con una lista fija documentada como respaldo si el servicio no
contesta. Estos tests fijan las tres mitades de esa decisión:

  1. la extracción del enum `InputFormat` del openapi del servicio,
  2. el respaldo cuando no hay respuesta (y que el respaldo NO sea más
     estricto que lo que el servicio diría),
  3. la regla de admisión, que en la duda ACEPTA — rechazar en la puerta una
     subida que hoy funciona es una regresión peor que la que se viene a
     arreglar.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from api_server.ingestion.formats import (
    FALLBACK_INPUT_FORMATS,
    SupportedFormats,
    cached_supported_formats,
    fetch_input_formats,
    refresh_supported_formats,
    reset_supported_formats_cache,
)

pytestmark = pytest.mark.unit


def _openapi(formats: list[str]) -> dict[str, Any]:
    """Un openapi de docling-serve reducido a lo que este módulo mira."""
    return {
        "openapi": "3.1.0",
        "components": {
            "schemas": {
                "InputFormat": {
                    "type": "string",
                    "enum": formats,
                    "title": "InputFormat",
                },
                "ChunkDocumentResponse": {"type": "object"},
            }
        },
    }


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _clean_cache() -> Any:
    reset_supported_formats_cache()
    yield
    reset_supported_formats_cache()


# ---------------------------------------------------------------------------
# 1. Preguntarle al servicio
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_input_formats_come_from_the_service_openapi() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json=_openapi(["pdf", "docx", "md"]))

    formats = await fetch_input_formats(
        base_url="http://docling-serve:5001", client=_client(handler)
    )

    assert seen["path"] == "/openapi.json"
    assert formats == frozenset({"pdf", "docx", "md"})


@pytest.mark.asyncio
async def test_a_service_that_does_not_answer_yields_no_formats() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    assert (
        await fetch_input_formats(base_url="http://docling-serve:5001", client=_client(handler))
        is None
    )


@pytest.mark.asyncio
async def test_an_openapi_without_the_enum_yields_no_formats() -> None:
    """Un bump de docling-serve que renombre el schema no puede inventarse una
    lista vacía: sin enum reconocible, `None` → respaldo."""
    body = {"components": {"schemas": {"Whatever": {"type": "object"}}}}
    formats = await fetch_input_formats(
        base_url="http://x", client=_client(lambda _r: httpx.Response(200, json=body))
    )
    assert formats is None


@pytest.mark.asyncio
async def test_a_non_json_answer_yields_no_formats() -> None:
    formats = await fetch_input_formats(
        base_url="http://x", client=_client(lambda _r: httpx.Response(200, text="<html>nope"))
    )
    assert formats is None


# ---------------------------------------------------------------------------
# 2. Caché y respaldo
# ---------------------------------------------------------------------------
def test_without_a_refresh_the_cache_serves_the_documented_fallback() -> None:
    formats = cached_supported_formats()
    assert formats.source == "fallback"
    assert formats.input_formats == FALLBACK_INPUT_FORMATS


@pytest.mark.asyncio
async def test_a_successful_refresh_replaces_the_fallback_in_the_cache() -> None:
    await refresh_supported_formats(
        base_url="http://x",
        client=_client(lambda _r: httpx.Response(200, json=_openapi(["pdf", "audio"]))),
    )
    cached = cached_supported_formats()
    assert cached.source == "docling-serve"
    assert cached.input_formats == frozenset({"pdf", "audio"})
    # Y la caché manda: un formato que el servicio NO declara deja de admitirse.
    assert cached.rejection_reason(filename="a.docx", content_type=None) is not None


@pytest.mark.asyncio
async def test_a_failed_refresh_leaves_the_fallback_and_never_raises() -> None:
    """El refresco es best-effort: si reventara, el api-server no arrancaría."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("docling-serve is down")

    result = await refresh_supported_formats(base_url="http://x", client=_client(handler))

    assert result.source == "fallback"
    assert cached_supported_formats().source == "fallback"
    # Y con el servicio caído se sigue pudiendo subir un PDF.
    assert result.rejection_reason(filename="informe.pdf", content_type="application/pdf") is None


def test_the_fallback_is_not_stricter_than_what_the_service_would_say() -> None:
    """El respaldo es deliberadamente el listado ANCHO conocido de docling.

    Si fuese más corto, un docling-serve caído durante el arranque convertiría
    una degradación en un rechazo de subidas legítimas — el modo de fallo que
    esta tarea llevaba dos meses evitando.
    """
    assert {"pdf", "docx", "pptx", "xlsx", "html", "md", "csv", "image", "audio"} <= set(
        FALLBACK_INPUT_FORMATS
    )


# ---------------------------------------------------------------------------
# 3. La regla de admisión
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("informe.pdf", "application/pdf"),
        ("informe.PDF", "application/pdf"),  # extensión en mayúsculas
        ("notas.md", "text/markdown"),
        ("notas.txt", "text/plain"),  # texto plano = markdown para docling
        ("hoja.xlsx", "application/octet-stream"),  # el navegador no supo el tipo
        ("captura.png", "image/png"),
        ("audio.wav", "audio/wav"),
        ("sin-extension", "application/pdf"),  # sin extensión, pero el tipo canta
        ("pagina.html", None),
    ],
)
def test_known_uploads_are_accepted(filename: str, content_type: str | None) -> None:
    formats = cached_supported_formats()
    assert formats.rejection_reason(filename=filename, content_type=content_type) is None


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("binario.exe", "application/octet-stream"),
        ("paquete.zip", "application/zip"),
        ("video.mp4", "video/mp4"),
        ("sin-extension", "application/octet-stream"),
        ("sin-extension", None),
    ],
)
def test_unparseable_uploads_are_rejected_with_a_reason(
    filename: str, content_type: str | None
) -> None:
    reason = cached_supported_formats().rejection_reason(
        filename=filename, content_type=content_type
    )
    assert reason is not None
    # El mensaje tiene que servirle a quien sube el fichero: dice qué se acepta.
    assert "pdf" in reason.lower()


def test_an_unknown_extension_with_a_known_mime_type_is_accepted() -> None:
    """En la duda, ACEPTA. Una extensión rara con un tipo que docling parsea es
    exactamente el caso que un allowlist ingenuo rompe."""
    formats = cached_supported_formats()
    assert (
        formats.rejection_reason(filename="contrato.fichero", content_type="application/pdf")
        is None
    )


def test_the_supported_formats_value_lists_what_it_accepts() -> None:
    formats = SupportedFormats.from_input_formats(frozenset({"pdf"}), source="fallback")
    assert ".pdf" in formats.extensions
    assert "application/pdf" in formats.mime_types
    assert ".docx" not in formats.extensions


# ---------------------------------------------------------------------------
# 4. El cableado: alguien tiene que preguntar
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_lifespan_primes_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """El patrón «mecanismo entregado, cero llamantes» aplicado a esto sería una
    caché que nunca se puebla: el respaldo funcionaría, nadie notaría nada, y la
    lista envejecería igual que si estuviera escrita a mano — que es el defecto
    que esta tarea vino a cerrar. Este test se pone rojo si el arranque deja de
    preguntar.
    """
    from api_server import main

    asked: list[str] = []

    async def _fake_refresh(*, base_url: str, **_: Any) -> SupportedFormats:
        asked.append(base_url)
        return cached_supported_formats()

    monkeypatch.setattr("api_server.ingestion.formats.refresh_supported_formats", _fake_refresh)
    await main._prime_supported_formats()

    assert asked, "el arranque no le preguntó sus formatos a docling-serve"
    assert asked[0].startswith("http")


def test_the_startup_hook_is_wired_into_the_lifespan() -> None:
    """Y que ese hook lo llame el lifespan de verdad, no sólo exista."""
    import inspect

    from api_server import main

    assert "_prime_supported_formats" in inspect.getsource(main._lifespan)


def test_every_fallback_format_maps_to_at_least_one_extension() -> None:
    """El agujero silencioso de este módulo: un formato en el respaldo que no
    esté en los diccionarios de extensiones/MIME no admite NADA, así que la lista
    dice que lo soporta y la puerta lo rechaza.

    Lo encontró la verificación contra el `docling-serve` vivo del stack el
    2026-08-12: su enum traía `vtt`, `latex` y `xml_xbrl`, que no estaban
    mapeados — o sea que un `.vtt` o un `.tex` se habrían rechazado en la puerta
    pese a que Docling los parsea. Este test es para que no vuelva a pasar
    cuando alguien amplíe el respaldo.
    """
    from api_server.ingestion.formats import _EXTENSIONS, _MIME_TYPES, _WILDCARD_TYPES

    unmapped = sorted(
        fmt
        for fmt in FALLBACK_INPUT_FORMATS
        if not _EXTENSIONS.get(fmt) and not _MIME_TYPES.get(fmt) and fmt not in _WILDCARD_TYPES
    )
    assert not unmapped, f"formatos del respaldo sin extensión ni MIME: {unmapped}"


@pytest.mark.parametrize("filename", ["subtitulos.vtt", "articulo.tex", "informe.xbrl"])
def test_the_formats_the_live_service_declares_are_accepted(filename: str) -> None:
    """Los tres que la verificación en vivo destapó."""
    assert cached_supported_formats().rejection_reason(filename=filename, content_type=None) is None
