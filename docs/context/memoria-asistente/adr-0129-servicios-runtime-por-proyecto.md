---
name: adr-0129-servicios-runtime-por-proyecto
description: ADR 0129 servicios auxiliares (DB/cache/colas) + imagen runtime custom por proyecto — fases 1 y 2 COMPLETAS y DESPLEGADAS
metadata:
  node_type: memory
  type: project
  originSessionId: ed356da1-3ffb-49dc-a846-642abace2f05
  modified: 2026-07-24T19:48:19.727Z
---

ADR 0129 (`docs/05-architecture-decisions/0129-servicios-e-imagen-runtime-por-proyecto.md`, status accepted): el proyecto declara servicios de respaldo + env + imagen de runtime custom en `Project.repository_config` (columna JSONB ya existente; sin migración nueva).

**Fase 1 (2026-07-24, ya commiteada antes: `dc1d99ae`)**: módulo puro `workers/runtime_services.py` `build_project_runtime_services(repository_config) -> ProjectRuntimeServices(aux_services, main_env, runtime_image)`. `SERVICE_CATALOG` allowlist (mysql:8/mariadb:11/postgres:16-alpine/redis:7-alpine/beanstalkd) + servicio de imagen arbitraria (`{image,alias,env}`); deriva connection-env (`DATABASE_URL`/`REDIS_URL`/`MYSQL_HOST`…; creds fijas `app/app/app`); el `env` del proyecto pisa lo derivado; validación (tipos/alias `[a-z][a-z0-9-]*`/env-key `[A-Z][A-Z0-9_]*`/image-ref/máx 8/dup) → `RuntimeServicesConfigError`. `TestRuntimeSpec` gana `main_env`, inyectado en `_build_test_kwargs` (nunca pisa HOME). Cableado en `stack_exec_task` + `test_runtime_task` (leen `project.repository_config`); override de imagen `runtime_image` vía `dataclasses.replace(template, docker_image=...)`.

**Fase 2 (2026-07-24, `ddfb3c82`)**: el review/preview monta los servicios. `_spawn_review_runtime` (review_runtime_task.py) crea un **bridge INTERNO per-sesión** (`review-aux-{sid}`, aislado — NUNCA en `agentic-agents` compartido, evita fuga cross-tenant), lanza aux con `build_aux_run_kwargs` relabelados a la sesión, conecta el main a ambas redes e inyecta `main_env`. Config inválida → main-only (nunca deja huérfana la review). Teardown por reapers: aux+bridge con labels `component=review-runtime` + `review-session-id`; `expire_review_runtimes` reap por `container_ids`, `orphan_reaper._reap_empty_networks` (multi-filtro con dedup) barre el bridge vacío. `review_autostart` hila `repository_config` en la request. **El override `runtime_image` NO aplica al review** (usa `main_image`, la imagen de app del proyecto); solo a stack_exec/tests. UI: sección «Servicios e imagen de runtime» en el hub de proyecto (`components/projects/runtime-services-section.tsx`, montada en `app/admin/projects/[id]/page.tsx`) — servicios catálogo/imagen + env + `runtime_image`, validación cliente espejo; quita `last_git_sync`/`review_image` del PUT (el server los conserva de BD).

**DESPLEGADO Y VERIFICADO VIVO 2026-07-24**: 4 imágenes rebuild (api-server:manuals, orchestrator:manuals, workers:ci, admin-panel:manuals) + recreate de 6 servicios (api-server/orchestrator/workers/workers-aux/workers-backup/admin-panel), 6/6 healthy; smoke: workers importa runtime_services+review-aux+filtros y traduce mysql/redis OK, api-server tiene repository_config en review_autostart, bundle admin-panel contiene la sección. TDD: `tests/unit/test_review_aux_services.py` (4), `test_runtime_services.py` (18), `test_orphan_container_reaper.py` (+bridge review). Rama `plan/runs-visor-trabajo`.

**PENDIENTE (diferido/gated)**: validación de procedencia/escaneo de la imagen custom (misma postura que `review_image` ADR 0063 — sin escaneo, defensa = envelope de aislamiento); tope de recursos agregado por proyecto (caps por sidecar ya aplican). **EMPUJADO a origin** 2026-07-24: fase 1 `dc1d99ae` + fase 2 `ddfb3c82` (junto con ADR 0130 y el fix review-task, rama hasta `3a886a47`). Relacionado: [[stack-exec-feature]], [[reviewer-ciego-convergencia-fix]], [[sesion-fixes-ws-draft-stackexec-2026-07-23]].
