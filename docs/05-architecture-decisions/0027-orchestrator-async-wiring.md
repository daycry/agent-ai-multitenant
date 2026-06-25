---
adr_id: "0027"
title: "Orchestrator async wiring — sync plan_runner sigue como demo / smoke"
status: accepted
date: 2026-05-28
authors: [system_architect]
plan_referenced: 06.5-orchestrator-wiring
docs_language: es
---

# ADR 0027 — Orchestrator async wiring (sync plan_runner sigue como demo)

## Contexto

Plan 06 entregó los módulos del orchestrator (test-runtime, dep-cache,
worktrees + bare repos, runtime-pool, plan-git workflow, review-runtime,
task lifecycle, plan progress) con tests en verde y un
`apps/orchestrator/src/orchestrator/plan_runner.py` **síncrono** que los
encadena en un único proceso. Ese `plan_runner` es lo que disparan los
12 tests humanos del Plan 06 (`demo_human_06_*.py`) y los demos pueden
correrse sin Celery, sin Redis y sin Docker — para validación operativa
puramente local.

Plan 06.5 cabla esos mismos módulos en la infraestructura productiva
(Celery + DB + endpoints + beat schedule + agente reviewer real). Tras
06.5, el orchestrator productivo funciona — pero el sync `plan_runner`
sigue siendo útil. Esta ADR decide qué hacemos con él: ¿lo borramos al
cerrar 06.5 o lo conservamos?

## Decisión

**Conservamos el sync `plan_runner` indefinidamente, en paralelo a la
implementación productiva.** Los dos caminos coexisten:

| Camino                                | Cuándo se usa                                                                                       |
| ------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **Sync `plan_runner`**                | Demos `demo_human_06_*`, smoke tests post-deploy, troubleshooting offline (sin Docker / sin Redis). |
| **Async wiring (Celery + endpoints)** | Producción. El admin-panel y el orchestrator dispatcher invocan **siempre** esta vía.               |

Los dos caminos comparten **el mismo código de los módulos**
(`workers.test_runtime`, `workers.review_runtime`, `workers.plan_git`,
etc.). Solo cambia el orquestador externo: el sync runner los llama en
secuencia in-process; Celery los llama como tasks distribuidas.

## Consecuencias

### Positivas

- **Debug accesible**: cuando una regression del orchestrator pega,
  reproducirla con el sync runner es trivial (un `python
scripts/demo_human_06_a_endtoend.py`). Sin Celery, sin Redis, sin
  Docker — solo psql + venv.
- **Tests humanos siguen vivos**: los 12 `human_06_*` validados en
  Plan 06 no requieren re-cableado. Después de cada cambio invasivo en
  los módulos podemos re-ejecutarlos como gate manual.
- **Coste de mantenimiento bajo**: el sync runner es ~340 líneas de
  pegado entre módulos. No tiene lógica propia; cualquier bug que
  aparezca en él suele ser un bug en los módulos compartidos.
- **Onboarding**: un dev nuevo entiende qué hace cada módulo más rápido
  leyendo el sync runner (300 LOC lineales) que persiguiendo `apply_async`
  - beat schedules + WebSocket subscribers.

### Negativas

- **Drift posible**: el sync runner y el async wiring evolucionan en
  paralelo. Si una feature nueva se añade al async wiring sin
  reflejarse en el sync, los demos se quedan obsoletos. Mitigación:
  el CI corre los demos del Plan 06 como parte del workflow (Plan
  06.5 task_06_5_18 — esta ADR — incluye este requisito).
- **Dos puntos de entrada para audit**: si un humano quiere saber
  "qué hizo el sistema con esta tarea", tiene que mirar
  `task_audit_events` (async path) **y** los logs del sync runner si
  se ejecutó desde un demo. Mitigación: el sync runner también
  persiste audit events tras Plan 06.5 (los repos
  `task_audit_repo`/`review_session_repo` son agnósticos al
  orquestador).

### Riesgos descartados

- **"Mid-flight tasks tras restart"**: el async path los recupera porque
  cada transición persiste en `task_audit_events`/`review_sessions`. El
  sync path no necesita recovery — es in-process, si crashea el
  proceso, el demo se reinicia.
- **"Schema drift sync vs async"**: imposible — los dos usan los
  mismos modelos SQLAlchemy. No hay esquema "para el sync runner".

## Wiring async — diagrama

```
┌─────────────────┐
│  admin-panel    │ click "ejecutar plan"
└────────┬────────┘
         │ POST /projects/{id}/tasks/{id}/run (futuro)
         ▼
┌─────────────────┐
│   api-server    │ enqueue
└────────┬────────┘
         │ celery.apply_async("workers.run_test_runtime", queue="test")
         ▼
┌─────────────────┐
│ celery worker   │  test_runtime.launch() — Docker SDK,
│  queue: test    │  bridge + aux services + main container
└────────┬────────┘
         │ persist TestReport → task_audit_events
         ▼
┌─────────────────┐
│ celery worker   │  reviewer agent — receives <test-report>
│  queue: review  │  block via prompt → parses verdict
└────────┬────────┘
         │ apply_reviewer_verdict
         │   reject → task → backlog, retry_count++
         │   approve → next phase
         ▼
┌─────────────────┐
│ celery beat     │  cada 30s: idle_sweep_pools
│                 │  cada 5 min: expire_review_runtimes
│                 │  diario:    purge_dep_cache + prune_worktrees
└─────────────────┘
```

## Wiring sync (demos / smoke)

```
demo_human_06_*.py
   │
   ▼
PlanRunner.execute_task(task_id, repo, file_writer)
   │
   ├─→ pool.acquire(role) (in-process pool)
   ├─→ WorktreeManager.add + sync_to_head
   ├─→ commit_task(worktree, message, trailers)
   ├─→ push_review_to_bare(worktree)
   └─→ push_branch_to_remote()
   │
   ▼
PlanRunner.try_transition_to_review()  # in-memory plan_progress
PlanRunner.try_complete(verdict=...)   # in-memory plan_progress
```

Mismos módulos, sin Celery / Redis / Docker en el path.

## Plan de rollback

Si el async wiring del Plan 06.5 introduce una regresión seria en
producción:

1. **Desactivar beat schedule**: setear `CELERY_BEAT_SCHEDULE_ENABLED=0`
   en el worker. Las 4 maintenance tasks dejan de correr; los modulos
   in-memory siguen funcionando hasta la próxima limpieza manual.
2. **Detener workers de las colas afectadas**: `celery -A workers
control shutdown -Q test,review` deja `default` / `ingestion` /
   `privileged` corriendo (las colas `heavy`/`gpu` se retiraron — ADR 0083).
   Los planes nuevos esperan; los existentes en
   `pending_human_validation` se sirven desde `review_sessions` (la
   tabla persiste su estado).
3. **Volver a sync para los humanos**: la doc de troubleshooting
   apunta a `scripts/demo_human_06_*` como fallback para reproducir
   tareas concretas.

No es un rollback "limpio" porque las migrations de Plan 06.5 ya
están aplicadas (`review_sessions`, `task_audit_events`). Esas tablas
no rompen nada si se quedan vacías — el código que las lee tolera
filas faltantes.

## Tareas relacionadas

- Plan 06.5 task_06_5_16 / 06_5_17: spin-up real de containers en
  `workers.test_runtime` y `workers.review_runtime`. Hasta cerrar
  esas, las celery tasks `run_test_runtime` y `compose_review_runtime`
  son stubs DB-only.
- Plan 06.5 task_06_5_14 / 06_5_15: integración con el agente
  reviewer real. El parser está en `reviewer_bridge` desde Fase E; el
  hook que lo invoca tras el test-runtime es parte de las task_06_5_16.

## Referencias

- [`docs/roadmap/06.5-orchestrator-wiring.md`](../roadmap/06.5-orchestrator-wiring.md)
  — el plan completo.
- [`apps/orchestrator/src/orchestrator/plan_runner.py`](../../apps/orchestrator/src/orchestrator/plan_runner.py)
  — el sync runner que se conserva.
- [`apps/workers/src/workers/celery_app.py`](../../apps/workers/src/workers/celery_app.py)
  — el async wiring (queues + beat schedule).
- ADR 0020 — `task_awaiting_human_approval` (foundation para el
  estado escalado que el reviewer maneja).
