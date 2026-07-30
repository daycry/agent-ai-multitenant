---
title: "Remediación integral del dominio Proyecto (auditoría 2026-07-17)"
plan_id: remediacion-proyecto-integral-2026-07-17
date: 2026-07-18
status: pending_human_validation
---

# Remediación integral del dominio Proyecto

Implementación completa (15/15 tareas, fases A–F) del plan
`docs/roadmap/remediacion-proyecto-integral-2026-07-17.md`, nacido de la
auditoría integral del dominio Proyecto
(`docs/roadmap/auditoria-proyecto-integral-2026-07-17.md`, 42 hallazgos).
Método: TDD estricto (RED verificado → GREEN → commit atómico) con regresión
enfocada por fase. 17 commits.

## Fase A — Conocimiento vivo (G-01/G-02/G-03)

- **Ingesta KB resucitada** (`25cd4315`): el cliente docling llamaba a
  `/v1/convert`, retirado de docling-serve — TODA la ingesta moría con 404.
  Ahora `POST /v1/chunk/hybrid/file` (multipart + `target_type=inbody`),
  parser del `ChunkDocumentResponse` real. Verificado vivo contra el
  contenedor.
- **Catálogo builtin garantizado** (`caef96d3`): red de seguridad al arranque
  del api-server (`ensure_builtin_catalog`, advisory lock) — 0 KBs builtin
  sembradas era el estado vivo del stack.
- **GC físico de conocimiento** (`7ab5a49e`): beat diario
  `collect_knowledge_garbage` (purga documentos soft-borrados vencidos +
  blobs MinIO huérfanos); borrar una KB cascada el soft-delete a sus
  documentos.

## Fase B — Ciclo de vida de planes y tareas sin puertas laterales

- **Gates de plan** (`9f333533`): `POST /plans` solo nace
  draft/pending_approval; `PUT /plans` rechaza transiciones privilegiadas
  (approved/pending_second_approval/completed van por sus endpoints con gate)
  y `pending_human_validation` exige todas las tareas terminales.
- **Contratos de tarea** (`03cad7cc`): status inicial solo backlog/ready;
  `plan_id` visible por RLS y del MISMO proyecto; free-task rechaza plan
  cerrado; `DELETE /plans` cancela tareas+runs; el beat de promoción filtra
  planes soft-borrados.
- **DAG sin agujeros** (`1c1aa758`): ciclos construidos en dos PUT detectados
  (`assert_acyclic_with_override` sobre el grafo del proyecto); deps
  cross-plan rechazadas (422); plan sin tareas no se aprueba ni arranca;
  accept-corrections valida el DAG del spec resultante.

## Fase C — Controles de proyecto reales

- **paused/archived con efecto** (`b83716f3`): guard `project_not_active` en
  POST /plans, POST /tasks, start-execution y chat; máquina de estados
  (active↔paused→archived, unarchive admin); archivar cancela el trabajo en
  vuelo; el dispatch y el beat de promoción no tocan proyectos no-activos.
- **Slug único por tenant** (`3e8b1056`): dedupe `-{id8}` + índice único
  parcial (migración **0114**); slugify translitera acentos (NFKD) y corta en
  frontera de palabra — dos proyectos ya no comparten bare repo.
- **Adopción server-side** (`ed13cabe`): `POST /projects` con template hereda
  TODA la forma en el servidor (equipo con fork por defecto, allowlist,
  runtime, dominios, políticas); las plantillas builtin declaran su toolchain
  (CI4 → php/composer/phpunit/spark + php-phpunit); `task_unassignable`
  notifica (con dedupe por audit-event) cuando ningún agente puede tomar una
  tarea.

## Fase D — Dispatch y datos

- **Dispatch por equipo** (`eb72dd7d`): con equipo, el pool son sus
  team_members + project_local del proyecto; preset de agente muerto se
  auto-repara (audit `assignment_preset_cleared`) en vez de dejar la tarea
  ready para siempre.
- **Integridad referencial** (`d43481be`): `sweep_fk_orphans` (FKs de
  pg_constraint + relación lógica tenant_id→organizations, transitivo)
  corre automáticamente tras cada restore per-tenant; el reconciler vigila
  hijos de tenants muertos (WARNING cada 90s).

## Fase E — Operación y board

- **Fin de los siempre-gris** (`02cb568a`): healthcheck de workers con
  `-d celery@$$HOSTNAME`; las review_sessions `suspended` expiran por TTL y
  el veredicto termina las demás sesiones activas del plan.
- **Higiene git** (`ad02789b`): poda de worktrees por ESTADO (plan cerrado
  48h / blocked se conserva / resto 30d) con `refs/rescue/{task}` para
  commits fuera de rama; `git_housekeeping` mensual (gc, locks huérfanos,
  poda de ramas plan/\* de planes cerrados con PR).
- **Boards sin truncado silencioso** (`caa5f78d`): paginación exhaustiva
  (`fetchAllPages`) en planes/tareas/deps + banner cuando se toca el tope.

## Fase F — Pulido

- **Sync/planning robustos** (`782ae932`): advisory lock por plan en
  sync-to-kanban (concurrencia sin duplicados); el re-sync cablea aristas de
  tareas preexistentes; el spec del chat con id duplicado → 422, no 500.
- **Settings honestos** (`db158e47`): `execution_budgets` y
  `guardrails_config` configurables por API; `repository_config` protegido
  (merge server-side de claves de plataforma); columnas muertas deprecated y
  fuera de las seeds; UI de `allowed_domains` en la página del proyecto.
- **Seeds/ledgers** (`e76216b4`): las seeds solo asignan tools cableadas
  (candado en CI); badge «No ejecutable» en la asignación; helpers muertos de
  repo_clone eliminados; ledgers cadena-pr (T2/T4) y tools-y-cierre
  (T5/T6/T7) al día con evidencia; docstring N-17.
- **ADR 0117 proposed** (`c668b904`): MCP por proyecto (retirar/HTTP/empaquetar),
  `task.human_validation_required` (corregir CLAUDE.md), `apps/web-app`
  (consolidar) — decisiones del operador.

## Despliegue

- Migración nueva: **0114_projects_slug_unique** (reversible; dedupe previo).
- Imágenes a reconstruir: api-server (base WITH_CLAUDE=1), workers,
  orchestrator, notification-dispatcher, admin-panel.
- Purgas one-shot post-deploy: `sweep_fk_orphans` (tenants muertos + FK),
  filas `agent_tools` de tools no cableadas, blobs MinIO huérfanos
  (`collect_knowledge_garbage`), review_sessions zombis (el sweep de expiry
  las vence solo).

## Pendiente humano

- Tests humanos del plan (`human_proy_01..04` del roadmap).
- Resoluciones del ADR 0117 (a/b/c).
