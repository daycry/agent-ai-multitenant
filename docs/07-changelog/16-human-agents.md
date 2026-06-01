---
plan_id: 16-human-agents
title: Human Agents y Workflows Mixtos Humano-IA
completed_at: null
docs_language: es
---

# Plan 16 — Human Agents y Workflows Mixtos Humano-IA

## Resumen

Introduce una nueva clase de agente —el **Human Agent** (`agent_type=human`)—
que representa a una persona (o rol) **asignable a tareas del plan exactamente
igual que un agente IA**. Un mismo DAG puede ahora mezclar tareas IA y tareas
humanas, cada una con sus estados, notificaciones, coste imputado, bandeja
personal de tareas asignadas, modos de revisión y trazabilidad auditable. El
PM agente planifica las tareas humanas (ve tarifas y tiempos esperados), el
orquestador las enruta **sin pedir contenedor**, el sistema imputa coste humano
en **USD canónico** y el dashboard 13.7 lo segmenta frente al coste IA. La
decisión de modelo está fijada en el **ADR 0046**.

> **Construido bajo override humano del operador** del gate `blocking_plan`
> (10/11 en `pending_human_validation`). El frontmatter del plan lo cierra el
> orquestador tras la verificación full-plan; esta entrada documenta lo
> implementado.

## Cambios por tarea

### Fase A — Modelo de datos y migración

- ✅ **`task_16_01`** — **`agent_type` enum en `Agent`**. Columna
  `agent_type ∈ {ai, human}` (`TEXT NOT NULL DEFAULT 'ai'` + CHECK
  `ck_agents_agent_type`). Migración **0066**; los agentes existentes quedan
  `ai`. Test: `tests/migrations/test_add_agent_type.py`.
- ✅ **`task_16_02`** — **Tabla `human_agent_config`** (1:1 → `agents`):
  `assignment_mode` (MVP `specific_user`), `assigned_user_id` (FK User),
  `hourly_rate` + `hourly_rate_currency`, `notification_channels` JSONB,
  `acceptance_timeout_hours` (default 24), `escalation_target_user_id`,
  `expected_response_time_hours`, `expected_execution_time_hours`. Migración
  **0067**. Test: `tests/migrations/test_human_agent_config.py`.
- ✅ **`task_16_03`** — **Tabla `human_work_sessions`** (`task_id`, `user_id`,
  `start_at`, `end_at`, `hours_logged`, `comments`, `output_files_attached`
  JSONB, `tenant_id`, RLS) — el **reemplazo de `Execution`** para tareas
  humanas. Migración **0068**. Test:
  `tests/migrations/test_human_work_sessions.py`.

### Fase B — State machine y orquestación

- ✅ **`task_16_04`** — **State machine extendido** (`task_state_machine.py`):
  `ready → assigned_to_human`, `assigned_to_human → in_progress / blocked /
assigned_to_human (reasignación)`, `in_progress → in_review`. Las
  transiciones humanas están **gated por `agent_type`**: una tarea IA no puede
  ir a `assigned_to_human`. Test:
  `tests/integration/test_human_task_states.py`.
- ✅ **`task_16_05`** — **Ruta humana del orquestador** (`orchestrator/
dispatch.py`): con `assignee.agent_type=human` **no** se pide contenedor del
  pool; se crea `HumanTaskAssignment` (`task_id`, `human_agent_id`,
  `assigned_to_user_id` resuelto desde `human_agent_config.assigned_user_id`,
  `assigned_at`) y la tarea va a `assigned_to_human`. Migración **0069**
  (`human_task_assignments` + `HumanTaskAssignmentStatus`). Test:
  `tests/integration/test_orchestrator_human_route.py`.
- ✅ **`task_16_06`** — **Acceptance timeout + escalación** (Celery Beat 10 min,
  `apps/workers/src/workers/human_escalation.py`): asignación sin aceptar tras
  `acceptance_timeout_hours` → reasigna al `escalation_target_user_id`; doble
  timeout → tarea a `blocked` + aviso al Tenant Admin. Test:
  `tests/integration/test_human_task_escalation.py`.

### Fase C — UI: galería y bandeja personal

- ✅ **`task_16_07`** — **Galería de Human Agents** (panel del tenant): listar
  y crear, formulario con todos los campos de `human_agent_config`, catálogo
  de plantillas globales con **"clonar y forkar al tenant"**. Backend
  `routers/human_agents.py` + seed `seeds/human_agent_templates.py`. Backend
  pytest vs DB real + `@pytest.mark.cross_tenant`; admin-panel
  typecheck/lint/build verde. e2e Playwright `human-agent-create.spec.ts` /
  `human-agent-fork.spec.ts` **escritos, NO ejecutados** (sin navegador en CI).
- ✅ **`task_16_08`** — **Bandeja personal "Tareas asignadas a mí"**: tareas
  activas (estado / proyecto / plan / deadline) con aceptar / rechazar (con
  justificación) / completar / escalar. Backend `routers/human_inbox.py`. e2e
  `human-inbox.spec.ts` escrito, no ejecutado.
- ✅ **`task_16_09`** — **Formulario de entrega**: textarea de output,
  attachments (archivos / URLs / screenshots), horas opcionales → crea
  `HumanWorkSession` y transiciona a `in_review`. e2e `human-task-submit.spec.ts`
  escrito, no ejecutado.
- ✅ **`task_16_10`** — **Histórico personal + métricas**: tiempo medio de
  aceptación, tiempo medio de ejecución, % aprobadas a la primera
  (`db/human_metrics.py`); alimentan las estimaciones del PM agente. Test:
  `tests/integration/test_human_metrics.py`.

### Fase D — Revisión y costes

- ✅ **`task_16_11`** — **Modos de revisión** (`human_agents/review.py`):
  `auto_approve` (default — la entrega lleva a `done`) y `peer_human_reviewer`
  (2.º Human Agent revisa → `done` / rechazo a `backlog` con `retry_count++`,
  y al agotar `max_retries` la infra §7.9 aparca en `blocked`). Migración
  **0073** (`projects.human_task_review_mode`). Tests:
  `test_human_review_auto.py` + `test_human_review_peer.py` (9 tests +
  `@pytest.mark.cross_tenant`).
- ✅ **`task_16_12`** — **Coste humano integrado** (`budgets/human_cost.py`):
  `horas * tarifa` → USD canónico (FX como coste IA, fallback
  `DEFAULT_HOURLY_RATE_EUR`). `Project.budget_includes_human_cost` (default
  false) decide si suma al budget; `consumption.py` lo pliega sólo si el flag
  está activo. `/tenant-stats/consumption` + dashboard 13.7 segmentan IA vs
  Humano. Migración **0074**. Tests: `test_human_cost.py` +
  `test_human_budget_inclusion.py` (7 tests + `@pytest.mark.cross_tenant`);
  admin-panel verde.

### Fase E — Integración con planning y asistente personal

- ✅ **`task_16_13`** — **Asignación desde el chat de planning**
  (`chat/planning_context.py`): el PM agente ve la galería del tenant (tarifa
  - tiempos esperados + carga + flag `overloaded`, RLS) y asigna tareas a
    Human Agents igual que a IA. `chat/cost.py` añade
    `compute_human_agent_plan_estimate` (duración = response + execution; coste =
    rate \* execution). Sin migración (sólo lectura). Test:
    `tests/integration/test_planning_human_agents.py` (4 tests +
    `@pytest.mark.cross_tenant`).
- ✅ **`task_16_14`** — **Tools del asistente personal** (`assistant/tools.py`):
  `tenant_human_workload` (asignaciones abiertas + sesiones de la semana ISO,
  resuelve el user sólo entre miembros del tenant del admin) y
  `tenant_human_assignments_pending` (`pending_acceptance` > N h, 24h por
  defecto). Respetan el RBAC del admin; registradas en `ASSISTANT_TOOLS` +
  `DEFAULT_ENABLED_TOOLS`. Sin migración. Test:
  `tests/integration/test_assistant_human_tools.py` (7 tests + 2×
  `@pytest.mark.cross_tenant`).
- ✅ **`task_16_15`** — **Memorizer adaptado** (`memorizer/distillation.py`):
  `distil_human_work_session` + gate `should_memorize_human_session`
  (`task=done`) reutilizan el pipeline §04.03 sin tocar `distil_execution`. El
  scope `private` del agente humano **se atribuye al user trabajador** (a
  diferencia del IA). Migración **0075**
  (`memory_entries.source_human_work_session_id` + CHECK
  `ck_memory_entries_single_source`: Execution **XOR** HumanWorkSession).
  Disparo desde el submit del inbox (auto_approve) y desde el approve del
  peer-review vía `celery_client.enqueue_memorize_human_work_session`. Test:
  `tests/integration/test_memorizer_human.py` (8 tests +
  `@pytest.mark.cross_tenant`).

### Fase F — Documentación y cierre

- ✅ **`task_16_16`** — **Docs: ADR, guía, runbook, changelog** (esta entrada).
  Creados: **ADR 0046** (modelo `agent_type` + diseño del Human Agent: modos
  de revisión, coste, asignación, alternativas), la guía
  `docs/03-guides/human-agents.md` (crear/configurar, asignar en planning,
  bandeja personal, modos de revisión, coste/budget), el runbook
  `docs/06-runbooks/human-tasks-operations.md` (aceptación, escalación, peer
  review, trazabilidad, checklist de incidencias) y este changelog. La fila del
  Plan 16 ya está en `docs/roadmap/README.md`.

## Migraciones del plan (todas reversibles, single head)

| Revisión | Contenido                                                                                               |
| -------- | ------------------------------------------------------------------------------------------------------- |
| **0066** | `agents.agent_type` (`TEXT NOT NULL DEFAULT 'ai'` + CHECK `ck_agents_agent_type`)                       |
| **0067** | `human_agent_config` (1:1 → agents; rate/moneda, canales, timeout, escalation, tiempos esperados)       |
| **0068** | `human_work_sessions` (trazabilidad humana, RLS por tenant — reemplaza Execution)                       |
| **0069** | `human_task_assignments` (+ `HumanTaskAssignmentStatus`, RLS por tenant)                                |
| **0073** | `projects.human_task_review_mode` (`auto_approve` / `peer_human_reviewer`)                              |
| **0074** | `projects.budget_includes_human_cost` (default false)                                                   |
| **0075** | `memory_entries.source_human_work_session_id` + CHECK `ck_memory_entries_single_source` (Execution XOR) |

> Las revisiones **0070-0072** NO pertenecen a este plan (Plan 11.2:
> `llm_providers`, `model_prices.provider_id`; Plan 06.16:
> `projects_command_config`). La cadena de Plan 16 intercala **0066-0069**
> (fases A/B) y **0073-0075** (fases D/E), cada una probada up/down/up sobre la
> single head.

## Endpoints / tools nuevos

| Recurso                                             | Rol / actor         | Notas                                              |
| --------------------------------------------------- | ------------------- | -------------------------------------------------- |
| Galería de Human Agents (`routers/human_agents.py`) | tenant admin        | listar / crear / clonar plantilla → fork al tenant |
| Bandeja personal (`routers/human_inbox.py`)         | usuario asignado    | aceptar / rechazar / completar / escalar / entrega |
| `tenant_human_workload` (asistente)                 | admin (RBAC propio) | carga de un user esta semana                       |
| `tenant_human_assignments_pending` (asistente)      | admin (RBAC propio) | tareas humanas sin aceptar > N h                   |

## Multi-tenancy (NON-NEGOTIABLE)

Toda fila/consulta nueva es **tenant-scoped (RLS)**: `human_agent_config`,
`human_work_sessions` y `human_task_assignments` heredan/aplican RLS por
`tenant_id`. Las plantillas globales se **forkan** al tenant (nunca linked)
porque el `assigned_user_id` es del tenant. El peer-review resuelve reviewer y
reviewer-user con predicado `tenant_id` explícito (un `reviewer_agent_id`
cross-tenant resuelve a `None`). Las tools del asistente resuelven usuarios
**sólo** entre miembros del tenant del admin que pregunta. Cobertura
`@pytest.mark.cross_tenant` en gallery, review, cost, budget, planning,
asistente y memorizer.

## Verificación

- **Backend pytest vs DB real** (PG 15432, Redis db 15) verde por tarea: ver
  la lista de tests en "Cambios por tarea" (migraciones + integración).
- `pre-commit` (black/ruff/mypy/prettier) verde por tarea.
- **admin-panel** `typecheck` / `lint` / `build` verde en las tareas de UI
  (16_07-16_12).
- Migraciones del plan reversibles (up/down/up), single head.

## Pendiente

- **e2e Playwright escritos, NO ejecutados** (sin navegador en CI —
  `human-agent-create.spec.ts`, `human-agent-fork.spec.ts`,
  `human-inbox.spec.ts`, `human-task-submit.spec.ts`). Pendientes de
  verificación humana.
- **Tests humanos del plan** (`human_16_01`..`human_16_06`): ciclo e2e de
  tarea humana en plan mixto, acceptance-timeout + escalación, peer review,
  trazabilidad auditable, tools de Human Workload del asistente, coste humano +
  budget. Pendientes de ejecutar por un humano.
- **Cierre del frontmatter del plan** (`status: completed`) y **merge del PR a
  `main`**: los gestiona el orquestador / humano tras la verificación
  full-plan. Por eso el plan NO queda `completed` en esta tarea.

## Fuera de alcance (iteración futura)

- `assignment_mode = role_queue` / `team_pool` (queueing y team-based).
- `human_task_review_mode = ai_reviewer` (reviewer IA evaluando output humano).
- Calendario / disponibilidad del Human Agent (vacaciones, horario).
- Integración con Jira / Asana / Linear.

## Referencias

- ADR: [0046 — Human Agents: agent_type y workflows mixtos](../05-architecture-decisions/0046-human-agents-agent-type-y-workflows-mixtos.md)
- Guía: [Human Agents](../03-guides/human-agents.md)
- Runbook: [Operar tareas humanas](../06-runbooks/human-tasks-operations.md)
- Roadmap: [Plan 16 — Human Agents](../roadmap/16-human-agents.md)
