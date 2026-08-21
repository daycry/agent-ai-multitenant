"""Qué formatos acepta la ingesta — se los preguntamos a `docling-serve`.

Contexto (plan prod-13, `task_prod13_04` / hallazgo api-2)
---------------------------------------------------------
`POST /knowledge-bases/{kb_id}/documents` aceptaba cualquier cosa. Un `.zip` o
un `.exe` se subían enteros a MinIO, se creaba la fila en `documents` y el
usuario se enteraba **minutos después**, cuando el pipeline levantaba
`DoclingParseError` y el documento acababa en `failed`. No es un problema de
robustez —nada se cuelga— sino de UX: el rechazo llega tarde y después de
transferir hasta 50 MiB.

La lista NO se inventa, y ése era el bloqueo
--------------------------------------------
Durante dos meses esta tarea quedó abierta porque **docling no es una
dependencia Python de este repo**: el api-server habla con `docling-serve` por
HTTP (`ingestion/docling.py`), así que no hay ningún `InputFormat` importable
del que derivar la lista. Escribirla a mano tiene un modo de fallo silencioso y
caro: envejece cada vez que Docling añade un formato, y entonces rechaza en la
puerta subidas que funcionarían.

**Decisión tomada**: se le pregunta al propio servicio *al arrancar* y se
cachea, con una **lista fija documentada como respaldo** si no contesta.
`docling-serve` publica su OpenAPI, y ahí vive el enum `InputFormat` que su
propio validador usa. Preguntar en cada petición queda descartado: metería una
dependencia de red dentro de una validación de entrada.

Qué pasa si `docling-serve` está caído al arrancar
--------------------------------------------------
Se usa :data:`FALLBACK_INPUT_FORMATS`, que es **deliberadamente el listado ancho
conocido de Docling**. Si el respaldo fuese más corto que la verdad, una caída
del servicio durante el arranque se convertiría en rechazos de subidas
legítimas, que es justo el fallo que esta tarea evitaba. Con el respaldo ancho,
la degradación es sólo que un formato NUEVO (añadido por un Docling posterior a
esta lista) se rechazaría en la puerta hasta el siguiente arranque con el
servicio vivo. Un test fija que el respaldo cubre los formatos troncales.

La regla de admisión: en la duda, ACEPTA
----------------------------------------
Este allowlist existe para cortar temprano lo indiscutiblemente inparseable, no
para ser exhaustivo. Por eso basta con que la **extensión** o el **tipo MIME**
sean reconocibles; `application/octet-stream` se trata como «no sé» (es lo que
mandan muchos navegadores) y decide entonces la extensión. Un rechazo de más
aquí es una regresión funcional silenciosa; un rechazo de menos es exactamente
el comportamiento de hoy, que ya está acotado (el documento acaba en `failed`
con su motivo).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

__all__ = [
    "DOCLING_OPENAPI_ROUTE",
    "FALLBACK_INPUT_FORMATS",
    "SupportedFormats",
    "cached_supported_formats",
    "fetch_input_formats",
    "refresh_supported_formats",
    "reset_supported_formats_cache",
]

#: Dónde publica `docling-serve` su esquema (FastAPI, ruta por defecto). El
#: openapi lleva el enum `InputFormat` que su propio validador usa.
DOCLING_OPENAPI_ROUTE = "/openapi.json"

#: Plazo corto: esto corre en el arranque del api-server y no puede retrasarlo.
OPENAPI_TIMEOUT_S = 5.0

#: El respaldo. Nombres del enum `InputFormat` de Docling, **copiados del
#: servicio vivo** el 2026-08-12 (`GET /openapi.json` del `docling-serve` del
#: stack), no de memoria: ésa es la diferencia entre una lista con procedencia y
#: una inventada. Ancho a propósito — ver el docstring del módulo.
FALLBACK_INPUT_FORMATS: frozenset[str] = frozenset(
    {
        "pdf",
        "docx",
        "pptx",
        "xlsx",
        "html",
        "md",
        "csv",
        "asciidoc",
        "image",
        "audio",
        "vtt",
        "latex",
        "xml_uspto",
        "xml_jats",
        "xml_xbrl",
        "json_docling",
        "mets_gbs",
    }
)

#: Extensiones por formato. Generoso a propósito: `.txt` cuelga de `md` porque
#: el texto plano ES markdown válido y Docling lo enruta por ese backend —
#: dejarlo fuera rompería subidas que hoy funcionan (la suite de KBs sube
#: `.txt`).
_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "pdf": (".pdf",),
    "docx": (".docx", ".docm", ".dotx"),
    "pptx": (".pptx", ".pptm", ".potx", ".ppsx"),
    "xlsx": (".xlsx", ".xlsm"),
    "html": (".html", ".htm", ".xhtml"),
    "md": (".md", ".markdown", ".mdown", ".txt"),
    "csv": (".csv", ".tsv"),
    "asciidoc": (".adoc", ".asciidoc", ".asc"),
    "image": (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"),
    "audio": (".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus"),
    "vtt": (".vtt",),
    "latex": (".tex", ".latex"),
    "xml_uspto": (".xml",),
    "xml_jats": (".xml", ".nxml"),
    "xml_xbrl": (".xbrl", ".xml"),
    "json_docling": (".json",),
    "mets_gbs": (".tar.gz",),
}

#: Tipos MIME por formato. Para `image` y `audio` la familia entera vale
#: (`_WILDCARD_TYPES`): enumerar cada códec sería una lista peor mantenida que
#: la que se viene a evitar.
_MIME_TYPES: dict[str, tuple[str, ...]] = {
    "pdf": ("application/pdf",),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-word.document.macroenabled.12",
    ),
    "pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
    ),
    "xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    ),
    "html": ("text/html", "application/xhtml+xml"),
    "md": ("text/markdown", "text/x-markdown", "text/plain"),
    "csv": ("text/csv", "text/tab-separated-values"),
    "asciidoc": ("text/asciidoc", "text/x-asciidoc"),
    "image": (),
    "audio": (),
    "vtt": ("text/vtt",),
    "latex": ("application/x-tex", "text/x-tex", "application/x-latex"),
    "xml_uspto": ("application/xml", "text/xml"),
    "xml_jats": ("application/xml", "text/xml"),
    "xml_xbrl": ("application/xml", "text/xml"),
    "json_docling": ("application/json",),
    "mets_gbs": ("application/gzip",),
}

#: Formatos cuya familia MIME entera se admite.
_WILDCARD_TYPES: dict[str, str] = {"image": "image/", "audio": "audio/"}

#: El tipo que significa «el cliente no sabe qué es esto». No aporta
#: información, así que no puede ni admitir ni rechazar por sí solo.
_OPAQUE_MIME = "application/octet-stream"


@dataclass(frozen=True)
class SupportedFormats:
    """Lo que la ingesta admite, y de dónde salió.

    ``source`` es ``"docling-serve"`` cuando el servicio contestó y
    ``"fallback"`` cuando se está usando la lista fija. Aparece en los logs
    para que una tanda de rechazos raros se pueda explicar sin adivinar.
    """

    input_formats: frozenset[str]
    source: str
    extensions: frozenset[str]
    mime_types: frozenset[str]
    wildcard_types: frozenset[str]

    @classmethod
    def from_input_formats(cls, input_formats: frozenset[str], *, source: str) -> SupportedFormats:
        extensions = {ext for fmt in input_formats for ext in _EXTENSIONS.get(fmt, ())}
        mimes = {mime for fmt in input_formats for mime in _MIME_TYPES.get(fmt, ())}
        wildcards = {_WILDCARD_TYPES[fmt] for fmt in input_formats if fmt in _WILDCARD_TYPES}
        return cls(
            input_formats=input_formats,
            source=source,
            extensions=frozenset(extensions),
            mime_types=frozenset(mimes),
            wildcard_types=frozenset(wildcards),
        )

    # -- admisión ----------------------------------------------------------
    def rejection_reason(self, *, filename: str | None, content_type: str | None) -> str | None:
        """``None`` si la subida se admite; si no, el motivo para el usuario.

        Basta con que la extensión O el tipo MIME sean reconocibles. Ver la
        sección «en la duda, ACEPTA» del docstring del módulo.
        """
        extension = _extension_of(filename)
        if extension and extension in self.extensions:
            return None
        mime = _normalise_mime(content_type)
        if mime and self._accepts_mime(mime):
            return None
        what = f"'{extension}'" if extension else (f"'{mime}'" if mime else "sin extensión")
        return (
            f"formato no soportado por la ingesta ({what}). "
            f"Formatos admitidos: {self.human_readable_extensions()}"
        )

    def _accepts_mime(self, mime: str) -> bool:
        if mime in self.mime_types:
            return True
        return any(mime.startswith(prefix) for prefix in self.wildcard_types)

    def human_readable_extensions(self) -> str:
        """Las extensiones admitidas, ordenadas, para un mensaje de error."""
        return ", ".join(sorted(ext.lstrip(".") for ext in self.extensions))


def _extension_of(filename: str | None) -> str | None:
    """La extensión en minúsculas (con punto), o ``None`` si no hay.

    Se usa `os.path.splitext` sobre el nombre BASE para que un `filename`
    malicioso con directorios (`../../x.pdf`) no despiste. `.tar.gz` se trata
    aparte porque `splitext` sólo ve `.gz`.
    """
    if not filename:
        return None
    base = os.path.basename(filename.replace("\\", "/")).lower()
    if base.endswith(".tar.gz"):
        return ".tar.gz"
    _, ext = os.path.splitext(base)
    return ext or None


def _normalise_mime(content_type: str | None) -> str | None:
    """El tipo MIME en minúsculas y sin parámetros (`; charset=…`).

    ``application/octet-stream`` se colapsa a ``None``: es lo que manda un
    navegador que no reconoce la extensión, y tomarlo por información sería
    rechazar ficheros perfectamente parseables.
    """
    if not content_type:
        return None
    mime = content_type.split(";", 1)[0].strip().lower()
    if not mime or mime == _OPAQUE_MIME:
        return None
    return mime


# ---------------------------------------------------------------------------
# Preguntarle al servicio
# ---------------------------------------------------------------------------
async def fetch_input_formats(
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = OPENAPI_TIMEOUT_S,
) -> frozenset[str] | None:
    """El enum `InputFormat` del openapi de `docling-serve`, o ``None``.

    ``None`` significa «el servicio no me lo ha dicho» en TODOS sus sabores —
    caído, respuesta no-JSON, esquema renombrado por un bump—, y aguas arriba
    todos significan lo mismo: usar el respaldo. Nunca levanta.
    """
    owns = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    url = f"{base_url.rstrip('/')}{DOCLING_OPENAPI_ROUTE}"
    try:
        response = await http.get(url, timeout=timeout)
        if response.status_code >= 400:
            return None
        body = response.json()
    except Exception:  # httpx.HTTPError, JSONDecodeError, lo que sea
        return None
    finally:
        if owns:
            await http.aclose()
    return _input_formats_from_openapi(body)


def _input_formats_from_openapi(body: Any) -> frozenset[str] | None:
    """Extrae `components.schemas.InputFormat.enum` de un openapi.

    Se busca por nombre de schema conteniendo ``inputformat`` (y no por igualdad
    exacta) porque FastAPI puede prefijar el nombre cuando hay colisiones.
    Devuelve ``None`` si no hay un enum de cadenas reconocible: una lista vacía
    sería peor que el respaldo, porque rechazaría TODAS las subidas.
    """
    if not isinstance(body, dict):
        return None
    schemas = body.get("components", {})
    schemas = schemas.get("schemas") if isinstance(schemas, dict) else None
    if not isinstance(schemas, dict):
        return None
    for name, schema in schemas.items():
        if "inputformat" not in str(name).lower() or not isinstance(schema, dict):
            continue
        values = schema.get("enum")
        if isinstance(values, list):
            found = {str(v).lower() for v in values if isinstance(v, str)}
            if found:
                return frozenset(found)
    return None


# ---------------------------------------------------------------------------
# Caché de proceso
# ---------------------------------------------------------------------------
_FALLBACK = SupportedFormats.from_input_formats(FALLBACK_INPUT_FORMATS, source="fallback")


class _Cache:
    """Portador de la caché de proceso. Una clase y no una global suelta para
    que escribirla no necesite `global` — que aquí, con un solo escritor (el
    arranque), sería ruido."""

    value: SupportedFormats | None = None


def cached_supported_formats() -> SupportedFormats:
    """Lo que hay en la caché, o el respaldo. **No hace I/O**: es lo que llama
    la ruta de subida, y una validación de entrada no puede depender de la
    red."""
    return _Cache.value if _Cache.value is not None else _FALLBACK


def reset_supported_formats_cache() -> None:
    """Vacía la caché (tests; y cualquier arranque re-primea igualmente)."""
    _Cache.value = None


async def refresh_supported_formats(
    *,
    base_url: str,
    client: httpx.AsyncClient | None = None,
    timeout: float = OPENAPI_TIMEOUT_S,
) -> SupportedFormats:
    """Repuebla la caché preguntándole al servicio. Best-effort: si no
    contesta, deja/devuelve el respaldo y NUNCA levanta — esto corre en el
    lifespan del api-server y un fallo aquí sería un contenedor en bucle de
    reinicio por no poder validar extensiones."""
    formats = await fetch_input_formats(base_url=base_url, client=client, timeout=timeout)
    if formats is None:
        logger.warning(
            "ingestion.supported_formats.fallback",
            reason="docling-serve did not answer with an InputFormat enum",
            base_url=base_url,
        )
        _Cache.value = None
        return _FALLBACK
    resolved = SupportedFormats.from_input_formats(formats, source="docling-serve")
    unknown = sorted(formats - set(_EXTENSIONS))
    if unknown:
        # No es un error: docling ha añadido formatos que este módulo no sabe
        # mapear a extensión/MIME. Se ignoran (no se admiten por extensión),
        # y el aviso es la señal para ampliar los dos diccionarios.
        logger.info("ingestion.supported_formats.unmapped", formats=unknown)
    _Cache.value = resolved
    logger.info(
        "ingestion.supported_formats.loaded",
        source=resolved.source,
        count=len(resolved.input_formats),
    )
    return resolved
