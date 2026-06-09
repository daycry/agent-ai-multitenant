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
   `Vector(768)` (`CHUNK_EMBEDDING_DIM`) y el `embedding_model_id` de una KB es
   **inmutable** una vez tiene chunks (re-embed masivo → Plan 12). Un embedder de
   otra dimensión (`mxbai-embed-large`/`bge`/`snowflake-arctic-embed:335m`→1024;
   `all-minilm`→384) hace fallar el embedder o rompe el esquema.

> Ojo a una sutileza cosmética: la columna `knowledge_bases.embedding_model_id`
> sigue defaulteando a la **etiqueta** `nomic-embed-text-v1.5` (es informativa y
> sigue siendo correcta: el modelo ES v1.5). Lo que se manda a `/api/embed` es el
> nombre real `nomic-embed-text`. No son el mismo string a propósito; no los
> compares entre sí.

## Fix

- El modelo que el embedder envía es **configurable** (ADR 0056):
  `API_SERVER_EMBEDDING_MODEL`, **default `nomic-embed-text`** (el nombre real).
  No pongas el sufijo `-v1.5`.
- El `ollama-bootstrap` del stack hace `pull` de **ese mismo** nombre
  (`EMBEDDING_MODEL` en compose / `embedding_model` en el instalador).
- Si quieres otro embedder, elige uno de **768 dims** (ver los recomendados en
  Admin → «Ollama & Embeddings», o `ingestion/embedding_models.recommended_models`).
  Cambiar a otra dimensión con KBs ya existentes **no** es un cambio de env: es el
  re-embedding masivo del **Plan 12**.

Ver el runbook [Ollama en el stack (CPU/GPU)](../../06-runbooks/ollama-gpu-setup.md)
y el **ADR 0056**.
