---
name: implementacion-auditoria-2026-07-04
description: Estado de la implementación autónoma de la remediación de la auditoría 2026-07-03 (rama plan/runs-visor-trabajo)
metadata:
  node_type: memory
  type: project
  originSessionId: 9d18fbde-26cf-4cab-95c3-c07600f935f7
---

Implementación autónoma de la remediación de la auditoría de plataforma 2026-07-03.
Rama `plan/runs-visor-trabajo`. **Mandato 2026-07-05: "continua con todo lo que queda,
al acabar TODO despliega los dockers, tests completos, commitea+pushea, y actualiza
toda la documentación"** — esto ANULA la orden previa de no-desplegar (deploy es la
FASE FINAL tras acabar el código). TDD real con `.venv/Scripts/python.exe -m pytest`
(unit **y** integración corren local: el fixture levanta una DB migrada a head).
Pre-commit corre ruff/black/mypy/prettier; mypy NO cubre `apps/orchestrator`. Imports
lazy `api_server.*` cross-paquete van al override `ignore_missing_imports` de pyproject.
Los hooks black/ruff a veces reformatean → re-add + re-commit.

**FASE 1 — HECHA + DESPLEGADA + VERIFICADA (2026-07-05):** 13 fixes (P1/P2 git
`plan_git_identity`, ADR 0098-0101, c10 PlanStatus, **g6 gate P0** categorías canónicas,
c5 tenant_id dispatch, c11, c7, g4, g5, c3, T4 `Plan.pr_url`) + migración **0102**
aplicada (SQL directo: `docker exec -i` + bump `alembic_version`; la imagen NO lleva
alembic.ini) + rebuild api-server/workers/orchestrator/agent-runtime + recreate. 6
contenedores healthy, /healthz 200. Changelog `docs/07-changelog/remediacion-auditoria-2026-07-03.md`.
**c1 REVERTIDO** (transversal: rompe ~17 tests + UX Kanban 409 + decisión de producto).

**FASE 2 — HECHA + DESPLEGADA + VERIFICADA (2026-07-05, 9 commits `60d1c87`..`d562637`):**
G3/r4 `has_produced` exige `ok` (b8ce01c); G6a/r1 allowlist con sed/awk/… (8c5bb11);
**g1 = P0 mitad 2 COMPLETO** — el motor de guardrails ya corre en la ejecución de agentes
(antes solo en planning): ADR 0102 proposed + seam `agent_runtime/guardrails.py`
(build_pipeline/run_hook, prompt_injection post_tool modo LOG) + `shared-guardrails` en la
imagen agent-runtime (pyproject+Dockerfile) + cableado (`act` sobre result.output + `recall`
sobre memoria → `AgentState.guardrail_events` reducer → `ExecutionResult` → envelope →
worker persiste `record_guardrail_event` RLS en SAVEPOINT best-effort). Revisión adversarial
(workflow 3 lentes) → 3 hallazgos P2 arreglados: `_load_project` dentro del SAVEPOINT,
observabilidad del fail-open, screening de `recall`. Bug clave cazado por TDD:
`triggered_outcomes` es PROPERTY no método. **G8/r7 DESCARTADO** (conteo acumulativo del
LoopDetector es intencional ADR 0089, un test lo pinea → cambiarlo pide revisar el ADR).
15 tests nuevos; suite runtime 222/222. Deploy: rebuild agent-runtime:v1 + workers:ci
(FROM api-server:manuals), recreate workers/cortex-beat, SIN migración (usa guardrail_events
de la 0052). Changelog `docs/07-changelog/remediacion-auditoria-2026-07-03-fase2.md`.

**FASE 2 CONTINUACIÓN — HECHA + DESPLEGADA + VERIFICADA (2026-07-05, commits hasta `55fa6f4`):**
**cadena-pr COMPLETO (T1-T9)**: T3 push incremental (`push_plan_branch_to_remote`), T5 retira
merge-directo de la UI, T6 endpoint `POST /projects/{id}/git/sync`+docstring `fetch_remote`, T7/T8
ya hechos, T9 e2e cierre (`test_plan_close_e2e`). **ciclo-vida**: T3/c2 (submit_verdict por
`transition_plan_status`), **T6/c9 durabilidad chat** (guarda idempotencia + `resume_pending_replies`

- hook `@app.on_event("startup")` + lock redis), **T7/c3 notificación** (evento `plan_blocked`
  notification-dispatcher + enqueue orquestador; restructure `if/else` behavior-preserving); T1/T5/T8/
  T9/T10 de fase 1. DEPLOY: rebuild api-server(c2/c9/T6)+workers:ci(T3)+orchestrator:manuals(T7)+
  admin-panel:manuals(T5), recreados, 23 healthy, ruta sync OK, hook c9 limpio. SIN migración.
  OJO: **notification_dispatcher NO está desplegado en dev** → `plan_blocked` inerte hasta desplegarlo.

**GUARDAS FASE G — HECHAS + DESPLEGADAS (ADR 0103, 2026-07-06):** 5 SAFE (G2 decay per-target, G3b
platform-error no estéril, G4a search_code research, G5 summary por variante, G10 digest 300) +
**G8-B** (reset quirúrgico del LoopDetector, ratificado opción B). Rebuild de agent-runtime:v1 hecho
(guardas vivas; per-run → sin recreate). **G9-B DESCARTADO** (ratificado; hallazgo: invalidación
segura fuerza miss en r2 + riesgo lectura stale); **G1 rechazado**. Atacan el síntoma «produce output».

**DECISIONES RATIFICADAS 2026-07-06 (workflow de análisis 4 briefs):** c1/T2=**B** (enforce +
override tenant_admin force, DAG-primero, 409, solo PUT); T7c=**A+D** (retry: tarea→ready+reset
retry_count=0+reactiva plan, mismo agente, +botón desbloqueo de plan); c8=**B** (GET /plans
tenant-wide + Kanban de planes); g1-full=**dirección aprobada** (rollout gated: 1 semana LOG→subir
locked a block; es prod-03 ~4 semanas, NO ahora).

**c1/T2 + T7c + c8 BACKEND HECHO + DESPLEGADO (2026-07-06, api-server rebuild+recreate, verificado vivo):**
c1 (6883f69: enforce por allowed_transitions tras DAG-check, 409 ilegal, override force tenant_admin
prohibido→done, solo PUT; 2 tests reparados+1 nuevo; blast radius real=2); T7c (3739e73: acción `retry`
en human-action → tarea ready/backlog + reset retry_count=0 + reactiva plan blocked→in_progress +
evento re-dispatch); c8 (0239b82: GET /plans tenant-wide RLS + filtros ?project_id/?status). Todos
api-server-side, desplegados juntos.
**FASE 3 COMPLETA + DESPLEGADA + VERIFICADA (2026-07-06, mandato "haz todo lo que queda autónomo"):**
c1/T7c/c8 cerrados **backend Y frontend**. Backend: c1 (6883f69), T7c retry (3739e73), c8 GET /plans
(0239b82), **T7c-D** POST /plans/{id}/unblock + helper `apply_task_retry` (e15121a). Frontend (admin-panel,
build verificado): c1 describeMoveError 409 en ambas Kanban, c8 board reescrito a planes reales vía /plans
(§6), T7c botones Reintentar + Desbloquear plan (c1a9c6b + fix tipos 07d1ef7). Changelog fase3-decisiones.md.
Deploy: rebuild api-server(T7c-D)+admin-panel, recreate, healthy, unblock+GET /plans vivos. tools-y-cierre
T1/T3/T9 marcados.
**PENDIENTE (features con scope/decisión propios, NO fixes rápidos):** c4 changelog auto = feature del
Technical Writer (escribe+commitea al repo del proyecto); T4 guard-test = gated al refactor "todas las
mutaciones vía state machine" (opción B fue solo PUT); tools-y-cierre T4-T8 (paridad catálogo↔executor +
cablear-o-retirar tools sin executor [decisión por-tool, toca dispatch], badge, docling, changelog); g1-full
= prod-03 (~4 sem). c6/c7/c11 planner (fidelidad).
Ver [[auditoria-runs-2026-07-02-remediacion]], ADR 0102 (g1)/0103 (guardas), docs/roadmap/{cadena-pr-plan,
ciclo-vida-planes-fixes,tools-y-cierre-plan-fixes,guardas-research-por-novedad(Fase G)}.
