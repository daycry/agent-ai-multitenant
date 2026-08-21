---
title: El embedder pide nomic-embed-text, NO nomic-embed-text-v1.5 (y debe ser de 768 dims)
area: embeddings, ollama
encountered: 2026-06-09
stack: ollama, pgvector, api-server
---

## Síntoma

Al re-sembrar o ingerir KBs, los embeddings fallan y las KBs quedan solo con
BM25 (sin vectores). En los logs:

```
ollama embed request failed / ollama embed 404: model "nomic-embed-text-v1.5" not found
```

O, al cambiar de modelo de embeddings:

```
EmbeddingError: ollama returned a 1024-dim vector, expected 768
```

## Causa raíz

Dos cosas distintas que es fácil confundir:

1. **Naming.** El modelo en el registro de Ollama se llama **`nomic-embed-text`**
   (que ES la v1.5, 768 dims). Pedir `nomic-embed-text-v1.5` da `model not found`
   porque ese tag no existe. Históricamente el embedder hardcodeaba el sufijo.

2. **Dimensión clavada en 768.** La columna pgvector de chunks/memoria es
   `Vector(768)` (`CHUNK_EMBEDDING_DIM`). Un embedder de otra dimensión
   (`mxbai-embed-large`/`bge`/`snowflake-arctic-embed:335m`→1024;
   `all-minilm`→384) hace fallar el embedder o rompe el esquema.

3. **Misma dimensión NO es el mismo espacio.** `nomic-embed-text` y
   `granite-embedding:278m` emiten los dos 768 floats: un `<=>` entre ellos
   devuelve un número válido y sin ningún sentido. Ese fallo no da error, sólo
   recall peor, y es el que hace que el modelo no pueda cambiarse en caliente.

> **Esta nota decía justo lo contrario hasta el ADR 0155.** Decía que la
> etiqueta `nomic-embed-text-v1.5` de la columna era «informativa» y que no había
> que comparar los dos strings entre sí. Era la descripción de una pantalla que
> miente: la UI presentaba ese valor como «modelo de embeddings de esta KB» y el
> embedder mandaba otro. Desde el ADR 0155 los dos strings **sí** se comparan, en
> un único sitio (`api_server.ingestion.embedding_contract`), que además sabe que
> son el mismo modelo escrito de dos maneras.

## Fix

- El modelo que el embedder envía es **configurable** (ADR 0056):
  `API_SERVER_EMBEDDING_MODEL`, **default `nomic-embed-text`** (el nombre real).
  No pongas el sufijo `-v1.5`.
- El `ollama-bootstrap` del stack hace `pull` de **ese mismo** nombre
  (`EMBEDDING_MODEL` en compose / `embedding_model` en el instalador).
- Si quieres otro embedder, elige uno de **768 dims** (ver los recomendados en
  Admin → «Ollama & Embeddings», o `ingestion/embedding_models.recommended_models`).
- **Cambiar el modelo NO es sólo cambiar el env var.** El sello
  `knowledge_bases.embedding_model_id` guarda con qué modelo se generaron los
  vectores de cada KB; en cuanto deja de coincidir con el activo, esa KB queda
  marcada `embedding_model_stale`, sale del camino vectorial y **rechaza
  documentos nuevos** (`failed` con el motivo en la ficha). El procedimiento de
  re-ingesta está en el [ADR 0155](../../05-architecture-decisions/0155-modelo-de-embeddings-de-kb.md).
- Y ponlo en **todos los servicios de aplicación**: la api-server sella y el
  worker embebe. Con los dos desalineados, toda ingesta falla — a propósito, en
  vez de mezclar espacios semánticos en silencio.

Ver el runbook [Ollama en el stack (CPU/GPU)](../../06-runbooks/ollama-gpu-setup.md),
el **ADR 0056** y el **ADR 0155**.
