---
title: "Remediación auditoría 2026-07-03 — Fase 3: decisiones ratificadas + guardas"
date: 2026-07-06
plan_ids:
  - ciclo-vida-planes-fixes
  - guardas-research-por-novedad
status: released
---

# Remediación auditoría 2026-07-03 — Fase 3

Tercer tramo de la remediación de la auditoría de plataforma 2026-07-03, sobre la rama
`plan/runs-visor-trabajo`. Cubre las **guardas de research** (ADR 0103) y las **cuatro
decisiones de producto** que se ratificaron con el operador vía un workflow de análisis
multi-agente (4 briefs), más su implementación completa (backend + frontend + tests).

## Guardas de research del runtime (ADR 0103)

Recalibración de las restricciones que castigaban la exploración legítima (síntoma
«produce output»). 5 ajustes SAFE + 1 quirúrgico, desplegados en `agent-runtime:v1`:

- **G2** — decay del contador per-target tras un turno productivo (el bucle TDD legítimo
  ya no dispara el nudge).
- **G3b** — un error de PLATAFORMA (tool sin executor, `command not allowed`, worktree
  vacío) no suma esterilidad ni churn: no es culpa del agente.
- **G4a** — `search_code` cuenta como research (gana novedad, resetea racha).
- **G5** — resumen del visor por variante de nudge (ya no hardcodea «stop researching,
  produce output» para todas).
- **G10** — digest de lecturas a 300 chars (de 100).
- **G8-B** — reset quirúrgico del `LoopDetector` cuando la acción productiva difiere de la
  anterior (preserva el pin del ADR 0089 de churn de escrituras idénticas).

**G9-B** (cache de lecturas) y **G1** (rebaja del deny-by-default) se descartaron tras el
análisis. La dirección de **g1 a alcance total** (motor de guardrails en los 4 hooks con
enforce) se aprobó pero es **prod-03** (~4 semanas, rollout gated con 1 semana en LOG).

## c1/T2 — el Kanban encamina el estado por la máquina de estados

`PUT /tasks` seteaba `.status` en crudo; un drag&drop `backlog→done` falso pasaba y el
trigger `trg_compute_task_ready` lo amplificaba promoviendo dependientes. Ahora la
transición se valida con `allowed_transitions` **tras** el guard DAG: transición ilegal →
**409** (distinto del 422 DAG). Un `tenant_admin` puede forzar (`force=true`) una
transición ilegal EXCEPTO hacia `done`. El Kanban del admin-panel traduce el 409 a un
mensaje en español. Solo `PUT`; `POST create_task` sigue permisivo.

## T7c/c3 — desbloqueo de tareas y planes

El escalado `plan→blocked` + la notificación ya existían; faltaba la ACCIÓN de desbloqueo:

- Acción humana **`retry`**: mueve la tarea blocked a `ready` (o `backlog` si una
  dependencia sigue pendiente) + resetea `retry_count=0` + reactiva el plan
  `blocked→in_progress` + evento de re-dispatch.
- Endpoint **`POST /plans/{id}/unblock`**: reactiva el plan y re-encola TODAS sus tareas
  blocked en un gesto.
- Botones «Reintentar» (por tarea) y «Desbloquear plan» en el panel de escaladas.

## c8/T11 — board gerencial de planes reales

El board pintaba PROYECTOS como planes (placeholder de ADR 0008). Ahora:

- Endpoint **`GET /plans`** tenant-wide (RLS, filtros `?project_id`/`?status`,
  paginación).
- `board/page.tsx` reescrito: Kanban de PLANES reales por estado; seleccionar un plan →
  sus tareas filtradas por `plan_id` (§6: nunca un Kanban plano que mezcla planes).

## Pendiente (fuera de esta fase)

- **c4** (changelog/docs automáticos al cierre de plan) — feature del agente Technical
  Writer que escribe+commitea al repo del proyecto; scope propio.
- **T4** (guard-test estático de mutación de estado) — gated al refactor «todas las
  mutaciones vía la máquina de estados»; la opción B ratificada fue enforce solo en `PUT`.
- **tools-y-cierre T4-T8** (paridad catálogo↔executor, cablear-o-retirar, badge, docling,
  changelog) — cada uno con su punto de decisión.
- **g1 a alcance total** — prod-03.
