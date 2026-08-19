"""Los LLAMANTES del contrato de embeddings (ADR 0155, `task_audit14_05`).

El módulo `embedding_contract` tiene sus propios tests; éstos prueban lo otro,
que es donde estaba el hallazgo AUD14-03: que el contrato **está cableado**. El
patrón dominante de esta base es «mecanismo entregado, cero llamantes», y un
contrato de embeddings que sólo existe en un módulo de utilidades sería
exactamente eso otra vez.

Cuatro puntos de cableado, uno por regla del ADR:

  * regla 2 — la API rechaza (422) un modelo que la plataforma no usa;
  * regla 3 — la respuesta enseña el sello canonizado y marca el desfase;
  * regla 4 — la ingesta se niega ANTES de embeber si el sello no es el activo;
  * regla 5 — el camino vectorial filtra por espacio de embeddings.

Más el eslabón que sostiene todo: el embedder real pide a Ollama EXACTAMENTE el
modelo del setting, que es el que se sella. Sin ese eslabón, comparar el sello
contra el setting no probaría nada sobre lo que de verdad se envía.

Sin BD y sin red: sesiones de mentira (el pipeline sólo hace `execute`, `add` y
`flush`) y un `httpx.MockTransport` para el embedder.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest
from api_server.ingestion.antivirus import AntivirusReport, AntivirusVerdict
from api_server.ingestion.docling import DoclingChunk
from api_server.routers._kb_embedding import stamp_for_kb_update, stamp_for_new_kb
from api_server.schemas.knowledge import to_kb_response
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.unit

_ACTIVE = "nomic-embed-text"
_LEGACY_STAMP = "nomic-embed-text-v1.5"  # lo que hay sellado en las 14 KBs reales
_OTHER_768 = "granite-embedding:278m"  # otro modelo de 768 dims → otro espacio


# ===========================================================================
# Regla 2 — la API no acepta un modelo que la plataforma no va a usar
# ===========================================================================
def test_creating_without_a_model_seals_the_active_one() -> None:
    assert stamp_for_new_kb(None, active_model=_ACTIVE) == _ACTIVE
    assert stamp_for_new_kb("", active_model=_ACTIVE) == _ACTIVE


def test_creating_with_the_legacy_label_is_accepted_as_the_same_model() -> None:
    # No es capricho: la UI y cuatro literales del repo mandaban esa etiqueta.
    assert stamp_for_new_kb(_LEGACY_STAMP, active_model=_ACTIVE) == _ACTIVE


def test_creating_with_a_foreign_model_is_422_not_a_decorative_201() -> None:
    with pytest.raises(HTTPException) as exc:
        stamp_for_new_kb("text-embedding-3-small", active_model=_ACTIVE)
    assert exc.value.status_code == 422
    assert "text-embedding-3-small" in str(exc.value.detail)
    assert _ACTIVE in str(exc.value.detail)


def test_update_to_the_same_model_is_a_noop() -> None:
    assert (
        stamp_for_kb_update(
            requested=_LEGACY_STAMP, current=_LEGACY_STAMP, has_chunks=True, active_model=_ACTIVE
        )
        is None
    )


def test_update_to_a_foreign_model_is_422_even_on_an_empty_kb() -> None:
    # Antes esto devolvía 200 y guardaba `text-embedding-3-small`: un modelo de
    # OpenAI en una plataforma que sólo habla con Ollama a 768 dims.
    with pytest.raises(HTTPException) as exc:
        stamp_for_kb_update(
            requested="text-embedding-3-small",
            current=_LEGACY_STAMP,
            has_chunks=False,
            active_model=_ACTIVE,
        )
    assert exc.value.status_code == 422


def test_restamping_a_kb_with_chunks_is_409() -> None:
    with pytest.raises(HTTPException) as exc:
        stamp_for_kb_update(
            requested=_ACTIVE, current=_OTHER_768, has_chunks=True, active_model=_ACTIVE
        )
    assert exc.value.status_code == 409
    assert _OTHER_768 in str(exc.value.detail)


def test_restamping_an_empty_kb_is_allowed() -> None:
    assert (
        stamp_for_kb_update(
            requested=_ACTIVE, current=_OTHER_768, has_chunks=False, active_model=_ACTIVE
        )
        == _ACTIVE
    )


# ===========================================================================
# Regla 3 — lo que la API enseña es lo que produjo los vectores
# ===========================================================================
class _Kb:
    """Fila `knowledge_bases` mínima — `to_kb_response` sólo lee atributos."""

    def __init__(self, stamp: str) -> None:
        now = datetime.now(tz=UTC)
        self.id = uuid4()
        self.tenant_id = uuid4()
        self.name = "KB"
        self.description = None
        self.embedding_model_id = stamp
        self.created_by = None
        self.created_at = now
        self.updated_at = now
        self.is_builtin = False


def test_response_canonises_the_legacy_stamp_and_is_not_stale() -> None:
    resp = to_kb_response(cast(Any, _Kb(_LEGACY_STAMP)), None, active_model=_ACTIVE)
    # La pantalla enseñaba `nomic-embed-text-v1.5`, que NO es un tag válido de
    # Ollama y jamás se envió a /api/embed.
    assert resp.embedding_model_id == _ACTIVE
    assert resp.platform_embedding_model == _ACTIVE
    assert resp.embedding_model_stale is False


def test_response_flags_a_kb_sealed_with_another_model() -> None:
    resp = to_kb_response(cast(Any, _Kb(_OTHER_768)), None, active_model=_ACTIVE)
    assert resp.embedding_model_id == _OTHER_768
    assert resp.platform_embedding_model == _ACTIVE
    assert resp.embedding_model_stale is True


# ===========================================================================
# Regla 4 — la ingesta se niega antes que mezclar
# ===========================================================================
class _Doc:
    def __init__(self) -> None:
        self.id = uuid4()
        self.tenant_id = uuid4()
        self.kb_id = uuid4()
        self.source_storage_key = "k"
        self.source_filename = "manual.pdf"
        self.source_mime_type = "application/pdf"
        self.status = "pending"
        self.error_message: str | None = None
        self.indexed_at = None
        self.page_count = 0
        self.deleted_at = None


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _Session:
    """El pipeline sólo hace `execute` (documento + sello de la KB), `add` y
    `flush`."""

    def __init__(self, doc: _Doc, kb_stamp: str) -> None:
        self.doc = doc
        self.kb_stamp = kb_stamp
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        if "knowledge_bases" in str(stmt):
            return _Result(self.kb_stamp)
        return _Result(self.doc)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _Storage:
    def __init__(self) -> None:
        self.reads = 0

    async def get_object(self, *, key: str) -> bytes:
        self.reads += 1
        return b"bytes"


class _Clean:
    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport:
        return AntivirusReport(verdict=AntivirusVerdict.CLEAN)


class _Docling:
    async def convert(self, *, filename: str, content_type: str, data: bytes) -> list[DoclingChunk]:
        return [DoclingChunk(ordinal=0, content="texto", bbox=None, metadata={})]


class _Embedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: Any) -> list[list[float]]:
        self.calls += 1
        return [[0.0] for _ in list(texts)]

    async def aclose(self) -> None:
        return None


async def _ingest(kb_stamp: str, active: str) -> tuple[Any, _Storage, _Embedder]:
    from api_server.ingestion.pipeline import ingest_document

    doc = _Doc()
    session = _Session(doc, kb_stamp)
    storage, embedder = _Storage(), _Embedder()
    result = await ingest_document(
        cast(AsyncSession, session),
        document_id=doc.id,
        storage=cast(Any, storage),
        antivirus=cast(Any, _Clean()),
        docling=cast(Any, _Docling()),
        embedder=cast(Any, embedder),
        redis=None,
        platform_embedding_model=active,
    )
    return result, storage, embedder


@pytest.mark.asyncio
async def test_ingestion_refuses_a_kb_sealed_with_another_model() -> None:
    result, storage, embedder = await _ingest(_OTHER_768, _ACTIVE)

    assert result.status == "failed"
    assert result.chunks_persisted == 0
    # El mensaje viaja a `documents.error_message` y de ahí a la ficha: el
    # operador tiene que poder leer QUÉ dos modelos chocan sin abrir los logs.
    assert _OTHER_768 in (result.error_message or "")
    assert _ACTIVE in (result.error_message or "")
    # Y se niega ANTES de gastar red: ni bytes, ni antivirus, ni embedder.
    assert storage.reads == 0
    assert embedder.calls == 0


@pytest.mark.asyncio
async def test_ingestion_accepts_the_legacy_stamp_without_a_false_positive() -> None:
    # Las 14 KBs de la instalación medida están selladas así. Si esto fallara,
    # la guarda dejaría al sistema sin ingesta el día que se despliegue.
    result, _storage, embedder = await _ingest(_LEGACY_STAMP, _ACTIVE)

    assert result.status == "indexed"
    assert embedder.calls == 1


@pytest.mark.asyncio
async def test_ingestion_refuses_a_kb_without_a_stamp() -> None:
    result, _storage, embedder = await _ingest("", _ACTIVE)

    assert result.status == "failed"
    assert embedder.calls == 0


# ===========================================================================
# Regla 5 — el camino vectorial no mezcla espacios semánticos
# ===========================================================================
class _CapturingResult:
    def all(self) -> list[Any]:
        return []


class _CapturingSession:
    """Captura el SQL y los parámetros del camino vectorial."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _CapturingResult:
        self.statements.append(str(stmt))
        self.params.append(params or {})
        return _CapturingResult()


@pytest.mark.asyncio
async def test_vector_path_filters_by_embedding_space() -> None:
    from api_server.rag.search import vector_chunks

    session = _CapturingSession()
    await vector_chunks(
        cast(AsyncSession, session),
        query_embedding=[0.1] * 4,
        tenant_id=uuid4(),
        project_id=uuid4(),
        embedding_model=_ACTIVE,
    )

    sql = session.statements[-1]
    assert "embedding_model_id = ANY(:embedding_model_refs)" in sql
    refs = session.params[-1]["embedding_model_refs"]
    # Las tres grafías con las que el sello puede estar escrito en la columna…
    assert set(refs) == {_ACTIVE, f"{_ACTIVE}:latest", _LEGACY_STAMP}
    # …y ninguna de otro modelo, que es lo que no debe competir con la consulta.
    assert _OTHER_768 not in refs


@pytest.mark.asyncio
async def test_kb_preview_vector_path_filters_too() -> None:
    from api_server.rag.search import _kb_vector_chunks

    session = _CapturingSession()
    await _kb_vector_chunks(
        cast(AsyncSession, session),
        kb_id=uuid4(),
        query_embedding=[0.1] * 4,
        limit=8,
        embedding_model=_ACTIVE,
    )

    assert "embedding_model_id = ANY(:embedding_model_refs)" in session.statements[-1]
    assert _LEGACY_STAMP in session.params[-1]["embedding_model_refs"]


# ===========================================================================
# El eslabón: lo que se sella es lo que el embedder pide a Ollama
# ===========================================================================
@pytest.mark.asyncio
async def test_the_embedder_asks_ollama_for_the_configured_model() -> None:
    """Sin esto, comparar el sello contra el setting no probaría nada.

    El sello vale porque el embedder de producción manda a `/api/embed`
    EXACTAMENTE `settings.embedding_model` cuando nadie le pasa `model_id` — que
    es como lo construyen la ingesta, la memoria y la consulta.
    """
    from api_server.ingestion.embeddings import OllamaEmbedder

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [[0.0] * 768]})

    class _Cfg:
        embedding_model = "granite-embedding:278m"
        ollama_url = "http://test"

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emb = OllamaEmbedder(client=client, settings=cast(Any, _Cfg()))
    try:
        await emb.embed(["hola"])
    finally:
        await client.aclose()

    assert emb.model_id == "granite-embedding:278m"
    assert seen["model"] == "granite-embedding:278m"
