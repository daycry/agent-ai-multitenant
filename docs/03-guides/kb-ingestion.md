---
title: Ingestar documentos en una Knowledge Base
audience: usuario tenant
phase: 04-memoria-rag-kbs
updated: 2026-05-26
---

# Ingestar documentos en una Knowledge Base

Esta guía recorre el flujo completo de subir un PDF (u otro formato
soportado) a una KB para que los agentes lo encuentren con
`rag_search`. Cubre los dos caminos:

- **Asíncrono** (recomendado, por defecto): `POST /knowledge-bases/{id}/documents` →
  worker de ingestión → indexado.
- **Síncrono** (in-flight desde el chat): `document_convert` →
  `promote_to_kb`.

> **Prerrequisitos.** El stack está arriba (`docker compose up -d`)
> con `docling-serve`, `docling-mcp`, `ollama`, `minio`, `clamav` y
> `postgres` corriendo. Ver `docs/02-getting-started/`.

## 1. Crear la KB

Un **`tenant_admin`** crea la KB y luego la grant al proyecto.

```http
POST /knowledge-bases
Content-Type: application/json

{
  "name": "Manuales del producto",
  "description": "PDFs internos del equipo de docs"
}
```

> **No mandes `embedding_model_id`** (ADR 0155). La plataforma indexa con un
> único modelo y lo sella ella: mandar otro devuelve **422**. La respuesta trae
> `embedding_model_id` (el sello, canonizado), `platform_embedding_model` (el
> activo) y `embedding_model_stale` (si esta KB se indexó con otro y necesita
> reindexado).

Por defecto la KB es **invisible para todo project**. Pásala a un
project con:

```http
POST /knowledge-bases/{kb_id}/projects
Content-Type: application/json

{ "project_id": "..." }
```

Ver [ADR 0023](../05-architecture-decisions/0023-docling-embeddings-rag.md)
para la rationale de "explicit grants by default".

## 2. Subir el documento (camino asíncrono)

Multipart contra el endpoint:

```http
POST /knowledge-bases/{kb_id}/documents
Content-Type: multipart/form-data; boundary=...

--...
Content-Disposition: form-data; name="file"; filename="manual.pdf"
Content-Type: application/pdf
<bytes>
--...
Content-Disposition: form-data; name="title"

Manual onboarding v3
--...
```

Devuelve `201` con el `Document` row en estado `pending`:

```json
{
  "id": "...",
  "kb_id": "...",
  "title": "Manual onboarding v3",
  "source_storage_key": "kb/{tenant}/{kb}/{doc}/manual.pdf",
  "source_size_bytes": 1234567,
  "status": "pending",
  "page_count": 0
}
```

A partir de aquí el worker de ingestión:

1. Escanea con ClamAV (un EICAR / virus marca `failed`).
2. Llama a `docling-serve /v1/convert` para parsear → chunks.
3. Llama a Ollama `/api/embed` para cada chunk → vector(768).
4. Persiste `chunks` rows con embeddings + bboxes + metadata.
5. Marca el `Document` como `indexed`.

Cada hito emite un evento al stream Redis `doc:{id}` que la UI
tira por WebSocket (`/ws/documents/{id}`) — el progreso se ve en
tiempo real en `/admin/documents/{id}/ingestion`.

## 3. Camino síncrono in-flight (`document_convert` + `promote_to_kb`)

Desde el chat el agente puede pedir parsear un documento sin
persistirlo:

```python
from api_server.ingestion import document_convert, HttpDoclingMCPClient

client = HttpDoclingMCPClient()  # apunta a docling-mcp
result = await document_convert(
    filename="memo.pdf",
    content_type="application/pdf",
    data=pdf_bytes,
    client=client,
)
# result.chunks está en memoria; nada persiste.
```

Si el humano (o el agente) decide que merece la pena guardar el
documento:

```python
from api_server.ingestion import promote_to_kb

await promote_to_kb(
    session,
    convert_result=result,
    tenant_id=tenant_id,
    kb_id=kb_id,
    raw_bytes=pdf_bytes,
    storage=minio_client,
    embedder=ollama_embedder,
    title="Memo de Q3",
)
```

`promote_to_kb` sube los bytes a MinIO con la **misma clave
canónica** que usa el endpoint upload (`kb/{tenant}/{kb}/{doc}/{filename}`),
así un usuario que ve un document en la UI no puede distinguir si
llegó por upload async o por convert+promote.

## 4. Visualizar el documento con citas

`/admin/documents/{id}/citations` renderiza una página por cada
página del documento con los bounding boxes overlaid. El sidebar
lista todos los chunks; un click salta al bbox correspondiente.

> El rendering real con PDF.js llega en una iteración posterior;
> Plan 04 fija la superficie (página → bbox → chunk) para que el
> swap a PDF.js sea transparente.

## 5. Buscar el contenido con `rag_search`

El agente llama al tool `rag_search` con:

```python
from api_server.rag import rag_search, NoopReranker, BGEReranker
from api_server.ingestion.embeddings import OllamaEmbedder

hits = await rag_search(
    session,
    query="cómo se autentica el cliente",
    tenant_id=tenant_id,
    project_id=project_id,
    limit=5,
    embedder=OllamaEmbedder(),
    reranker=BGEReranker(),  # o NoopReranker para evitar torch
)
```

Cada `RAGSearchHit` lleva:

- `content` — el texto del chunk
- `bbox` — para deep-linkar al visor de citas
- `bm25_rank`, `vector_rank` — la posición en cada path (debug)
- `rrf_score` — el score combinado pre-rerank
- `rerank_score` — el score del cross-encoder

## 6. Tabla rápida de formatos soportados

| Formato     | Backend Docling | Notas                               |
| ----------- | --------------- | ----------------------------------- |
| PDF         | nativo          | Bounding boxes para citation viewer |
| DOCX / PPTX | nativo          | Sin bboxes (no es paginado)         |
| HTML / MD   | nativo          | Sin bboxes                          |
| TXT         | nativo          | Sin chunking estructural — un chunk |
| WAV / MP3   | Whisper         | Sin bboxes; metadata Whisper-style  |
| EML         | nativo          | Mensaje + adjuntos                  |

## 7. Solución de problemas

| Síntoma                                           | Causa probable                  | Qué hacer                                                    |
| ------------------------------------------------- | ------------------------------- | ------------------------------------------------------------ |
| `status=failed` con `antivirus hit:`              | ClamAV detectó la firma         | Investigar el origen; el documento no es seguro              |
| `status=failed` con `docling-serve` error         | Docling crash en un PDF roto    | Re-OCR el PDF (Acrobat, pdftk) y reintentar                  |
| Todos los chunks con `embedding IS NULL`          | Ollama caído al ingestar        | El back-fill job recupera (Plan 04 Fase D follow-up)         |
| `403` en POST `/memories` con `scope=global`      | El usuario no es `tenant_admin` | Pedir al admin que cree la memoria, o usar otro scope        |
| KB no aparece en `/projects/{id}/knowledge-bases` | Falta el grant en `kb_projects` | POST `/knowledge-bases/{kb_id}/projects` con el `project_id` |

## Referencias

- [ADR 0023 — Docling + Ollama + bge-reranker](../05-architecture-decisions/0023-docling-embeddings-rag.md)
- [Plan 04 — roadmap](../roadmap/04-memoria-rag-kbs.md)
- API Reference: `POST /knowledge-bases/{id}/documents`,
  `POST /memories`, `GET /documents/{id}/citations`,
  `/ws/documents/{id}`.
