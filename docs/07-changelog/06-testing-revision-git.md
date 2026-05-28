---
plan_id: 06-testing-revision-git
title: Testing Heterogéneo, Revisión y Ciclo Git del Plan
completed_at: 2026-05-27
docs_language: es
---

# Plan 06 — Testing Heterogéneo, Revisión y Ciclo Git del Plan

## Resumen

Cerrada la fase más crítica del MVP: el flujo completo **plan → tareas →
tests por stack → revisión humana → PR automático** queda operativo.
La plataforma ya distingue entre el container del _agent-runtime_ (donde
corre el loop LangGraph) y el del _test-runtime_ (donde corren los
tests del proyecto), con catálogo de 14 stacks, dep-cache compartido,
TestReport canónico, pool elástico de containers por plan, y el flujo
git de cuatro transiciones con tres ejes de política.

## Cambios visibles

- **Nuevo package** [`packages/shared-test-runtimes/`](../../packages/shared-test-runtimes/)
  con catálogo Python de 14 runtime templates + dep-cache + 7 parsers de
  output + TestReport canónico.
- **14 Dockerfiles** en [`docker/agent-runtimes/`](../../docker/agent-runtimes/)
  (python-pytest, node-jest, node-vitest, node-playwright, php-phpunit,
  php-pest, go-test, java-maven, java-gradle, ruby-rspec, rust-cargo,
  dotnet-test, generic-shell, generic-http).
- **CI workflow** [`.github/workflows/build-runtime-templates.yml`](../../.github/workflows/build-runtime-templates.yml)
  con build matrix de los 14 templates + smoke check `WORKDIR=/workspace`.
- **Worker modules nuevos** en [`apps/workers/src/workers/`](../../apps/workers/src/workers/):
  `test_runtime.py` (group + launch + aux + DinD opt-in), `git_repos.py`
  (bare repos + worktrees), `runtime_pool.py` (pool elástico),
  `plan_git.py` (transitions + PR), `review_runtime.py` (review session).
- **API endpoints nuevos**:
  - `POST /projects/{id}/dep-cache/invalidate` — invalidación de caché
    por runtime.
  - `task_lifecycle` module con `reject_review`, `escalate_if_exhausted`,
    `apply_human_action`, `create_task_from_checkbox`, `create_free_task`,
    `history`.
  - `plan_progress` module con `compute_plan_progress` +
    `transition_to_pending_human_validation` + `transition_to_completed`.
- **Páginas admin-panel nuevas**:
  - `/admin/projects/{id}/dep-cache` — botones de invalidación por runtime.
  - `/admin/review/{id}` — terminal + logs WS + rerun btn + checklist
    humano para la validación del plan.
  - `/admin/plans/{id}/escalated` — panel de tareas escaladas + form de
    tarea libre.

## Decisiones técnicas notables

### Worktrees en detached HEAD (Fase E)

Git rechaza dos worktrees en la misma rama, pero el plan model
necesita que varios siblings compartan la rama del plan para que
`sync_to_head` traiga los commits de tareas hermanas. La solución:
cada worktree se crea con `--detach` apuntando al sha actual de la
rama. La rama vive en el bare repo, sin estar checked-out en ningún
worktree.

### Pool elástico con configuración en cascada (Fase E2)

Tres dimensiones distintas (clarificadas con el usuario tras una
pregunta directa sobre el alcance):

- **Plan**: el pool _vive_ mientras dura el plan (un container del
  pool sirve a varios roles consecutivos sin reiniciar el proceso
  Python ni las conexiones HTTP).
- **Proyecto**: configura los parámetros (`min` / `max` /
  `idle_ttl_seconds`).
- **Tenant**: el cap absoluto (`max_runtime_pool_size_per_tenant`,
  default 20) recorta cualquier crecimiento de planes paralelos.

### Sandbox de testcontainers vía socket proxy (Fase B task_06_07)

El test container nunca recibe `/var/run/docker.sock` directamente.
Cuando un proyecto declara que sus tests usan testcontainers, el
worker spawnea un sidecar `tecnativa/docker-socket-proxy` con ACL
hardened (`EXEC=0`, `VOLUMES=0`, `CONTAINERS=1`, `IMAGES=1`) y expone
`DOCKER_HOST=tcp://docker-proxy:2375` al test container. Limita la
fuga a "vecino ruidoso" en lugar de "fuga al host".

### URLs firmadas con HMAC-SHA256 (Fase G task_06_27)

Cada sesión de review-runtime emite una URL con `exp` y `sig`. La
firma es HMAC-SHA256 sobre `session_id|exp` base64-urlsafe. El
verify usa `hmac.compare_digest` para constant-time compare. La URL
es "read-only para el adversario" — puede abrirse pero no
extenderse sin re-firmar.

### Tres ejes de política Git ortogonales (Fase F task_06_25)

Sección 12.6 del .docx: `branch_push_mode` (incremental/final_only)
× `plan_validation_mode` (human_required/auto_approve) ×
`push_policy` (forbidden/branch_only_pr_required/
direct_to_default_allowed). Las 12 combinaciones tienen comportamiento
bien definido y testeado con un test parametrizado por matriz.

### Append-only audit log (Fase G2 task_06_34b6)

`InMemoryTaskStore` rechaza transiciones desde estados terminales
(`done` / `cancelled`). Los eventos `AuditEvent` son frozen
dataclasses — una vez emitido, su contenido es inmutable.

## Tests

- **Backend Python**: 200+ tests nuevos (unit + integration).
- **Playwright e2e**: 10 specs nuevos en admin-panel (dep-cache,
  review-terminal/logs/rerun/checklist, escalated-tasks-panel,
  add-free-task, kanban-plans-progress).
- **CI workflow nuevo**: builds los 14 runtime templates en matrix.

## Roadmap actualizado

[`docs/roadmap/06-testing-revision-git.md`](../roadmap/06-testing-revision-git.md):
50 tareas marcadas `[x]`, frontmatter movido a `status: completed`,
`completed_at: 2026-05-27`. Próximo plan en el árbol de dependencias:
**Plan 07 — Documentación y Visor**.
