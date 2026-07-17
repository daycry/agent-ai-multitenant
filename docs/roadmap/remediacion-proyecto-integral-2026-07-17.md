---
plan_id: remediacion-proyecto-integral-2026-07-17
title: Remediación del dominio Proyecto — conocimiento, ciclo de vida de planes, controles de proyecto y equipo
status: in_progress
blocking_plan: []
started_at: 2026-07-17
completed_at: null
estimated_duration_calendar: 1-2 semanas
estimated_effort_person_days: 11
created_by: claude-fable-5-audit-2026-07-17
docs_language: es
priority: P0
source_audit: auditoria-proyecto-integral-2026-07-17
---

# Plan de remediación — Auditoría integral del dominio Proyecto (2026-07-17)

## Cabecera

| Campo             | Valor                                                                                                           |
| ----------------- | --------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**   | `remediacion-proyecto-integral-2026-07-17`                                                                      |
| **Prioridad**     | P0 (fases A-C) · P1/P2 (fases D-F)                                                                              |
| **Bloqueado por** | Ninguno; no solapa con plan 07-14, AUD16 (cerrada), cadena-pr-plan ni tools-y-cierre (coordina con sus ledgers) |
| **Rama sugerida** | `plan/runs-visor-trabajo` (continuidad)                                                                         |
| **Método**        | TDD + commits atómicos, como la remediación AUD16                                                               |

## Resumen

Implementa el delta confirmado por
[`auditoria-proyecto-integral-2026-07-17`](./auditoria-proyecto-integral-2026-07-17.md)
(42 hallazgos: P1-xx, PROY2-xx, G-xx, PROJ-xx). No duplica planes existentes;
donde un ledger ajeno está desactualizado (cadena-pr-plan T2/T4,
tools-y-cierre T5/T7 ya hechos), la fase F lo sincroniza en vez de rehacerlo.

## Tareas

### Fase A — Resucitar el pilar de conocimiento (G-01, G-02, G-03)

#### `task_proy_a1` — Ingesta KB viva contra el docling-serve real

- [x] **Título**: `docling.py` habla con rutas que EXISTEN (`/v1/convert/file`
      o `/v1/chunk/hybrid/file`) + contract-test contra el openapi de la
      imagen pineada (1.20.0) para que un bump de docling-serve rompa en CI,
      no en producción. Re-implantación con TDD del hot-fix perdido del 06-25.
- **Tiempo**: 0,75 d · **Hallazgo**: G-01 (crítico, regresión)
- **Tests**: `pytest tests/integration -k docling` + contract-test nuevo +
  ingesta e2e de un documento real en dev.

#### `task_proy_a2` — Catálogo builtin de KBs sembrado y garantizado

- [x] **Título**: re-correr el seed de ~14 KBs builtin (+ ingesta de catálogo)
      en dev; mover el seed al arranque del api-server o al instalador
      (idempotente); smoke-check «builtin KBs > 0» en tests/docs o startup
      warning. Verificar que los `default_kb_grants` de las 6 plantillas
      vuelven a apuntar a KBs existentes.
      Hecho: red de seguridad `ensure_builtin_catalog` (tenant+categorías+KBs,
      advisory lock, idempotente) en el arranque del api-server con WARNING si
      re-siembra; corpus (embeddings) sigue en el CLI. Re-seed del corpus va
      en el deploy.
- **Tiempo**: 0,5 d · **Hallazgo**: G-02
- **Tests**: seed idempotente ×2 + SELECT de slugs concedidos por plantillas.

#### `task_proy_a3` — GC físico del conocimiento

- [ ] **Título**: beat de GC — blobs `kb/**` sin fila `documents` viva →
      borrar; documents soft-deleted > N días → purgar chunks + blob;
      `delete_kb` encola la purga. Limpiar los 8 huérfanos actuales en el
      deploy. Métrica `agentic_kb_gc_*` (patrón textfile existente).
- **Tiempo**: 1 d · **Hallazgo**: G-03
- **Tests**: unit del selector de huérfanos + integración con MinIO real.

### Fase B — Cerrar la máquina de estados de PLANES (PROY2-01/02/03, P1-07)

#### `task_proy_b1` — Nacimiento y transiciones de plan con gates reales

- [ ] **Título**: `POST /plans` restringe `status` inicial a
      `{draft, pending_approval}`; `PUT /plans/{id}`: transiciones
      privilegiadas con gate por rol (`approved` solo vía
      `require_can_approve_plan`/doble firma; `pending_human_validation` solo
      con todas las tasks done reutilizando
      `transition_to_pending_human_validation`; `completed` solo vía el camino
      de veredicto — el PUT la rechaza). Actualizar el seed demo para no
      fabricar estados imposibles (P1-09/G-05) + guard en la validación
      humana (todas done).
- **Tiempo**: 1,25 d · **Hallazgos**: PROY2-01, PROY2-02, P1-09/G-05
- **Tests**: integración de cada arista privilegiada (403/409) + seed sano.

#### `task_proy_b2` — Nacimiento de tareas dentro del contrato

- [ ] **Título**: `POST /tasks` restringe status inicial (backlog, o ready sin
      deps/plan), valida `plan_id` vía sesión tenant (visibilidad RLS) y exige
      `plan.project_id == project_id`; `create_free_task` exige plan no
      terminal. `DELETE /plans` cancela tasks+runs (reusar
      `cancel_tasks_and_executions`) y `promote_ready_plans` filtra
      `deleted_at`.
- **Tiempo**: 0,75 d · **Hallazgos**: PROY2-03, P1-06, PROY2-13, P1-07
- **Tests**: integración de los 4 contratos.

#### `task_proy_b3` — DAG sin agujeros: ciclos y cross-plan

- [ ] **Título**: detección de ciclos en `_set_dependencies` (DFS sobre las
      aristas del proyecto, 422 con el ciclo nombrado) y en
      accept-corrections (`validate_dag` sobre el spec resultante); política
      cross-plan: rechazar deps hacia tareas de otro plan (422) — si producto
      las quiere después, ADR. Gate de mínimo 1 tarea al aprobar/arrancar un
      plan (PROY2-11).
- **Tiempo**: 1 d · **Hallazgos**: PROY2-04, PROY2-05, PROY2-11
- **Tests**: unit del detector + integración (ciclo por 2 PUT, corrección
  cíclica del LLM, dep cross-plan, plan vacío).

### Fase C — Controles de proyecto reales (P1-01, P1-02, PROJ-01, P1-05)

#### `task_proy_c1` — `paused`/`archived` con efecto

- [ ] **Título**: guard `project.status == active` en dispatch, promotor DAG,
      `POST /plans`, `POST /tasks`, planning-chat y start-execution (409 con
      motivo); al archivar, cancelar tasks/runs en vuelo (reusar la cascada
      del delete sin el soft-delete). Máquina mínima de estados del proyecto
      (active↔paused→archived; archived terminal salvo unarchive admin).
- **Tiempo**: 1 d · **Hallazgo**: P1-01
- **Tests**: integración por cada camino bloqueado + archivado cancela.

#### `task_proy_c2` — Slug único por tenant

- [ ] **Título**: dedupe al crear (`-{id8}` en colisión), índice único parcial
      `(tenant_id, slug) WHERE deleted_at IS NULL` (migración con dedupe
      previo de existentes), y transliteración de tildes + corte en frontera
      de palabra (arregla también PROY2-14 en slugs de plan).
- **Tiempo**: 0,75 d · **Hallazgos**: P1-02, PROY2-14
- **Tests**: unit slugify + migración reversible + colisión → sufijo.

#### `task_proy_c3` — El camino por defecto crea proyectos operativos

- [ ] **Título**: adopción de plantilla SERVER-SIDE completa (equipo con
      `fork_team=true` por defecto, `allowed_commands`,
      `default_runtime_template`, `allowed_domains`, `human_approval_policy`,
      `worker_config` útil, `repository_config`, `model_config`) — el wizard
      pasa a ser un consumidor más; dotar a las plantillas builtin de
      allowlist+runtime coherentes con su stack (CI4 → composer/php/phpunit).
      Surfacing de «tarea sin candidatos» (evento/notificación
      `task_unassignable` reutilizando el rail de escalado) para PROJ-01/05.
- **Tiempo**: 1,5 d · **Hallazgos**: PROJ-01, P1-05, PROJ-05 (surfacing)
- **Tests**: adopción por API directa produce proyecto completo; plantilla
  CI4 → allowed_commands poblados; task sin candidatos → notificación.

### Fase D — Equipo, dispatch y datos (PROJ-03/04, G-04/P1-08)

#### `task_proy_d1` — El dispatch respeta el equipo del proyecto

- [ ] **Título**: `_candidates` restringe a `team_members` de
      `project.team_id` cuando exista (fallback actual sin equipo); preset de
      agente soft-borrado → limpiar preset + WARNING con task_audit_event
      (auto-reparación PROJ-05).
- **Tiempo**: 0,75 d · **Hallazgos**: PROJ-04, PROJ-05
- **Tests**: unit de `_candidates` con/sin equipo + preset muerto se repara.

#### `task_proy_d2` — Integridad referencial: restore y tenants muertos

- [ ] **Título**: sweep post-restore que re-valida FKs desactivadas por
      `session_replication_role` (reporta+borra huérfanos); limpiar las 10
      filas `agent_tools` vivas; purga puntual de los hijos de tenants
      inexistentes (5 proyectos, 21 agentes, 2 equipos) + check de integridad
      tenant→hijos en el reconciler (WARNING). Nota en runbook de restore.
- **Tiempo**: 0,75 d · **Hallazgos**: PROJ-03, G-04/P1-08
- **Tests**: unit del sweep + integración restore→sweep limpia.

### Fase E — Operación y board (G-06/07/08, PROY2-07/08)

#### `task_proy_e1` — Fin de los «siempre-gris»

- [ ] **Título**: healthcheck de workers ping al nodo propio
      (`-d celery@$HOSTNAME`, como el fix del dispatcher del 07-10) o timeout
      30s; expiración de `review_sessions` `suspended` (sweep las vence por
      `expires_at` y el cierre del plan las termina; el reconciler no cuenta
      `suspended` vencidas como activas) + limpiar los 2 zombis.
- **Tiempo**: 0,5 d · **Hallazgos**: G-06, PROY2-07
- **Tests**: unit del sweep de sesiones + compose config.

#### `task_proy_e2` — Higiene git programada

- [ ] **Título**: en el beat de cleanup: `git worktree prune` + gc ligero
      mensual del bare; poda por-estado de worktrees (plan cerrado → TTL 48h,
      task blocked → conservar) con ref de rescate `refs/rescue/{task}` si el
      HEAD no está contenido en la rama del plan; poda de rama `plan/*` al
      detectar PR mergeado; desbloquear el lock `initializing` huérfano.
- **Tiempo**: 1 d · **Hallazgos**: G-07, G-08
- **Tests**: unit de la política de poda + rescate en conflicto simulado.

#### `task_proy_e3` — Boards sin truncado silencioso

- [ ] **Título**: paginación real (o «cargar más» + contador total visible)
      en board de planes, board de tareas por plan y resolución de deps del
      task-detail; aviso visual cuando total > mostrado.
- **Tiempo**: 1 d · **Hallazgo**: PROY2-08
- **Tests**: vitest con >100 filas simuladas.

### Fase F — Pulido y coherencia (resto)

#### `task_proy_f1` — Robustez del sync y del planning

- [ ] **Título**: `sync_to_kanban` bajo el advisory-lock por plan (o índice
      único parcial sobre spec_id) (PROY2-09); el re-sync cablea aristas de
      tareas preexistentes (PROY2-10); `create_plan` por conversación valida
      con `PlanSpecification` y los `ValueError` de `validate_dag` → 422
      (PROY2-12).
- **Tiempo**: 1 d
- **Tests**: unit + integración de los 3 contratos.

#### `task_proy_f2` — Settings honestos

- [ ] **Título**: exponer en API+UI lo aplicado (`allowed_domains` en la
      página del proyecto, `execution_budgets` y `guardrails_config` al menos
      por API con guard admin); retirar del schema/respuesta las columnas
      muertas (`rag_knowledge_bases`, `secrets_vault_id`,
      `worker_config.recursos`) o marcarlas deprecated y dejar de sembrarlas;
      proteger `repository_config` (merge server-side de las claves de
      plataforma, P1-10).
- **Tiempo**: 1 d · **Hallazgos**: P1-03, P1-04, P1-10
- **Tests**: contrato API + PUT no pisa last_git_sync.

#### `task_proy_f3` — Ledgers y seeds sincronizados con la realidad

- [ ] **Título**: marcar hechos T2/T4 en cadena-pr-plan y T7/T5-anuncio en
      tools-y-cierre (con evidencia); limpiar helpers muertos
      (`_slugify`/`_repo_name_from_url` de repo_clone); seeds sin tools no
      cableadas (ROLE_DEFAULT_TOOLS y ci4_team sin apply-patch/search-code/
      summarize-text) + limpieza de las 55 filas asignadas; badge
      `is_runtime_wired` en la UI de asignación de tools; docstring stale de
      marketplace (N-17 lo recoge — solo si no se hizo).
- **Tiempo**: 0,75 d · **Hallazgo**: PROJ-08 + estados de ledgers
- **Tests**: tests/docs + unit de seeds.

#### `task_proy_f4` — Decisiones menores de producto (preparar, no decidir)

- [ ] **Título**: ADR breve con 3 decisiones para el operador: (a) MCP por
      proyecto — empaquetar servers vs retirar de la UI (PROJ-02); (b)
      `task.human_validation_required` — implementar el flag del principio 7
      o corregir CLAUDE.md (PROY2-06); (c) `apps/web-app` vacío — consolidar
      en admin-panel (actualizar CLAUDE.md) o plan para separarlo.
- **Tiempo**: 0,5 d
- **Tests**: lint/frontmatter de ADR.

## Gated / decisión del operador

| Ítem                                | Decisión                                            |
| ----------------------------------- | --------------------------------------------------- |
| PROJ-02 MCP por proyecto            | ADR (f4a): empaquetar imagen mcp-runners vs retirar |
| PROY2-06 validación humana por task | ADR (f4b): implementar vs retirar promesa           |
| web-app vacío                       | ADR (f4c): consolidar vs separar                    |
| P1-11 membresía por-proyecto        | producto (fuera de alcance)                         |
| G-09 re-sync git remoto             | dueño: cadena-pr-plan P5                            |

## Tests humanos del plan

```yaml
- id: human_proy_01
  description: "Conocimiento vivo e2e"
  checklist:
    - "Subir un PDF a una KB lo ingesta (chunks > 0) y rag_search lo devuelve"
    - "Un proyecto desde plantilla CI4 tiene KBs concedidas visibles y composer permitido"
- id: human_proy_02
  description: "Controles de proyecto"
  checklist:
    - "Pausar un proyecto detiene el despacho; archivarlo cancela lo en vuelo"
    - "Un tenant_member NO puede aprobar un plan; un admin sí (con doble firma si aplica)"
    - "Dos proyectos con el mismo nombre no comparten repo"
- id: human_proy_03
  description: "Board y equipo"
  checklist:
    - "Un plan de >100 tareas se ve completo (paginado) en el board"
    - "Las tareas de un proyecto con equipo solo van a agentes de ese equipo"
```

## Criterios de cierre

1. Checkboxes `[x]` solo con test automático en verde; suites globales
   (unit+ratchet, tests/docs, integración dirigida, vitest, mypy/ruff) verdes.
2. Migraciones reversibles; imágenes redeplegadas en dev y verificación viva.
3. Changelog en `docs/07-changelog/remediacion-proyecto-integral-2026-07-17.md`.
4. ADRs de f4 en `docs/05-architecture-decisions/` en estado `proposed`.
