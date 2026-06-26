---
plan_id: prod-18-worktree-en-ejecucion
title: Worktree en la ejecución del agente — código persistente, commit y test-runtime real
completed_at: null
docs_language: es
---

# Plan prod-18 — Worktree en la ejecución del agente (progreso)

## Resumen

El Plan 06 construyó y testeó las bibliotecas de git-worktrees (`git_repos`,
`plan_git`), pero `conduct_execution` **no las usaba**: el agente corría en un
`/workspace` tmpfs efímero, su trabajo se perdía, nada se commiteaba y el test-runtime
no tenía qué testear (hallazgo `[critical]`; pendiente explícito de ADR 0072). Este
plan **cablea** el worktree en la ejecución real, cerrando además **prod-17 `test_01`**.

## Cambios

### Fase A — Modelo de datos (`design_01`)

- **ADR 0085** (`accepted`): slugs estables, un repo por proyecto (MVP), worktree por
  tarea, **commitea el worker** (no el sandbox — principio 2), cadencia por
  `PlanGitPolicies`, bind RW.
- `api_server.slug.slugify` (kebab, fallback `untitled`); `Project.slug` + `Plan.slug`
  (migración **0099** reversible con backfill SQL) + población en los create-paths.

### Fase B — Provisión del worktree (`provision_01`)

- `conduct_execution` resuelve el worktree de la tarea (slugs org/project/plan) y lo
  **bind-montea RW en `/workspace`** (`ContainerSpec.workspace_host_path`); antes era
  tmpfs. Solo para un run de implementador con plan+slugs (no review). Best-effort: sin
  plan/slugs o fallo de git → tmpfs (degradación segura).
- Robustez en `git_repos`: `_run_git` inyecta `safe.bareRepository=all`;
  `BareRepoManager.seed_initial_commit_if_empty` siembra un commit raíz en un bare local
  vacío (proyecto sin remoto).

### Fase C — Commit + push del output (`commit_01`)

- Tras un run `done` con worktree, el **worker** hace `commit_task` (trailers
  Plan-Id/Task-Id/Execution-Id) + `push_review_to_bare` (worktree → rama del plan en el
  bare). Árbol limpio → no-op; el push bare→remoto sigue en `open_plan_pr`.

### Fase D — Encadenar el test-runtime (`test_01`) — cierra prod-17 test_01

- Reordeno el finalize del camino implementador: **finalize → commit+push →
  run_test_runtime → transición a `in_review`**, para que el AI reviewer (prod-17),
  despachado por el evento `in_review`, encuentre el diff commiteado y el
  `<test-report>` ya persistido (antes había carrera). `_run_task_tests` filtra los
  criterios automáticos y dispara el test-runtime sobre el worktree (Docker-aware,
  best-effort).

## Tests (TDD, sin Docker)

- `test_slugify` (7) + `test_slug_columns_migration` (2: backfill + reversible).
- `test_conduct_execution_worktree` (6): provisión del worktree, worktrees disjuntos,
  commit+push con trailers, no-op en árbol limpio, threading del test-runtime + filtro.
- Reorder sin regresión: capture / task_transition / review / cancellation (16).
- mypy limpio; `git_repos` (hook) verde.

## Pendiente

- **`task_prod18_e2e_01`** (Fase E): e2e del bucle completo sobre **contenedores reales**
  (Docker) + un modelo capaz (Claude SDK). Skip-guarded (`tests/e2e/test_worktree_execution.py`,
  `E2E_INSTALL=1`), como el e2e de instalación — solo se acredita con un run GREEN en un
  runner Linux. Las piezas están cubiertas en integración; el e2e las une.
- Gotcha **`worktree-bind-dood-empty-vs-named-volume.md`** documentado (DooD + volumen
  nombrado + safe.bareRepository + bare vacío).

Por la Fase E (Docker) el plan permanece `in_progress`. El **bucle de persistencia +
commit + tests está cableado y cubierto a nivel de integración**; falta su validación e2e
con contenedores reales.
