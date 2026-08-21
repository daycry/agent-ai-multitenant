---
name: auditoria-proyecto-integral-2026-07-17
description: "Auditoría dominio Proyecto (42 hallazgos) + remediación ENTERA implementada y desplegada 2026-07-18 (pending_human_validation)"
metadata:
  node_type: memory
  type: project
  originSessionId: 50eee157-5b9f-4f4f-85b3-9a5c1e232a6e
---

Auditoría integral del dominio Proyecto (2026-07-17, 42 hallazgos, informe en
docs/roadmap/auditoria-proyecto-integral-2026-07-17.md) → plan
`remediacion-proyecto-integral-2026-07-17` **IMPLEMENTADO ENTERO el
2026-07-18** (15/15 tareas, fases A–F, 18 commits TDD en rama
plan/runs-visor-trabajo, changelog docs/07-changelog/remediacion-proyecto-integral.md).

**Estado: pending_human_validation.** Desplegado en dev: migración **0114**
(slug único por tenant) aplicada, 5 imágenes reconstruidas (api-server
WITH_CLAUDE=1 → workers/orchestrator/dispatcher sobre esa base, admin-panel
MSYS_NO_PATHCONV=1 con /api), stack 100% healthy — las 4 lanes celery healthy
por primera vez (G-06: ping por nodo `-d celery@$$HOSTNAME` **+ timeout 30s**;
el de 10s daba unhealthy crónico sin fallo real). Purgas one-shot hechas: 55
agent_tools de tools no cableadas, 38 huérfanos FK/tenant (sweep_fk_orphans),
8 blobs MinIO, review_sessions zombis vencidas.

Lo grande que quedó cableado: ingesta KB viva (`/v1/chunk/hybrid/file`),
catálogo builtin garantizado al arranque, GC de conocimiento diario, gates
reales de plan/tarea/DAG (puerta lateral PUT cerrada, ciclos cross-PUT,
cross-plan 422, plan vacío no arranca), paused/archived con efecto, adopción
de plantilla server-side (fork por defecto + toolchains por stack), dispatch
por equipo + preset muerto se auto-repara, task_unassignable notifica,
integridad post-restore automática, higiene git por estado con refs/rescue +
housekeeping mensual, boards paginados (fetchAllPages), sync con advisory
lock, settings honestos (execution_budgets/guardrails_config por API,
allowed_domains en UI), seeds solo con tools cableadas (candado CI).

**Pendiente del operador**: tests humanos `human_proy_01..04` del plan; y las
3 resoluciones del **ADR 0117** (a: retirar/HTTP/empaquetar MCP por proyecto;
b: corregir CLAUDE.md sobre task.human_validation_required; c: consolidar
admin-panel como frontend único y borrar apps/web-app).

**Nota**: la suite integration COMPLETA quedó corriendo al cierre de la
sesión (unit global 2307 passed; todas las regresiones enfocadas por fase
verdes) — si destapó algo, tratarlo como hotfix sobre esta base.

Relacionado: [[auditoria-dirigida-2026-07-16]] (AUD16, base previa),
[[adr-pendientes-implementar-autonomo]] (ADR 0117 entra en esa cola).
