---
name: prod-06-entregado
description: prod-06 (ciclo de vida de ejecución) entregado y empujado; PR
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

**2026-06-26.** Plan **prod-06** (ciclo de vida de ejecución robusto: DAG, zombis,
cancelación, budgets) — Fases A–E ENTREGADAS con TDD, commiteadas en
`plan/prod-06-ciclo-vida-ejecucion`.

- **PR abierto: #55** → base `master`, head `plan/prod-06-ciclo-vida-ejecucion`,
  estado **MERGEABLE**, 35 commits limpios sobre `origin/master`. Es un PR
  **combinado** (decisión del operador): incluye `feat/provider-llm-selection`
  (ADR 0082, selección de modelo por `provider_id`, que estaba sin pushear) +
  prod-06. La rama se mergeó con `origin/master` para quedar 0 behind.
- **Decisión del operador 2026-06-26:** colas heavy/gpu → **Opción B (recortar)**
  del [[adr-0082-provider-id-unificacion]] vecino — ver ADR 0083 `accepted`.
- **`task_prod06_dag_03`:** parte B (métrica `agentic_celery_queue_depth{queue}` +
  `agentic_tasks_by_status{status}` vía beat `workers.sample_queue_metrics` →
  textfile node-exporter) **ENTREGADA**. Parte A (cablear el AI reviewer) **DIFERIDA
  a un plan dedicado** (operador 2026-06-26, **ADR 0084 accepted Opción B**). HALLAZGO:
  la atribución "depende de ADR 0063" era una **conflación** — ADR 0063 es el
  contenedor review-runtime de PREVIEW HUMANO; el AI reviewer (`reviewer_bridge`,
  0 callers productivos) es una ejecución de agente normal. `reviewer_bridge` queda
  como biblioteca lista sin caller. Por este único ítem prod-06 sigue `in_progress`.
  El trabajo diferido se maquetó como **plan `prod-17-bucle-ai-reviewer`** y se
  EMPEZÓ (rama `plan/prod-17-bucle-ai-reviewer`, **PR #56** apilado sobre prod-06).
  Entregado: Fase A (apply_reviewer_verdict approve→done + escalado a `blocked` en
  max_retries — OJO: `in_review→awaiting_human_approval` NO es transición legal),
  Fase B (trigger in_review en dispatch → ejecución del reviewer → worker aplica el
  veredicto), Fase C consumidor (test_02: inyecta `<test-report>` leído de los audit
  events). **BLOQUEADOS:** test_01 productor (conduct_execution no monta worktree —
  `ContainerSpec.workspace_host_path` sin fijar; subsistema git-worktrees-en-ejecución,
  CLAUDE.md 4/5) y Fase D e2e (Docker real + test_01). Bucle autónomo FUNCIONAL a nivel
  de integración; falta el test-report real + e2e Docker.
- **`prod-18-worktree-en-ejecucion`** (`pending_approval`, maquetado 2026-06-26): plan
  dedicado que DESBLOQUEA prod-17 test_01. Las bibliotecas de git-worktrees
  (`git_repos`/`plan_git`, Plan 06 completed+testeadas) existen pero `conduct_execution`
  (execution.py:694) NO fija `ContainerSpec.workspace_host_path` → agente en `/workspace`
  tmpfs efímero, nada se commitea (cadena completa solo en `plan_runner.py`, demo+stub).
  prod-18 cablea el worktree en la ejecución (provisión + commit del worker + encadenar
  test-runtime); desbloquea también ADR 0063 B2 y el auto-PR con contenido (0072).
  Decisiones abiertas: granularidad worktree, FK Task→repo (no existe), columnas slug.
  **EMPEZADO** (rama `plan/prod-18-worktree-en-ejecucion`): **Fase A entregada** (3a222cb) —
  ADR 0085 (accepted: slugs estables, repo por proyecto MVP, worktree por tarea, commitea
  el WORKER no el sandbox, bind RW), `api_server.slug.slugify` (kebab), `Project.slug`+
  `Plan.slug` (nullable) + migración **0099** (backfill SQL) + create-paths. Head dev → 0099.
  **Fases A+B+C ENTREGADAS** (rama `plan/prod-18`): A (slug+migración 0099, 3a222cb), B
  (provision_01: conduct_execution monta el worktree RW en /workspace, c0acda7), C
  (commit_01: el WORKER commitea con trailers + push_review_to_bare, 6940f0b). Fixes de
  robustez en git_repos: `safe.bareRepository=all` en \_run_git + `seed_initial_commit_if_empty`
  (bare local vacío). El perfil de seguridad lo preserva `build_hardened_run_kwargs` (no se
  debilita). PENDIENTE: **Fase D** (encadenar run_test_runtime → cierra prod-17 test_01) —
  OJO: requiere reordenar el finalize de conduct_execution para que el test-runtime persista
  el report ANTES del evento in_review (si no, el reviewer despacha antes de que exista el
  report); y **Fase E** (e2e Docker + gotcha DooD). D testeable sin Docker (stub fallback).
  **ACTUALIZACIÓN 2026-06-26: prod-18 Fases A–D ENTREGADAS** (PR #57, apilado sobre prod-17):
  A (slug+0099, 3a222cb), B (provision_01: monta worktree RW, c0acda7), C (commit_01: worker
  commitea+push, 6940f0b), D (test_01: reordena finalize → commit→test-runtime→in_review,
  785cd59 — **cierra prod-17 test_01**). E parcial (gotcha DooD + esqueleto e2e skip-guarded +
  changelog, 05b4939); falta solo el run e2e Docker en runner Linux. La PILA en vuelo:
  **PR #55 (prod-06) ← PR #56 (prod-17) ← PR #57 (prod-18)** — mergear en orden a master.
- **Migraciones nuevas (reversibles):** `0097_document_enqueued_at` (lease de
  ingesta) + `0098_project_execution_budgets` (override de budgets por proyecto).
  Head dev pasa de 0096 a 0098.
- **Falta (humano/deploy):** mergear el PR #55 a master; rebuild del admin-panel
  para desplegar el botón "Cancelar ejecución" (cancel_01) — paso de DEPLOY, no
  bloquea el PR. Tests humanos del plan + cierre formal cuando dag_03 deje de
  estar diferido.

Gotcha hallado al cerrar: [[gotcha-caplog-orden-tests]] (afirmar sobre logs con
caplog es frágil por orden de tests).
