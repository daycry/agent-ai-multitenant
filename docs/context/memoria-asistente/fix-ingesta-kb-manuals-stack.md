---
name: fix-ingesta-kb-manuals-stack
description: La ingesta de KB del stack de manuales estaba rota en 3 capas; arreglada (worker env + cliente docling v1.20.0).
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

2026-06-25: al crear una KB (subir docs → ingesta) en el stack de manuales, los documentos quedaban `failed`. Era una **cadena de 3 bugs**, todos por el mismo patrón (el worker reusa deps de `api_server` que leen settings `API_SERVER_*` y, sin override, caen a `localhost`):

1. **MinIO**: el servicio `workers` en `docker-compose.manuals.yml` no tenía `API_SERVER_MINIO_URL/ACCESS_KEY/SECRET_KEY` → `storage read failed: localhost:9000 connection refused`. Fix: añadidas al `environment` del worker (mismo MinIO dev que api-server).
2. **docling/clamav/ollama**: faltaban `API_SERVER_DOCLING_SERVE_URL=http://docling-serve:5001`, `API_SERVER_CLAMAV_HOST=clamav`+`PORT=3310`, `API_SERVER_OLLAMA_URL=http://ollama:11434`. Fix: añadidas al worker.
3. **cliente docling desfasado** (`apps/api-server/src/api_server/ingestion/docling.py`): posteaba a `POST /v1/convert` (campo `file`), pero docling-serve **v1.20.0** lo eliminó → 404. La ingesta quiere chunks, así que el endpoint correcto es **`POST /v1/chunk/hybrid/file`** (campo `files`, plural); su respuesta `{"chunks":[...]}` ya la maneja `_flatten_chunks`. Fix de código + test unit `tests/unit/test_docling_client.py` (httpx.MockTransport). `_flatten_chunks` NO se tocó.

Para desplegar el #3 hay que reconstruir **`api-server:ci`** (base, donde vive api_server) y luego **`workers:ci`** (`FROM api-server:ci`) — `api-server:ci` se construye SIN `WITH_CLAUDE` (igual que el CI; no tiene el CLI de claude, no romper eso). `api-server:manuals` (servicio api-server) NO ejecuta ingesta, así que no es crítico rebuildearla, pero queda con el docling viejo.

Verificado e2e: 8 docs `indexed`, chunks embebidos, búsqueda semántica devuelve resultados relevantes.

**Estado git (2026-06-25, todo commiteado, NADA pusheado):**

- docling.py + `tests/unit/test_docling_client.py` → rama **`fix/kb-ingestion-docling-v1.20`** (desde master, `62e4cbe`).
- compose (`docker-compose.manuals.yml`, +6 envs API*SERVER*\* al worker) → NO puede ir a master (el fichero solo vive en el stack prod-01/feat) → commiteado en **`feat/provider-llm-selection`** (`4e029bb`).
- Bonus de la misma sesión: 2 bugs de consola del admin-panel (input `pattern` inválido bajo flag `v` en slugs de categorías KB + `/admin` sin página índice → 404 en breadcrumbs "Inicio"/prefetch RSC) → rama **`fix/admin-panel-console-errors`** (desde master, `06dc4b1`); verificado con crawl Playwright de 31 rutas = 0 errores. `api-server:manuals` sigue con el docling viejo pero NO ejecuta ingesta (irrelevante).

Bug aparte detectado (no tocado): `backfill_memory_embeddings` del worker falla con `ollama localhost` (mismo patrón, otra ruta). KB de prueba creada: "Webscorpo - Arquitectura (CI4+Doctrine+Twig)" en tenant Demo, concedida a los 10 agentes del equipo CI4 [[codeigniter4-builtin-team]]. Relacionado [[adr-0082-provider-id-unificacion]].
