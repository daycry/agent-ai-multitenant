---
plan_id: prod-06-ciclo-vida-ejecucion
title: Ciclo de vida de ejecución robusto — DAG, zombis, cancelación y budgets
completed_at: null
docs_language: es
---

# Plan prod-06 — Ciclo de vida de ejecución robusto

## Resumen

La auditoría de producción (2026-06-10) confirmó que el "happy path autónomo"
**no se sostenía sin intervención manual**: nada promovía tareas `backlog → ready`
al terminar sus dependencias, una ejecución `done`/`failed` dejaba su tarea en
`in_progress` para siempre, los modos de fallo de larga duración (SIGKILL/OOM,
eventos perdidos) no convergían a un estado terminal, no existía cancelación real,
y los budgets/auto-pausa tenían el guard lector pero ningún escritor productivo.

Este plan cierra el ciclo de vida completo **promoción DAG → dispatch → ejecución
→ transición de tarea → recuperación → cancelación → control de presupuesto**.
Entregadas las Fases A–E; quedan dos ítems con gate de decisión humana (ver
"Pendiente").

## Cambios

### Fase A — Cierre del ciclo plan→tarea

- **Transición post-ejecución** (`dag_01`): `transition_task_after_run` mueve la
  tarea al terminar el run — `done` → `in_review`/`done` (según reviewer),
  `failed`/otros → `blocked`; `awaiting_human_approval` lo gestiona su rama. Se
  publica el evento tras el commit. Guard idempotente por `in_progress`.
- **Promoción de DAG** (`dag_02`): `promote_ready_tasks` + `announce_ready_tasks`
  (api_server.dag_promotion) en `start-execution` (raíces) y un beat
  `workers.promote_ready_plans` cada 30 s como safety-net (el trigger de BD
  promueve dependientes sin publicar evento). Lock `pg_advisory_xact_lock` por plan.
- **Métrica de cola/estado** (`dag_03`, parte B): beat `workers.sample_queue_metrics`
  (cada 30 s) emite `agentic_celery_queue_depth{queue}` (Redis LLEN) +
  `agentic_tasks_by_status{status}` vía el textfile-collector de node-exporter
  (mismo patrón que `backup_metrics`). prod-08 añade el scrape + alerta + dashboard.
  La parte A de `dag_03` (cablear el AI reviewer) se DIFIRIÓ — ver "Pendiente".

### Fase B — Recuperación de fallos de larga duración

- **Sweeper de zombis** (`zombi_01`): beat `workers.sweep_stale_executions` cada
  5 min cierra ejecuciones `running` huérfanas (>7 h = cap de 6 h + 1 h de margen)
  como `failed`/`stale_after_worker_loss`, transiciona la tarea y reapa contenedores.
- **Redelivery seguro** (`zombi_02`): `task_reject_on_worker_lost` + bound task
  `run_execution` con `celery_task_id`; el guard `supersede_running_executions`
  absorbe el duplicado.
- **visibility_timeout coherente** (`zombi_03`): `broker_transport_options
.visibility_timeout = 25200 s` (7 h) y `execution_hard_time_limit_s.max_value`
  acotado a 21600 s (6 h) con validación cruzada en el registry.
- **Reconciliación de eventos** (`evento_01`): `reclaim_stale_pending` (XAUTOCLAIM)
  al arrancar el consumer del orchestrator + el beat de re-anuncio de Fase A.

### Fase C — Cancelación real

- **Cancelación cooperativa** (`cancel_01`): flag `executions.cancel_requested_at`
  - `celery_task_id` (migración 0090), poll en el worker que mata el contenedor,
    `revoke(terminate=True)`, endpoint `POST /executions/{id}/cancel` y botón en la UI.
- **Cascada plan/proyecto** (`cancel_02`): `cancel_tasks_and_executions` cancela
  tareas no-terminales + sus ejecuciones al cancelar un plan o soft-borrar un
  proyecto, con revoke de los jobs Celery.

### Fase D — Colas y schedule

- **ADR 0083 colas heavy/gpu** (`colas_01`): redactado y `accepted` — el operador
  eligió la **Opción B (recortar)** el 2026-06-26.
- **Recorte de colas heavy/gpu** (`colas_02`): `heavy`/`gpu` eliminadas de
  `QUEUE_NAMES` (eran lanes muertas en single-host); runbook 06-capacity-management,
  ADR 0027 y el compose (generator + manuals) actualizados a la topología
  `default + ingestion + test + review + privileged`.
- **`_parse_cron` ruidoso** (`beat_01`): un cron malformado ya no degrada en
  silencio al 04:00 global — en staging/prod RECHAZA el boot de beat; en dev loguea
  ERROR (nombrando la env var) y cae al default DOCUMENTADO de esa entrada.
- **Lease de encolado de ingesta** (`beat_02`): columna `documents.enqueued_at`
  (migración 0097) como lease; el sweep CLAIMA con un `UPDATE … RETURNING` atómico
  y solo re-encola documentos sin lease o con lease expirado.

### Fase E — Presupuestos operativos y proyectos borrados

- **Auto-pausa + alertas cableadas** (`budget_01`): seam `sweep_tenant_budgets` →
  beat `workers.refresh_budgets` (cada 5 min, por tenant) + hook
  `refresh_budgets_after_run` tras `finalize_execution`. Cierra el bucle
  escritor→lector que el dispatch ya leía.
- **Budgets de run configurables** (`budget_02`): columna `projects
.execution_budgets` (migración 0098) + platform setting `execution_default_budgets`;
  `resolve_execution_budgets` resuelve plataforma ← proyecto con clamp al techo del
  runtime; threadeado a `ExecutionRequest.budgets`.
- **No despachar proyectos soft-borrados** (`budget_03`): `_route_ai` carga el
  proyecto con `deleted_at IS NULL` y salta con log si está borrado.

## Migraciones

- `0097_document_enqueued_at` — lease de encolado de ingesta (reversible).
- `0098_project_execution_budgets` — override de budgets por proyecto (reversible).

(La cancelación de ejecuciones `0090` se entregó en el trabajo previo de Fase C.)

## Pendiente

- **`dag_03` parte A** (cablear el AI reviewer al flujo post-ejecución): **DIFERIDA a
  un plan dedicado** por decisión del operador (2026-06-26, **ADR 0084 Opción B**). Al
  mapear el subsistema se corrigió la atribución original: NO depende de ADR 0063 (ése
  es el contenedor review-runtime de preview humano); el AI reviewer es una ejecución
  de agente normal cuyo bucle completo (test-runtime → reviewer → veredicto + escalado)
  se diseñará aparte. `reviewer_bridge` queda como biblioteca lista sin caller. La parte
  B (métrica) ya está entregada y deja el `in_review` observable.

Por este único ítem el plan permanece en `in_progress` y NO se marca `completed`
(protocolo CLAUDE.md). El resto de Fases A–E están entregadas.

Por este único ítem el plan permanece en `in_progress` y NO se marca `completed`
(protocolo CLAUDE.md). El resto de Fases A–E están entregadas.
