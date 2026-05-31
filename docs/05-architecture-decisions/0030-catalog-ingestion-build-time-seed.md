---
adr_id: "0030"
title: "Ingesta del catálogo built-in como seed idempotente de build-time con chunker markdown ligero"
status: accepted
date: 2026-05-29
authors: [system_architect]
plan_referenced: 06.13-kb-catalog-content
docs_language: es
---

# ADR 0030 — Ingesta del catálogo como seed de build-time + chunker markdown ligero

## Contexto

Las 6 KBs built-in (Plan 06.9/06.12, ADR 0029) se siembran como
**contenedores vacíos** bajo `PLATFORM_TENANT_ID`: son navegables y
concedibles, pero conceder una no aporta nada al RAG hasta que tengan
chunks indexados. El Plan 06.13 las puebla a partir de un **corpus
curado** de ficheros `.md` versionados en el repo
(`apps/api-server/src/api_server/seeds/catalog/<slug>.md`,
task_06_13_01).

Quedaban dos decisiones abiertas (anotadas en el plan como "decisión a
tomar / ADR si hace falta"):

1. ¿La ingesta es un **seed de build-time** o un **cron de refresco**?
2. ¿Se parsea con **docling-serve** o con un **chunker markdown ligero**?

## Decisión

### 1. Seed idempotente de build-time, NO cron

La ingesta del catálogo es un **seed** (`catalog_ingestion.py`) que corre
junto a los demás seeds built-in (`python -m api_server.seeds`),
**después** de `seed_builtin_kbs` (las filas KB deben existir primero por
el FK `documents.kb_id`).

Razones:

- El corpus es **markdown curado versionado en el repo**. Cambia sólo
  cuando un humano edita un `.md` y publica una release — no hay fuente
  externa que sondear, así que un cron no haría nada el 99.9 % del
  tiempo.
- Reutiliza la infraestructura existente: el mismo esquema
  `documents` + `chunks` y el mismo `Embedder` Protocol del pipeline de
  Plan 04. No se inventa un esquema paralelo.

**Idempotencia**:

- Un documento estable por KB:
  `document_id = uuid5(CATALOG_DOC_NAMESPACE, slug)`. Re-sembrar hace
  upsert de esa única fila, no crea una nueva cada vez.
- Se estampa un **SHA-256 del corpus** (`corpus_hash`) en el metadata de
  cada chunk. En cada re-run, si los chunks existentes ya llevan el hash
  actual, el documento se **salta** (sin re-embed, sin duplicar). Si el
  `.md` cambió, los chunks viejos se borran y se insertan los nuevos
  (la unique constraint `(document_id, ordinal)` garantiza que no
  sobreviven duplicados).

**Descartado** — cron de refresco: añade un servicio beat, una cola y un
estado mutable para un input que sólo cambia en build-time. Si algún día
el corpus pasara a venir de una fuente externa (un repo remoto, un
bucket), entonces sí merecería un cron; hoy no.

### 2. Chunker markdown ligero, NO docling-serve

El corpus se trocea con un chunker propio (`chunk_markdown`) que corta
por encabezados markdown (`#` … `######`) y, dentro de una sección
larga, por límites de párrafo hasta un máximo de caracteres
(`MAX_CHUNK_CHARS = 1500`).

Razones:

- **docling-serve es un servicio HTTP externo** (ADR 0023) que está
  **caído en CI** y en el entorno de desarrollo offline. Hacer depender
  el seed de él rompería el arranque del stack y los tests.
- Para **markdown ya estructurado** un splitter por encabezados/párrafos
  produce chunks autocontenidos de calidad equivalente; no necesitamos
  el OCR ni el layout-analysis de Docling (pensados para PDF/DOCX).
- El seed queda **testeable offline**: el embedder es inyectable (Ollama
  real en producción; `HashEmbedder` determinista en tests, sin red).

**Descartado** — docling-serve sobre los `.md`: overkill para markdown,
y acopla el seed a un servicio externo indisponible en CI. Docling sigue
siendo el parser canónico para documentos subidos por el usuario
(PDF/Office/HTML); esto es sólo para el corpus curado del catálogo.

## Consecuencias

**Positivas**:

- El catálogo se puebla en el mismo `python -m api_server.seeds` que el
  resto del contenido built-in, sin infra nueva.
- Editar un `.md` y re-sembrar re-chunkea sólo lo que cambió; el resto se
  salta por hash. Re-correr el seed N veces no duplica nada.
- Sin dependencia de docling-serve ni de red en el camino del seed →
  arranque del stack robusto y tests offline.

**Negativas**:

- El chunker ligero no entiende tablas/listas complejas como Docling.
  Aceptable: el corpus es markdown curado y conciso por diseño (ver
  `seeds/catalog/README.md`).
- El embedding real (Ollama) sigue siendo necesario en producción para
  que los chunks sean recuperables por similitud; si Ollama está caído al
  sembrar, los chunks se persisten con `embedding = NULL` y BM25 los
  cubre hasta el siguiente re-seed (mismo comportamiento que el pipeline
  de Plan 04).

## Referencias

- `apps/api-server/src/api_server/seeds/catalog_ingestion.py` — el seed.
- `apps/api-server/src/api_server/seeds/catalog/` — el corpus curado.
- `tests/integration/test_catalog_ingestion.py` — happy path,
  idempotencia, re-chunk al editar, y el chunker.
- ADR 0023 — Docling/embeddings/RAG (parser canónico para uploads).
- ADR 0029 — platform tenant y catálogo global.
