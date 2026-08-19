"""prod-13 · task_prod13_16 — el embed de la ingesta va TROCEADO en lotes.

`ingest_document` embebía todos los chunks del documento en UNA sola llamada.
Un manual de 300 páginas son miles de chunks: una petición gigante al embedder
que tarda minutos, consume memoria en los dos extremos y, si revienta, deja el
documento entero sin vector — "verde en la UI, invisible para el RAG vectorial",
que es justo el hueco que la tarea cierra.

Lo que se mide aquí:

  * que se trocea (nº de llamadas y tamaño de cada lote);
  * que el troceo NO desordena: el chunk i sigue llevando el vector de su propio
    contenido — un fallo de alineación aquí es silencioso y envenena el RAG;
  * que un lote que falla se pierde SOLO a sí mismo (el resto conserva vector, y
    el backfill rellenará los NULL), en vez de tumbar el documento entero;
  * que un embedder que devuelve un número de vectores distinto del que se le
    pidió NO produce una asignación cruzada.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.ingestion.antivirus import AntivirusReport, AntivirusVerdict
from api_server.ingestion.docling import DoclingChunk
from api_server.ingestion.embeddings import EmbeddingError


class _Doc:
    """Fila `documents` mínima — el pipeline solo lee/escribe atributos."""

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


class _FakeSession:
    """Sesión de mentira: el pipeline solo hace `execute`, `add` y `flush`.

    Desde el ADR 0155 el pipeline hace DOS consultas distintas —la fila
    `documents` y el sello de embeddings de su KB— así que la sesión ya no
    puede devolver lo mismo a todo. Discrimina por la tabla que menciona el
    SQL; devolver el documento también a la consulta del sello dejaba la KB
    «sin sello» y el documento terminaba en `failed` antes de llegar al
    troceado que este módulo mide.
    """

    def __init__(self, doc: _Doc, *, kb_embedding_model: str = "nomic-embed-text") -> None:
        self.doc = doc
        self.kb_embedding_model = kb_embedding_model
        self.added: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        if "knowledge_bases" in str(stmt):
            return _Result(self.kb_embedding_model)
        return _Result(self.doc)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


class _Storage:
    async def get_object(self, *, key: str) -> bytes:
        return b"bytes"


class _Clean:
    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport:
        return AntivirusReport(verdict=AntivirusVerdict.CLEAN)


class _Docling:
    def __init__(self, n: int) -> None:
        self.chunks = [DoclingChunk(ordinal=i, content=f"texto-{i}") for i in range(n)]

    async def convert(self, *, filename: str, content_type: str, data: bytes):
        return list(self.chunks)


class _BatchEmbedder:
    """Registra CADA lote recibido y devuelve un vector derivado del texto, de
    modo que el test puede comprobar que el vector llegó a SU chunk."""

    def __init__(self, *, fail_on_batch: int | None = None, short_on_batch: int | None = None):
        self.batches: list[list[str]] = []
        self._fail_on = fail_on_batch
        self._short_on = short_on_batch

    async def embed(self, texts):
        texts = list(texts)
        self.batches.append(texts)
        index = len(self.batches) - 1
        if self._fail_on == index:
            raise EmbeddingError("ollama caído")
        vectors = [[float(int(t.split("-")[1]))] for t in texts]
        if self._short_on == index:
            return vectors[:-1]
        return vectors

    async def aclose(self) -> None:
        return None


async def _run(n_chunks: int, embedder: _BatchEmbedder):
    from api_server.ingestion.pipeline import ingest_document

    doc = _Doc()
    session = _FakeSession(doc)
    result = await ingest_document(
        session,  # type: ignore[arg-type]
        document_id=doc.id,
        storage=_Storage(),  # type: ignore[arg-type]
        antivirus=_Clean(),  # type: ignore[arg-type]
        docling=_Docling(n_chunks),  # type: ignore[arg-type]
        embedder=embedder,  # type: ignore[arg-type]
        redis=None,
    )
    return result, session


def test_the_batch_size_is_a_named_bound_not_a_magic_number() -> None:
    from api_server.ingestion import pipeline

    assert 1 <= pipeline.EMBED_BATCH_SIZE <= 256
    assert pipeline.EMBED_BATCH_SIZE == 64, "el plan fija 64 chunks/request como referencia"


@pytest.mark.asyncio
async def test_a_big_document_is_embedded_in_several_bounded_batches() -> None:
    """150 chunks con lotes de 64 → 3 peticiones (64 + 64 + 22), no una de 150."""
    from api_server.ingestion.pipeline import EMBED_BATCH_SIZE

    embedder = _BatchEmbedder()
    result, _session = await _run(150, embedder)

    assert result.status == "indexed"
    assert result.chunks_persisted == 150
    assert len(embedder.batches) == 3, f"se hicieron {len(embedder.batches)} peticiones"
    assert [len(b) for b in embedder.batches] == [64, 64, 22]
    assert all(len(b) <= EMBED_BATCH_SIZE for b in embedder.batches)


@pytest.mark.asyncio
async def test_a_document_that_fits_in_one_batch_still_makes_one_request() -> None:
    """El troceo no puede convertir el caso normal en N peticiones de 1."""
    embedder = _BatchEmbedder()
    await _run(10, embedder)
    assert len(embedder.batches) == 1
    assert len(embedder.batches[0]) == 10


@pytest.mark.asyncio
async def test_no_chunks_means_no_request_to_the_embedder() -> None:
    embedder = _BatchEmbedder()
    result, _ = await _run(0, embedder)
    assert embedder.batches == []
    assert result.status == "indexed_empty"


@pytest.mark.asyncio
async def test_batching_does_not_misalign_a_chunk_with_another_chunks_vector() -> None:
    """El fallo caro del troceo es silencioso: los vectores se pegan al chunk
    equivocado y el RAG devuelve basura sin que nada falle."""
    embedder = _BatchEmbedder()
    _result, session = await _run(150, embedder)

    assert len(session.added) == 150
    for chunk in session.added:
        expected = float(int(chunk.content.split("-")[1]))
        assert chunk.embedding == [expected], f"chunk {chunk.ordinal} lleva el vector de otro"


@pytest.mark.asyncio
async def test_a_failing_batch_only_loses_its_own_chunks() -> None:
    """Antes, un `EmbeddingError` dejaba el documento ENTERO sin vector. Con
    lotes, lo correcto es perder solo el lote roto: los demás chunks quedan
    recuperables por vector desde ya y el backfill rellena los NULL."""
    embedder = _BatchEmbedder(fail_on_batch=1)
    result, session = await _run(150, embedder)

    assert result.status == "indexed"
    assert len(embedder.batches) == 3, "un lote roto cortó los siguientes"
    with_vector = [c for c in session.added if c.embedding is not None]
    without = [c for c in session.added if c.embedding is None]
    assert len(without) == 64, "se perdieron más chunks que los del lote que falló"
    assert {c.ordinal for c in without} == set(range(64, 128))
    assert len(with_vector) == 86
    for chunk in with_vector:
        assert chunk.embedding == [float(int(chunk.content.split("-")[1]))]


@pytest.mark.asyncio
async def test_an_embedder_returning_the_wrong_count_does_not_cross_wire_vectors() -> None:
    """Un embedder que devuelve menos vectores de los pedidos es un fallo del
    backend, no una invitación a emparejar por posición: ese lote se descarta
    entero (NULL) y el resto sigue. Mismo criterio que el backfill de chunks."""
    embedder = _BatchEmbedder(short_on_batch=0)
    result, session = await _run(150, embedder)

    assert result.status == "indexed"
    without = [c for c in session.added if c.embedding is None]
    assert {c.ordinal for c in without} == set(range(64))
    for chunk in session.added:
        if chunk.embedding is not None:
            assert chunk.embedding == [float(int(chunk.content.split("-")[1]))]
