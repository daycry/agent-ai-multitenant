---
adr: "0023"
title: Docling + Ollama embeddings + bge-reranker para el pipeline RAG
status: accepted
date: 2026-05-26
deciders: System Admin
phase: 04-memoria-rag-kbs
---

# ADR 0023 — Docling + Ollama embeddings + bge-reranker

> **Estado: `accepted`.** Cierra Plan 04: las cuatro elecciones
> clave del stack RAG quedan congeladas en estas líneas, con sus
> alternativas descartadas anotadas para futura referencia.

## Contexto

Plan 04 monta la pila RAG completa: ingestión de documentos → chunks
→ embeddings → búsqueda híbrida → reranker → tool del agente. Cada
caja es un sub-sistema con varios candidatos en el mercado. Llegamos
a esta fase con cuatro decisiones que merecían ADR:

1. **Parser/chunker** de documentos.
2. **Modelo de embeddings**.
3. **Vector store**.
4. **Reranker**.

Las cuatro están entrelazadas — cambiar embeddings implica
re-indexar, cambiar vector store implica reescribir SQL, cambiar
parser cambia la calidad inicial — así que las congelamos juntas en
un solo ADR.

## Decisiones

### 1. Parser: **Docling** (docling-serve + docling-mcp)

Open source (IBM Research). Maneja 65+ formatos (PDF, Office, HTML,
Markdown, audio vía Whisper) con un solo HTTP endpoint. La salida
estructurada preserva la jerarquía (heading → paragraph → list →
table) y emite bounding boxes para PDFs — la base de la
visualización de citas (task_04_25).

Dos despliegues:

- **docling-serve** (`/v1/convert`) — endpoint HTTP REST clásico
  para el worker de ingestión (Plan 04 task_04_11). Consume bytes,
  devuelve chunks.
- **docling-mcp** (`/tools/call/convert`) — superficie MCP-over-HTTP
  para que el agente la llame como una herramienta in-flight
  (`document_convert`, task_04_22). Misma parser, distinto
  transporte.

**Descartado**:

- **LlamaParse** — comercial, hosting externo (saca el contenido
  del proyecto de la red controlada del tenant).
- **Unstructured.io** — open source pero rendimiento en PDFs
  complejos es inferior a Docling según benchmarks IBM publicó al
  liberar Docling.
- **PyMuPDF/pdfplumber a pelo** — sin jerarquía estructural ni
  audio; reinventaríamos lo que Docling ya hace.

### 2. Embeddings: **Ollama + `nomic-embed-text-v1.5`** (768 dims)

Modelo open-source pequeño (~270 MB) con calidad competitiva en MTEB
para inglés + español. Corre local vía Ollama; sin coste por token;
sin sacar contenido fuera del stack. La dimensionalidad 768 es la
misma que `text-embedding-3-small` (OpenAI), facilitando una
migración eventual sin tocar el schema.

Configurable por KB: la columna `knowledge_bases.embedding_model_id`
permite que distintas KBs usen distintos modelos. Plan 12 añadirá
re-embedding masivo cuando se cambie el modelo de una KB existente.

**Descartado** como default:

- **OpenAI `text-embedding-3-small`** — calidad superior pero pago
  por token + datos externos. Sigue disponible como opt-in por KB.
- **`sentence-transformers` in-process** — infla la imagen del
  api-server con torch + transformers y compite por CPU/RAM con
  FastAPI. Ollama aísla esto en un servicio dedicado.
- **`bge-m3`** — buen modelo pero 3.5× más pesado en disco que
  nomic. Reservado para tenants que pidan multilingual estricto.

### 3. Vector store: **pgvector + HNSW** en el mismo PostgreSQL

`pgvector >= 0.7` con índice **HNSW** (m=16, ef_construction=64) y
operador cosine (`vector_cosine_ops`). Vive en el mismo Postgres
que el resto de tablas — sin nueva infra, transacciones atómicas
entre `chunks`, `documents` y `knowledge_bases`, RLS por tenant
heredado del resto del schema (ADR 0001).

**Descartado**:

- **Qdrant** — excelente vector DB pero servicio aparte =
  inconsistencia eventual contra `documents` + RLS reimplementado a
  mano.
- **Weaviate / Milvus** — overkill para el volumen objetivo
  (<10M chunks por tenant).
- **pgvector + IVFFlat** — index más rápido de construir pero
  recall inferior a HNSW. RAG vive de la recall.

### 4. Reranker: **`bge-reranker-v2-m3`** local vía FlagEmbedding

Cross-encoder multilingüe pequeño (~568 MB) corriendo en CPU. Tras
la recall híbrida (BM25 + vector + RRF) devolvemos 20 candidatos al
reranker, que reordena con scoring (query, chunk) y entrega top-5
al agente.

Implementación **lazy-imported**: la `Reranker` Protocol tiene tres
implementaciones (`NoopReranker`, `DeterministicReranker`,
`BGEReranker`). Los tests usan las fakes; producción usa
`BGEReranker` que importa `FlagEmbedding` sólo cuando se invoca.
Despliegues que no quieran cargar torch + transformers en la imagen
del api-server pueden simplemente no instalar el extra opcional.

**Descartado**:

- **Cohere Rerank** — API externa, coste por llamada.
- **MS MARCO MiniLM cross-encoder** — sólo inglés.
- **Sin reranker** (RRF directo) — calidad RAG cae ~15-20 % en
  benchmarks; merece el ms extra de latencia.

## Reciprocal Rank Fusion (RRF)

Constante de smoothing **k=60** (Cormack et al. 2009). Los tests
unitarios (`tests/unit/test_rrf.py` y
`tests/unit/test_memory_recall_rrf.py`) la blindan: una refactor
silenciosa que cambie ese valor degradaría retrieval en todo el
sistema.

## Visibilidad de KBs

Una KB sin filas en `kb_projects` es **invisible para todo
project** del tenant. Las grants son explícitas (POST
`/knowledge-bases/{id}/projects`). Decisión tomada en la pregunta
al humano durante Fase B (Plan 04); preferimos el modelo seguro por
defecto al "tenant-wide implícito".

## Consecuencias

**Positivas**:

- Stack 100 % open source y local. Un tenant puede correr el
  pipeline RAG completo sin sacar nada fuera de su Docker Compose.
- Costes de embeddings/reranker = 0 €. Sólo CPU/RAM.
- Misma transacción para `documents` + `chunks` + RLS (no hay
  consistencia eventual entre vector store y metadata).

**Negativas**:

- Latencia del reranker en CPU (~50-150 ms por batch de 20). En
  picos saturará una CPU; Plan 12 lo aborda con un pool dedicado.
- Postgres + HNSW + RLS suma carga al mismo proceso que sirve
  todo. Un día habrá que separar el lector RAG (réplica con
  pgvector pre-cargado).
- Ollama necesita modelo descargado en el primer boot del stack
  (`ollama pull nomic-embed-text:v1.5`). Documentado en
  `docs/03-guides/kb-ingestion.md`.

## Tests / referencias

- `tests/integration/test_docling_ingestion.py` — pipeline scan →
  parse → embed → persist.
- `tests/integration/test_embeddings.py` — contrato del Embedder
  Protocol contra MockTransport.
- `tests/integration/test_bm25_search.py` / `test_vector_search.py`
  / `test_rag_search_tool.py` — los tres ejes de retrieval.
- `tests/integration/test_reranker.py` — Protocol + lazy import de
  FlagEmbedding.
- `apps/admin-panel/e2e/citation-bboxes.spec.ts` — surface del
  visor de citas sobre los bboxes de Docling.

## Próximos pasos

- **Plan 12** — re-embedding masivo cuando un tenant cambie el
  modelo de embeddings de una KB existente.
- **Plan 12** — pool dedicado de CPUs para el reranker.
- **Plan 09** (marketplace) — KBs compartidas cross-tenant con
  permisos de lectura.
