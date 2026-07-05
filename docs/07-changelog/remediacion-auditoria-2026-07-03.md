---
plan_id: remediacion-auditoria-2026-07-03
title: Remediación de la auditoría de plataforma 2026-07-03 (fase 1 — 13 fixes)
completed_at: 2026-07-05
docs_language: es
---

# Remediación de la auditoría de plataforma 2026-07-03 — fase 1

## Resumen

Implementación de la primera tanda de correcciones de la auditoría de plataforma
2026-07-03 (`docs/roadmap/auditoria-plataforma-2026-07-03.md`), en la rama
`plan/runs-visor-trabajo`. **13 unidades implementadas, testeadas (TDD, unit +
integración) y desplegadas.** El **P0 de seguridad más agudo (g6, gate humano
fail-open) queda cerrado.** Quedan pendientes las piezas mayores (ver «Pendiente»).

## Cambios

### Seguridad (P0)

- **g6 — cierre del fail-open del gate de validación humana.** El gate del runtime
  emitía 4 categorías (`code_execution`/`file_write`/…) que NO intersectaban las 13
  canónicas de las plantillas, así que `requires_human()` siempre caía a `auto` y
  **ninguna tool sensible se detenía**, ni con el preset «Cliente Externo». Fix:
  fuente única `packages/shared-domain/approval_categories.py` importada por el seed
  y remapeo del gate del runtime a categorías canónicas + test de contrato.

### Git / cadena auto-PR

- **P1/P2 — identidad git de fuente única** (`plan_git_identity`): el auto-PR y el
  clone ya resuelven el mismo bare `{project.slug}.git` y la misma rama (desde los
  slugs persistidos, no del título). Garantía de `origin` en el bare de ejecución.
- **P7 — conflicto de rebase escalable**: `abort_code='rebase_conflict'` propio
  (antes se tragaba como `commit_failed` genérico, oculto en el panel de escaladas).
- **P6 (T4) — persistencia del PR**: migración **0102** (`plans.pr_url` / `pr_branch`
  / `pr_error`, nullable, reversible); la task `open_plan_pr` persiste el resultado
  (o el motivo del fallo) en el plan; `PlanResponse` lo expone.

### Ciclo de vida de planes / planner

- **c10** — unificación del vocabulario `PlanStatus` (StrEnum de dominio) + test de
  contrato que impide que vuelva a divergir.
- **c5** — `tenant_id` en los loads por-id del dispatch BYPASSRLS + guard-test
  estático (regla dura #1).
- **c3** — un plan estancado por tareas `blocked` escala a `blocked` (antes quedaba
  `in_progress` para siempre sin ruta de salida).
- **c11** — el planner emite `complexity` por tarea (antes todo pesaba `m`).
- **c6** — el planner emite `phases[]` (habilita el sync por fases).
- **c7** — warning cuando un rol del plan no resuelve a agente (antes slot NULL en
  silencio).

### Tools / MCP

- **g4** — el LLM ya no recibe builtins sin executor (`apply_patch`/`search_code`/
  `summarize_text`) aunque un seed los asigne — cierra el «unknown tool» del run
  019f27ff; guard de paridad catálogo↔executor.
- **g5** — `docling-mcp` (sin imagen publicable) se retira del picker de MCP.

### ADR (propuestos)

- **0098** política de push/PR y re-sync del ciclo de plan · **0099** visor de diffs
  de código + flujo de conflictos · **0100** materialización del marketplace ·
  **0101** discovery MCP en runtime. Redactados en `proposed`, pendientes de
  ratificación del operador.

## Verificación

- Suite **unit completa: 1954 tests en verde**. Integración: los ficheros de las
  áreas tocadas (planes, tasks, dispatch, approval, tools, execution, worktree) en
  verde. Todos los commits pasan el pre-commit completo (ruff/black/mypy/prettier).
- **Deploy**: migración 0102 lista para aplicar; reconstrucción de las imágenes
  `api-server`/`workers`/`orchestrator`/`agent-runtime` y recreación de contenedores
  en la ventana idle (0 runs en vuelo). Recetas: `api-server` context=raíz
  `WITH_CLAUDE=1` → `workers`/`orchestrator` FROM esa base → `agent-runtime:v1`
  `WITH_CLAUDE=1` → `alembic upgrade head` (paso explícito) → `up -d` de los
  servicios de app.

## Pendiente (fase 2)

- **cadena-pr T3** (push incremental al remoto por `branch_push_mode`), T5/T6
  (política de merge / re-sync), ficha admin-panel que muestre el PR.
- **c1** (PUT de tareas por la máquina de estados — transversal: backend + sweep de
  tests + 409 en el Kanban del frontend; decisión de estrictez).
- **c9** (turno de planning durable vía Celery), **c8** (board gerencial por
  `plan_id`).
- **guardas Fase G** (G1-G13, recalibración del runtime — síntoma «produce output»).
- **g1 / prod-03** (cablear el motor de guardrails `pre_tool`/`post_tool` — la otra
  mitad del P0; requiere su ADR de política de fallo).

## Trazabilidad

Auditoría: `docs/roadmap/auditoria-plataforma-2026-07-03.md`. Planes de remediación:
`docs/roadmap/{cadena-pr-plan, ciclo-vida-planes-fixes, tools-y-cierre-plan-fixes}.md`
y la Fase G de `docs/roadmap/guardas-research-por-novedad.md`. Rama
`plan/runs-visor-trabajo` (commits `6a078cd`…`2a18e1f`).
