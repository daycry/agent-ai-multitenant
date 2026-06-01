---
plan_id: 16-human-agents
title: Human Agents y Workflows Mixtos Humano-IA
status: in_progress
blocking_plan: [06-testing-revision-git, 10-asistente-personal, 11-guardrails-precios]
started_at: 2026-05-31
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 85-100
estimated_cost_human_eur: 34.000 € – 40.000 €
estimated_cost_ai_eur: 100 € – 180 €
created_by: system_architect
spec_sections_referenced: [3.1.3, 5.8, 7.2, 13.6, 17, 28.7.2]
docs_language: es
---

# Plan 16 — Human Agents y Workflows Mixtos Humano-IA

## Cabecera

| Campo                              | Valor                                                                                     |
| ---------------------------------- | ----------------------------------------------------------------------------------------- |
| **ID del Plan**                    | `16-human-agents`                                                                         |
| **Estado**                         | `in_progress` (override humano del gate blocking_plan: 10/11 en pending_human_validation) |
| **Bloqueado por**                  | `06-testing-revision-git`, `10-asistente-personal`, `11-guardrails-precios`               |
| **Tiempo estimado (calendario)**   | 4-5 semanas                                                                               |
| **Tiempo estimado (persona-días)** | 85-100                                                                                    |
| **Previsión de coste — humano**    | 34.000 € – 40.000 € (tarifa media 50 €/h)                                                 |
| **Previsión de coste — IA**        | 100 € – 180 €                                                                             |
| **Aprobador propuesto**            | System Admin                                                                              |
| **Rama git**                       | `plan/16-human-agents`                                                                    |
| **Secciones del .docx**            | [3.1.3, 5.8, 7.2, 13.6, 17, 28.7.2]                                                       |

---

## Descripción Detallada

### Resumen Ejecutivo

Introducir agentes de tipo humano en el sistema: una nueva clase de Agent (`agent_type=human`) que representa a un humano (o rol/equipo de humanos) capaz de ser asignado a tareas del plan exactamente como un agente IA. Los planes pueden mezclar tareas IA y tareas humanas en un mismo DAG, con sus propios estados, notificaciones, coste imputado, bandeja de tareas asignadas y trazabilidad auditable.

### Contexto

Hasta esta fase, todas las tareas las ejecutan agentes IA. Muchos casos reales requieren intervención humana dentro del flujo: revisión legal, decisión de marca, firma del cliente, audit de seguridad, intervención DBA en producción. Modelar estos pasos como tareas humanas dentro del plan (y no como interrupciones externas) hace que el PM agente las planifique, el DAG las orqueste, el sistema mida coste y duración real, y el dashboard refleje progreso integrado.

### Alcance

**Entra en este plan**:

- Campo `agent_type` enum (ai/human) en entidad Agent. Default ai. Migración Alembic para añadirlo.
- Campos específicos del Human Agent: `assignment_mode` (MVP: solo `specific_user`), `assigned_user_id`, `hourly_rate`, `notification_channels`, `acceptance_timeout_hours` (default 24), `escalation_target_user_id`, `expected_response_time_hours`, `expected_execution_time_hours`.
- Galería de Human Agents en la UI del tenant: ver, crear, editar, asignar a User concreto. Plantillas globales clonables ('Security Reviewer Senior', 'Brand Lead', 'DBA Senior', 'Legal Reviewer', 'UX Lead'). Forking obligatorio al tenant (no linked).
- Extensión del state machine 7.2 con transiciones: `ready → assigned_to_human`, `assigned_to_human → in_progress / blocked / assigned_to_human (reasignación)`, `in_progress → in_review`.
- Modos de revisión `human_task_review_mode` (project-level): MVP soporta `auto_approve` (default) y `peer_human_reviewer`. El modo `ai_reviewer` queda para iteración posterior.
- Bandeja personal "Tareas asignadas a mí" para cualquier User: vista de tareas activas, botones de acción, formulario de entrega con attachments y log de horas opcional, histórico de tareas pasadas con métricas personales.
- Notificaciones al user asignado por sus canales preferidos (email, asistente personal si admin, in-app).
- Acceptance timeout con escalación automática al `escalation_target_user_id` al expirar.
- Coste humano integrado: `hourly_rate * horas` se suma al coste humano del plan. Campo `Project.budget_includes_human_cost` (default false) para incluir en budget si se desea.
- Entidad `HumanWorkSession` (start_at, end_at, hours_logged, comments, output_files_attached, user_id, task_id) para trazabilidad auditable como reemplazo de `Execution` en tareas humanas.
- Extensión del Memorizer: las tareas humanas también producen MemoryEntries (el Memorizer IA destila el trabajo humano).
- Tools nuevas del asistente personal: `tenant_human_workload`, `tenant_human_assignments_pending`. Respetan RBAC del admin que pregunta.
- Integración con el chat de planning: el PM agente puede asignar tareas a Human Agents desde la galería, ve sus tiempos esperados y tarifas para estimar plan.
- Alertas en chat de planning si un Human Agent crítico (en ruta crítica del DAG) tiene carga excesiva o no disponibilidad declarada.

**Queda fuera (otras fases / iteración futura)**:

- `assignment_mode = role_queue` y `team_pool` (queueing y team-based assignment).
- `human_task_review_mode = ai_reviewer` (requiere que el reviewer IA sepa evaluar output humano; tarea no trivial).
- Calendar/availability del Human Agent (vacaciones, horario laboral): la asignación en MVP no respeta calendario.
- Integración con sistemas externos de gestión de tareas humanas (Jira, Asana, Linear) — queda fuera del MVP de Human Agents.

### Decisiones Clave

- `agent_type` añadido a la entidad Agent existente, no entidad separada. Mantiene la simetría con el resto del sistema (DAG, planning, registro auditable, etc.) y minimiza duplicación de lógica.
- En MVP solo `assignment_mode=specific_user`: simplifica drásticamente la implementación y cubre el caso de uso más común (asignar a una persona concreta). Los modos queue/pool entran cuando haya demanda real.
- Modo de revisión MVP = `auto_approve` + `peer_human_reviewer`. `ai_reviewer` se difiere porque requiere diseño cuidadoso del prompt y de los acceptance_criteria para evaluación cruzada.
- Acceptance timeout 24h por defecto: equilibrio entre dar tiempo razonable al humano y no atascar el plan.
- Las plantillas globales de Human Agent siempre se forkan al tenant: la asignación al User concreto es intrínsecamente del tenant y no puede compartirse cross-tenant.

### Riesgos Identificados

| Riesgo                                                            | Probabilidad | Impacto | Mitigación                                                                                   |
| ----------------------------------------------------------------- | ------------ | ------- | -------------------------------------------------------------------------------------------- |
| El User humano olvida la tarea asignada                           | Alta         | Medio   | Notificaciones por múltiples canales + acceptance_timeout con escalación automática.         |
| Bloqueo del plan por Human Agent crítico no disponible            | Media        | Alto    | Alertas en chat de planning durante diseño del plan; calendario en iteración futura.         |
| Inconsistencia entre coste estimado (rate \* tiempo medio) y real | Media        | Bajo    | El sistema aprende del histórico de cada User para refinar estimaciones futuras.             |
| El humano marca done con output insuficiente                      | Media        | Medio   | Modo `peer_human_reviewer` para tareas críticas; auto_approve solo para tareas tipo 'firma'. |

---

## Tareas

### Fase A — Modelo de Datos y Migración

#### `task_16_01` — Añadir `agent_type` enum a entidad Agent + migración Alembic

- [x] **Título**: Añadir `agent_type` enum (ai/human, default ai) a entidad Agent. Migración Alembic reversible. Validar que agentes existentes quedan con `agent_type=ai`.
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_16_01_a
    description: "Migración Alembic añade agent_type, los agentes existentes son ai"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/migrations/test_add_agent_type.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_02` — Tabla `human_agent_config` con campos específicos del modelo humano

- [x] **Título**: Tabla `human_agent_config` con `agent_id` (FK), `assignment_mode`, `assigned_user_id` (FK User), `hourly_rate`, `hourly_rate_currency`, `notification_channels` JSONB, `acceptance_timeout_hours`, `escalation_target_user_id` (FK User), `expected_response_time_hours`, `expected_execution_time_hours`.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_16_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_02_a
    description: "Tabla human_agent_config creada con todos los campos, constraints y FKs"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/migrations/test_human_agent_config.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_03` — Tabla `human_work_sessions` para trazabilidad de tareas humanas

- [x] **Título**: Tabla `human_work_sessions` con `task_id`, `user_id`, `start_at`, `end_at`, `hours_logged`, `comments`, `output_files_attached` JSONB, `tenant_id`. Reemplaza el rol de Executions para tareas con agent_type=human.
- **Tiempo estimado**: 5 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_16_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_03_a
    description: "Tabla human_work_sessions creada con tenant_id y RLS activado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/migrations/test_human_work_sessions.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — State Machine y Orquestación

#### `task_16_04` — Extender state machine de Task con estados de tareas humanas

- [x] **Título**: Añadir transiciones `ready → assigned_to_human`, `assigned_to_human → in_progress`, `assigned_to_human → assigned_to_human (reasignación)`, `assigned_to_human → blocked` al state machine del Servicio de Dominio. Validar que solo aplican si `assignee.agent_type=human`.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_16_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_04_a
    description: "Transiciones humanas se aceptan; tareas con agent_type=ai no pueden transicionar a assigned_to_human"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_task_states.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_05` — Orchestrator: ruta de tareas humanas (no solicita pool de runtime)

- [x] **Título**: Cuando `assignee.agent_type=human`, el orchestrator NO solicita contenedor del pool. En su lugar crea `HumanTaskAssignment` con `task_id`, `human_agent_id`, `assigned_to_user_id` (resuelto desde `human_agent_config.assigned_user_id`), `assigned_at`. Transiciona la tarea a `assigned_to_human`.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_16_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_05_a
    description: "Orchestrator no pide contenedor para tareas humanas; crea HumanTaskAssignment"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_orchestrator_human_route.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_06` — Acceptance timeout con escalación automática

- [x] **Título**: Job programado (Celery Beat, cada 10 minutos) que detecta HumanTaskAssignments con estado pendiente de aceptación cuya antigüedad supera `acceptance_timeout_hours`. Reasigna al `escalation_target_user_id`. Si el escalation target tampoco acepta dentro del mismo timeout, transiciona la tarea a `blocked` y notifica al Tenant Admin.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_16_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_06_a
    description: "Tarea humana sin aceptación tras timeout se reasigna al escalation target"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_task_escalation.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — UI: Galería de Human Agents y Bandeja Personal

#### `task_16_07` — Galería de Human Agents en panel del tenant

- [x] **Título**: Página en panel admin del tenant que lista Human Agents existentes y permite crear nuevos. Formulario con todos los campos de `human_agent_config`. Catálogo de plantillas globales con botón "clonar y forkar al tenant". _(backend pytest verde vs DB real + @pytest.mark.cross_tenant; admin-panel typecheck/lint/build verde; e2e Playwright escritos NO ejecutados — pendiente verificación humana)._
- **Tiempo estimado**: 12 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_16_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_07_a
    description: "Crear Human Agent desde UI persiste correctamente"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/human-agent-create.spec.ts"
    expected_signal: "exit_code == 0"
  - id: auto_16_07_b
    description: "Clonar plantilla global forka al tenant"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/human-agent-fork.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_08` — Bandeja personal "Tareas asignadas a mí"

- [x] **Título**: Vista en el panel principal de cada User: lista de tareas asignadas activas con estado (assigned / accepted / in*progress / in_review), proyecto, plan, deadline. Botones de acción contextual: aceptar, rechazar (con justificación), marcar como completada, escalar al admin. *(backend pytest verde vs DB real + @pytest.mark.cross*tenant; admin-panel typecheck/lint/build verde; e2e Playwright escritos NO ejecutados — pendiente verificación humana).*
- **Tiempo estimado**: 14 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_16_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_08_a
    description: "User ve sus tareas asignadas; las acciones aceptar/rechazar/completar funcionan"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/human-inbox.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_09` — Formulario de entrega con attachments y log de horas opcional

- [x] **Título**: Al marcar tarea como completada, modal con textarea de output, attachments (archivos, URLs, screenshots), y campo opcional de horas trabajadas. Crea HumanWorkSession y transiciona tarea a in*review. *(backend pytest verde vs DB real + @pytest.mark.cross*tenant; admin-panel typecheck/lint/build verde; e2e Playwright escritos NO ejecutados — pendiente verificación humana).*
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_16_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_09_a
    description: "Marcar completada con attachments y horas persiste HumanWorkSession correctamente"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/human-task-submit.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_10` — Histórico personal con métricas de performance

- [x] **Título**: Pestaña "Histórico" en la bandeja personal: tareas pasadas con tiempo medio de aceptación, tiempo medio de ejecución, % de tareas aprobadas a la primera. Las métricas alimentan estimaciones futuras del PM agente. _(backend pytest verde vs DB real + @pytest.mark.cross_tenant; admin-panel typecheck/lint/build verde; e2e Playwright escrito NO ejecutado — pendiente verificación humana)._
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_16_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_10_a
    description: "Métricas personales calculadas correctamente desde HumanWorkSessions"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_metrics.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Revisión y Costes

#### `task_16_11` — Modos de revisión `auto_approve` y `peer_human_reviewer`

- [x] **Título**: Implementar `project.human_task_review_mode` con dos modos: `auto_approve` (la tarea pasa a done al marcar completada sin paso de revisión adicional) y `peer_human_reviewer` (otro Human Agent revisa el output). _(backend pytest verde vs DB real — test_human_review_auto.py + test_human_review_peer.py, 9 tests + @pytest.mark.cross_tenant; migración 0073 reversible, single head; review.py reutiliza el state machine §7.2 + escalación §7.9 a blocked + task_blocked como el sweep de acceptance-timeout)._
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_16_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_11_a
    description: "Modo auto_approve transiciona a done sin revisión adicional"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_review_auto.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_16_11_b
    description: "Modo peer_human_reviewer asigna review a otro Human Agent; verdict approved/rejected funciona"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_review_peer.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_12` — Coste humano integrado en plan, project budget y dashboard

- [x] **Título**: Imputar coste humano (hourly*rate \* horas) al plan y al proyecto. Campo `Project.budget_includes_human_cost` (default false): si true, el coste humano suma al budget; si false, solo coste AI cuenta. Actualizar dashboard 13.7 para segmentar AI cost vs Human cost. *(backend pytest verde vs DB real — test*human_cost.py + test_human_budget_inclusion.py, 7 tests + @pytest.mark.cross_tenant; migración 0074 reversible single head; budgets/human_cost.py imputa rate\*horas→USD (FX, fallback DEFAULT_HOURLY_RATE_EUR), consumption.py pliega coste humano sólo si budget_includes_human_cost; /tenant-stats/consumption + admin-panel tenant-stats segmentan IA vs Humano; admin-panel typecheck/lint/build verde).*
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + frontend-dev
- **Dependencias**: `task_16_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_12_a
    description: "Coste humano se imputa y se segmenta correctamente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_cost.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_16_12_b
    description: "Project.budget_includes_human_cost=true hace que el budget incluya coste humano en alertas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_budget_inclusion.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Integración con Planning y Asistente Personal

#### `task_16_13` — Asignación de tareas a Human Agents desde chat de planning

- [x] **Título**: El PM agente durante el chat de planning ve los Human Agents del tenant en la galería y puede asignarles tareas igual que a agentes IA. La estimación del plan integra `expected_response_time + expected_execution_time + hourly_rate * expected_execution_time` por cada tarea humana. _(backend pytest verde vs DB real — test_planning_human_agents.py, 4 tests + @pytest.mark.cross_tenant; sin migración (sólo lectura de human_agent_config / human_task_assignments existentes); planning_context expone la galería del tenant (rate + tiempos esperados + carga + flag overloaded, RLS) y chat/cost.py añade compute_human_agent_plan_estimate (duración = response+execution; coste = rate\*execution); comportamiento IA/coste genérico humano sin cambios)._
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer + backend-dev
- **Dependencias**: `task_16_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_13_a
    description: "PM agente ve Human Agents disponibles y los asigna correctamente; estimación del plan los integra"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_planning_human_agents.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_14` — Tools del asistente personal: `tenant_human_workload`, `tenant_human_assignments_pending`

- [x] **Título**: Implementar las dos tools en el asistente personal. Respuestas a queries tipo "¿cuántas tareas tiene Fulano esta semana?", "¿qué tareas humanas están en cola sin aceptar desde hace más de 24h?". Respetan RBAC del admin. _(backend pytest verde vs DB real — test_assistant_human_tools.py, 7 tests + 2× @pytest.mark.cross_tenant; sin migración (sólo lectura de human_task_assignments / human_work_sessions / users existentes); tenant_human_workload cuenta asignaciones abiertas (pending+accepted) + sesiones de la semana ISO, resuelve al usuario sólo entre miembros del tenant del admin (RLS); tenant_human_assignments_pending lista pending_acceptance > N h (24h por defecto); ambas registradas en ASSISTANT_TOOLS + DEFAULT_ENABLED_TOOLS)._
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer + backend-dev
- **Dependencias**: `task_16_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_14_a
    description: "Las dos tools del asistente personal responden correctamente y respetan RBAC"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_human_tools.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_16_15` — Memorizer adaptado a tareas humanas

- [x] **Título**: El Memorizer IA destila también las HumanWorkSessions, no solo Executions. Genera MemoryEntries útiles para futuros planes (ej. "X decisión la tomó Fulano en este contexto y resultó en Y"). _(backend pytest verde vs DB real — test_memorizer_human.py, 8 tests + @pytest.mark.cross_tenant; migración 0075 reversible single head; memory_entries.source_human_work_session_id cita la HumanWorkSession (CHECK ck_memory_entries_single_source: Execution XOR HumanWorkSession); distil_human_work_session + should_memorize_human_session (gate task=done) reutilizan el pipeline §04.03 sin tocar distil_execution; el scope private del agente humano SÍ se atribuye al user trabajador (a diferencia del agente IA); trigger desde inbox submit auto_approve + peer-review approve vía celery_client.enqueue_memorize_human_work_session)._
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer + backend-dev
- **Dependencias**: `task_16_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_15_a
    description: "Memorizer procesa HumanWorkSessions y genera MemoryEntries con citas correctas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_memorizer_human.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase F — Documentación y Cierre

#### `task_16_16` — Documentación: ADR, guías de Human Agents, runbook, changelog

- [x] **Título**: ADR sobre el modelo agent*type, guía de creación y configuración de Human Agents en /docs/03-guides/human-agents.md, runbook de uso en /docs/06-runbooks/, entrada en changelog. *(ADR 0046 sobre el modelo agent*type ai/human + diseño Human-Agent (modos de revisión, coste, asignación, alternativas); guía docs/03-guides/human-agents.md; runbook docs/06-runbooks/human-tasks-operations.md; changelog docs/07-changelog/16-human-agents.md resumiendo Plan 16 completo (16_01..16_16, migraciones 0066-0069/0073-0075, e2e escritos-no-ejecutados, tests humanos pendientes); fila Plan 16 ya presente en docs/roadmap/README.md; sin cambios de código ni del frontmatter status — lo cierra el orquestador tras el gate full-plan).*
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_16_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_16_16_a
    description: "Documentación canónica creada en /docs"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/16-human-agents.md && test -f docs/03-guides/human-agents.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

```yaml
- id: human_16_01
  description: "Ciclo end-to-end completo de una tarea humana en un plan mixto"
  hint: "Plan con 3 tareas IA + 1 tarea humana (revisión legal). Asignar a un user real con permisos."
  checklist:
    - "Las 3 tareas IA ejecutan en su pool de runtime normal"
    - "La tarea humana NO solicita contenedor; pasa a assigned_to_human y se notifica al user"
    - "El user recibe la notificación por su canal preferido"
    - "El user acepta la tarea desde su bandeja personal y pasa a in_progress"
    - "El user marca la tarea como completada con output y horas trabajadas"
    - "El sistema crea HumanWorkSession correctamente"
    - "La tarea pasa a done (modo auto_approve) y el DAG continúa"
    - "El coste humano se imputa al plan y se ve en el dashboard 13.7 segmentado"

- id: human_16_02
  description: "Acceptance timeout y escalación automática"
  hint: "Crear Human Agent con acceptance_timeout=1h, asignar tarea, no aceptar"
  checklist:
    - "Tras 1h sin aceptar, la tarea se reasigna automáticamente al escalation_target_user_id"
    - "Llega notificación al escalation target"
    - "Si el escalation target tampoco acepta en otra 1h, la tarea pasa a blocked y se notifica al admin"

- id: human_16_03
  description: "Modo peer_human_reviewer"
  hint: "Configurar proyecto con human_task_review_mode=peer_human_reviewer y ejecutar tarea humana"
  checklist:
    - "Tras submit del primer humano, la tarea pasa a in_review y se asigna al segundo Human Agent"
    - "El reviewer ve el output completo y puede aprobar o rechazar con comentarios"
    - "Si rechaza, la tarea vuelve a backlog con los comentarios; el flujo de reintento aplica igual que en tareas IA"
    - "retry_count se incrementa correctamente; tras max_review_retries, escalación humana (sección 7.9)"

- id: human_16_04
  description: "Trazabilidad auditable de la tarea humana"
  hint: "Tras completar el ciclo, revisar la vista de detalle de la tarea humana"
  checklist:
    - "La vista de detalle muestra las HumanWorkSessions (no Executions) con horas, comments, attachments"
    - "Las reviews aparecen con verdict, reviewer_user_id, feedback_text"
    - "El registro auditable está completo y exportable como bundle JSON (sección 13.6.3)"
    - "El Memorizer genera MemoryEntries a partir de las HumanWorkSessions"

- id: human_16_05
  description: "Asistente personal: tools de Human Workload"
  hint: "Habilitar asistente, preguntar por carga de un usuario y por tareas pendientes"
  checklist:
    - "'¿Cuántas tareas tiene Fulano esta semana?' responde con número correcto"
    - "'¿Hay tareas humanas sin aceptar desde hace más de 24h?' lista correctamente"
    - "Las respuestas respetan RBAC: un admin no ve datos de proyectos a los que no tiene acceso"

- id: human_16_06
  description: "Coste humano y budget"
  hint: "Plan con tareas humanas costosas, project.budget_includes_human_cost=true"
  checklist:
    - "Coste humano se imputa correctamente (rate * horas reales)"
    - "El dashboard 13.7 segmenta AI cost vs Human cost"
    - "Si budget_includes_human_cost=true, las alertas de budget incluyen el coste humano"
    - "Al cruzar 100% con coste humano incluido, los nuevos arranques se pausan (sección 28.7.4)"
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el sistema está completo en su versión vigente. No hay siguiente plan en el roadmap actual. Las próximas mejoras (assignment_mode role_queue/team_pool, ai_reviewer mode, calendar/availability, integración con Jira/Asana/Linear) se planificarán como iteración futura mediante el propio sistema de planning del producto.
