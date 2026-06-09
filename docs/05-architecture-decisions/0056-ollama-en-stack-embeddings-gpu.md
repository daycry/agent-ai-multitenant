---
adr_id: "0056"
title: "Ollama como servicio del stack para embeddings (+ LLM local), con modo Ninguno/CPU/GPU(CUDA)"
status: proposed
date: 2026-06-08
authors: [system_architect]
plan_referenced: 15-instalador-produccion
docs_language: es
---

# ADR 0056 — Ollama como servicio del stack (embeddings + LLM local), modo Ninguno/CPU/GPU

> **Estado: `proposed`** — pendiente de aprobación del operador. Sucesor natural
> del trabajo de embeddings (Plan 04) y del instalador (Plan 15). No introduce un
> quinto proveedor LLM: Ollama ya está en el catálogo cerrado del **ADR 0021**;
> esto decide **cómo se despliega** (servicio del stack) y **cómo se cablea** para
> embeddings, no qué proveedores existen.

## Contexto

Las KBs y la memoria semántica necesitan un **endpoint de embeddings**. Hoy:

- El embedder (`api_server.ingestion.embeddings.OllamaEmbedder`) llama a
  `POST {ollama_url}/api/embed` con el modelo `nomic-embed-text-v1.5` (768 dims).
  `ollama_url` por defecto es `http://localhost:11434` (`config.py`).
- **No existe ningún servicio Ollama en el stack de dev** (`docker/docker-compose.yml`).
  `docker/.env.example` ya reserva `OLLAMA_PORT=11434` pero **sin servicio**. Un
  re-seed real falla con `ollama embed request failed: All connection attempts
failed` y las KBs quedan indexadas solo para BM25, sin vectores.
- El **instalador** (`apps/installer`, Plan 15) ya genera un servicio `ollama`,
  pero con tres limitaciones para este caso:
  1. **Solo-GPU**: se añade únicamente si `gpu_enabled` (`compose_generator.py`
     `selected_services` + `_ollama_service` con `profiles: ["gpu"]` y reserva
     NVIDIA). Un operador **sin GPU** no obtiene Ollama local para embeddings.
  2. **No cablea el embedder**: inyecta `LLM_OLLAMA_ENDPOINT` (para el _provider
     LLM_ del runtime) pero **no** `API_SERVER_OLLAMA_URL` (la var que usa el
     embedder). Con GPU, los embeddings seguirían apuntando a `localhost`.
  3. **No hace bootstrap del modelo**: el contenedor arranca vacío; nada hace
     `pull` de `nomic-embed-text`, así que el primer `/api/embed` falla.
- Detalle de naming: el embedder pide `nomic-embed-text-v1.5`, pero en el
  registro de Ollama el modelo se llama `nomic-embed-text` (que ES v1.5, 768
  dims). Pedir el nombre con sufijo da `model not found`.

Objetivo: que **embeddings locales funcionen out-of-the-box** (dev e instalador),
sin obligar a instalar nada en el host, con **CPU por defecto** y **GPU (CUDA)
opcional** para quien quiera además servir LLMs locales con aceleración.

## Opciones consideradas

### Modo de despliegue del Ollama local

- **D-A. Solo-GPU (status quo del instalador).** Ollama solo si `gpu_enabled`.
  - ✅ Cero cambios. ❌ Sin GPU no hay embeddings locales — el caso más común en
    dev y en máquinas modestas. **Rechazada.**
- **D-B. Toggle 3-vías Ninguno / CPU / GPU (ELEGIDA).** El operador elige:
  `none` (usa Ollama externo/cloud o se queda en BM25), `cpu` (servicio Ollama
  sin reserva GPU — basta para embeddings y LLMs pequeños), `gpu` (añade la
  reserva NVIDIA `deploy.resources.reservations.devices` para LLMs grandes).
  - ✅ Cubre el caso sin GPU; la GPU es un _upgrade_, no un requisito. ✅ Detección
    de GPU para sugerir el modo. ❌ Un toggle más rico que el binario actual.
- **D-C. CPU siempre on + GPU como override.** Ollama CPU presente siempre.
  - ✅ Embeddings locales por defecto. ❌ Impone el contenedor (RAM/disco) aunque
    el operador prefiera Ollama externo/cloud o BM25. Menos control. **Rechazada.**

### Bootstrap del modelo de embeddings

- **B-A. Init one-shot (ELEGIDA).** Un servicio efímero `ollama-bootstrap`
  (misma imagen) que, cuando `ollama` está _healthy_, hace
  `ollama pull <embedding_model>` contra él y termina. Los servicios de app
  dependen de su `service_completed_successfully`. Idempotente; el modelo persiste
  en el volumen.
  - ✅ Limpio, declarativo, sin imagen custom; re-arrancar no re-descarga.
- **B-B. Dockerfile custom con el modelo horneado.** ❌ Imagen pesada, rebuild por
  modelo, peor cacheo. **Rechazada.**
- **B-C. La api-server hace pull al arrancar si falta.** ❌ Acopla el arranque de
  la app a una descarga de red; peor separación de responsabilidades. **Rechazada.**

### Naming del modelo

- **N-A. `ollama cp` para crear el alias `nomic-embed-text-v1.5`.** ❌ Paso manual
  frágil, fuera del control de versiones. **Rechazada.**
- **N-B. `embedding_model` configurable, default al nombre real (ELEGIDA).**
  Hacer `model_id` del embedder configurable (`API_SERVER_EMBEDDING_MODEL`) y
  poner el default en `nomic-embed-text` (el nombre del registro). El bootstrap
  hace `pull` de ese mismo nombre. Mismo modelo, mismas 768 dims → sin cambio de
  esquema ni re-embed forzado.

### Selección del modelo entre los embedders del Ollama local

Ollama puede tener varios embedders instalados (`nomic-embed-text`,
`mxbai-embed-large`, `snowflake-arctic-embed`, `all-minilm`…). ¿Cómo elige el
operador? **Tope duro:** la columna pgvector es `Vector(768)`
(`CHUNK_EMBEDDING_DIM`) y `knowledge_bases.embedding_model_id` es inmutable una
vez la KB tiene chunks (re-embed masivo diferido al Plan 12). Solo sirven
embedders de **768 dims** (`nomic-embed-text` sí; `mxbai`/`bge`/`arctic-335m`
→1024; `all-minilm`→384, NO). Y `/api/tags` lista lo instalado pero **no
etiqueta** cuál es embedder vs chat.

- **S-A. Selección libre de cualquier modelo instalado.** ❌ Rompe el esquema:
  un modelo ≠768 dims hace fallar el embedder (`EmbeddingError`). **Rechazada.**
- **S-B. Dimensión dinámica + re-embed al cambiar.** ❌ Mucho mayor; es justo el
  Plan 12 (re-embedding masivo). Fuera de alcance — pediría su propio ADR.
- **S-C. Descubrir + fijar (env), panel informativo (ELEGIDA).** Un endpoint
  `GET /admin/embeddings/available-models` sondea `{ollama_url}/api/tags`, lo
  cruza con una **allowlist curada** de embedders conocidos (nombre→dims) y
  marca los **compatibles (768)**, el modelo **activo** y la **reachability**. El
  modelo se **fija por env** (`API_SERVER_EMBEDDING_MODEL`, elegido en el
  instalador/config; el bootstrap lo `pull`ea). El panel de admin es
  **informativo** (read-only): muestra qué embedders 768 hay instalados, cuál
  está activo y cuáles se recomiendan. **Sin** tocar los workers, **sin** nueva
  migración, **sin** swap en vivo (cambiar de modelo con KBs existentes = Plan 12).

## Decisión

1. **Ollama es un servicio del stack con modo `none | cpu | gpu`**, disponible
   tanto en **dev** (`docker/docker-compose.yml`) como en el **instalador**
   (`compose_generator.py`). En `cpu` y `gpu` se añade el servicio; la **reserva
   NVIDIA** (`driver: nvidia`) solo en `gpu`. Imagen pineada `ollama/ollama`
   (versión ya fijada en el instalador).
2. **GPU = NVIDIA/CUDA por ahora.** ROCm/AMD queda como trabajo futuro (perfil
   aparte) — se documenta, no se implementa. Sin GPU/Toolkit, `cpu` es el camino.
3. **Cablear el embedder**: cuando el Ollama local esté on, inyectar
   `API_SERVER_OLLAMA_URL=http://ollama:11434` en la api-server (y donde aplique).
   El `LLM_OLLAMA_ENDPOINT` existente sigue para el provider LLM del runtime.
4. **Bootstrap del modelo** vía init one-shot `ollama-bootstrap` (opción B-A).
5. **Modelo de embeddings configurable** (opción N-B), default `nomic-embed-text`.
   Se **fija por env** y se **descubre** desde el Ollama local (opción S-C): un
   endpoint admin `GET /admin/embeddings/available-models` cruza `/api/tags` con
   una allowlist curada de embedders, filtra a los **compatibles (768)** y marca
   el activo + reachability; el panel lo muestra en read-only. Sin swap en vivo
   ni migración (cambiar de modelo con KBs existentes ⇒ Plan 12 re-embed).
6. **Instalador**: el toggle binario `gpu_enabled` evoluciona a un selector
   `ollama_mode ∈ {none, cpu, gpu}` (con `gpu` deshabilitado/avisado si la
   detección de hardware no ve GPU NVIDIA). El compose generado incluye el
   servicio + el bootstrap según el modo, y el arranque usa `--profile` acorde.
7. **Egress**: el servicio Ollama vive en `agentic-net` (con salida a internet);
   `ollama.com` ya está en la allowlist del egress-proxy. El `pull` del bootstrap
   sale por ahí. (Un futuro modo _air-gapped_ precargaría el volumen — fuera de
   alcance.)

## Detalle de implementación

- **Compose (dev + generado)** — servicio `ollama`:
  imagen pineada; `OLLAMA_HOST=0.0.0.0:11434`; volumen nombrado/`{data_root}/ollama`;
  healthcheck `ollama list`; `networks: [agentic-net]`; hardening (cap_drop,
  no-new-privileges, apparmor, logging); en modo `gpu` añade
  `deploy.resources.reservations.devices: [{driver: nvidia, count: all,
capabilities: [gpu]}]`. Servicio `ollama-bootstrap` (misma imagen, `entrypoint`
  que hace `ollama pull ${EMBEDDING_MODEL}` apuntando a `ollama`, `restart: "no"`).
- **api-server**: `config.py` añade `embedding_model` (default `nomic-embed-text`);
  `embeddings.py` toma el `model_id` de settings. El compose inyecta
  `API_SERVER_OLLAMA_URL` + `API_SERVER_EMBEDDING_MODEL` cuando el modo ≠ `none`.
  Nuevo módulo `ingestion/embedding_models.py` con la **allowlist curada**
  (nombre→dims) + helper de compatibilidad (dim == `CHUNK_EMBEDDING_DIM`) y un
  `fetch_installed_embedding_models()` que sondea `{ollama_url}/api/tags`. Router
  admin `GET /admin/embeddings/available-models` (System Admin) que devuelve
  `{ollama_reachable, active_model, required_dim, installed[], recommended[]}`.
- **Instalador**: `ResourceConfig.gpuEnabled` → `ollamaMode` (`none|cpu|gpu`) en
  `lib/config.ts` + paso Resources; `compose_generator.selected_services` y
  `_ollama_service` parametrizan CPU vs GPU + emiten el bootstrap; detección de
  GPU (task_15_02) condiciona la opción `gpu`. Backwards-compat: mapear el
  `gpu_enabled` previo a `ollama_mode=gpu`.
- **Docs**: este ADR + runbook en `docs/06-runbooks/` (setup GPU en Windows/WSL2 +
  NVIDIA Container Toolkit; verificación; troubleshooting de embeddings) + nota en
  `docs/03-guides/gotchas/` del naming del modelo.
- **Tests**: unit del `compose_generator` (modos none/cpu/gpu emiten lo esperado:
  presencia del servicio, reserva NVIDIA solo en gpu, bootstrap, env del embedder);
  unit del embedder con `embedding_model` configurable; smoke opcional.

## Consecuencias

- ✅ Embeddings locales funcionan en dev y en instalaciones nuevas sin instalar
  nada en el host; semántica + BM25 completos.
- ✅ La GPU es opcional y aprovechable (LLMs locales) sin ser requisito.
- ✅ El naming deja de morder; cambiar de modelo de embeddings es un env var.
- ⚠️ **Windows/GPU**: la reserva NVIDIA exige Docker Desktop + WSL2 + NVIDIA
  Container Toolkit. Sin eso, `cpu` (o Ollama externo/cloud) es el camino — se
  documenta en el runbook.
- ⚠️ **Recursos**: el contenedor Ollama consume RAM/disco (modelo ~270MB +
  runtime). Por eso el modo `none` existe (Ollama externo/cloud o solo BM25).
- ⚠️ **ROCm/AMD** y **air-gapped** quedan como trabajo futuro documentado.
- ↩️ Reversible: con `ollama_mode=none` el stack vuelve al estado actual (sin
  servicio); el embedder cae a su comportamiento no-fatal (BM25).

## Plan de implementación (resumen)

1. `config.py` + `embeddings.py`: `embedding_model` configurable (default real).
   1b. `ingestion/embedding_models.py` (allowlist curada + compat 768 + sonda
   `/api/tags`) + router admin `GET /admin/embeddings/available-models`
   (descubrimiento, opción S-C); panel admin informativo (read-only).
2. `docker/docker-compose.yml` (+ `.dev`): servicio `ollama` (CPU) + `ollama-bootstrap`
   - volumen + wiring `API_SERVER_OLLAMA_URL`/`API_SERVER_EMBEDDING_MODEL`.
3. `compose_generator.py`: `ollama_mode` (none/cpu/gpu), reserva NVIDIA solo gpu,
   bootstrap, env del embedder; backwards-compat de `gpu_enabled`.
4. Instalador UI: selector 3-vías + detección de GPU.
5. Runbook GPU/WSL2 + gotcha del naming; tests.
