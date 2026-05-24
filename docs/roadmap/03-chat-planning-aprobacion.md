---
plan_id: 03-chat-planning-aprobacion
title: Chat, Planning Multi-Agente y Aprobación
status: in_progress
blocking_plan: [02-ejecucion-agentes]
started_at: 2026-05-24
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 80-100
estimated_cost_human_eur: 32.000 € – 40.000 €
estimated_cost_ai_eur: 180 € – 280 €
created_by: system_architect
spec_sections_referenced: [8]
docs_language: es
---

# Plan 03 — Chat, Planning Multi-Agente y Aprobación

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `03-chat-planning-aprobacion`             |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `02-ejecucion-agentes`                    |
| **Tiempo estimado (calendario)**   | 4-5 semanas                               |
| **Tiempo estimado (persona-días)** | 80-100                                    |
| **Previsión de coste — humano**    | 32.000 € – 40.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 180 € – 280 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/03-chat-planning-aprobacion`        |
| **Secciones del .docx**            | [8]                                       |

---

## Descripción Detallada

### Resumen Ejecutivo

El usuario habla con el equipo en chat, los agentes generan un plan canónico, el humano lo aprueba y lo sincroniza al Kanban respetando el DAG de dependencias. Modos de chat (Planning, Discusión, Ejecución) sin pérdida de contexto.

### Contexto

Hasta ahora las tareas se crean a mano. Con esta fase nacen del chat: el PM agente coordina, otros intervienen, el resultado es un Plan persistible. El botón 'Generar Plan' aparece cuando el equipo lo considera cerrado. El humano revisa el detalle (con coste calculado) y aprueba.

### Alcance

**Entra en este plan**:

- Conversation y Message con modos (Planning, Discusión, Ejecución, custom).
- Selector de modo persistente, cambios sin pérdida de contexto.
- Multi-agente coordinado con LangGraph en modo Planning.
- Construcción de contexto enriquecido (chat + Kanban + planes anteriores + memoria + KBs + config).
- Generación del Plan estructurado siguiendo la plantilla canónica.
- Botón 'Generar Plan' contextual en chat.
- Pestaña 'Planes' del proyecto con máquina de estados completa.
- Vista de detalle del plan: cabecera, descripción, fases, tareas, grafo DAG, vista Gantt, desglose de coste.
- Cálculo de coste humano (tarifa única tenant) y coste IA (catálogo + rango de incertidumbre).
- Flujo de aprobación con doble firma opcional.
- Botón 'Sincronizar al Kanban' (total / por fase / selección custom).
- Garantías DAG: imposible lanzar tarea con dependencias pendientes.

**Queda fuera (otras fases)**:

- Tests humanos a nivel de plan (Fase 6: requiere review-runtime).
- Integración Git con plan (Fase 6).
- Validación estructural de tests humanos del plan generado (Fase 11: guardrails).

### Decisiones Clave

- Una sola Conversation con cambios de modo internos, no múltiples conversaciones por modo.
- El botón 'Generar Plan' solo aparece cuando un guardrail estructural valida que el equipo ha producido propuesta completa (este guardrail viene en Fase 11; antes, validación manual).
- Coste humano con tarifa única tenant (50 €/h default), no tabla por rol.
- Coste IA como rango (mín-máx), no número único.

### Riesgos Identificados

| Riesgo                                                              | Probabilidad | Impacto | Mitigación                                                                                                 |
| ------------------------------------------------------------------- | ------------ | ------- | ---------------------------------------------------------------------------------------------------------- |
| Conversación de planning se descontrola con muchos agentes hablando | Media        | Medio   | PM agente como portavoz único por defecto. Otros agentes solo intervienen cuando aportan valor específico. |
| Plan generado con tareas con dependencias circulares                | Media        | Alto    | Validación DAG (detección de ciclos por DFS) antes de persistir.                                           |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Modelo de Conversación

#### `task_03_01` — Modelos Conversation, Message con campos mode, attachments, related_plan_id

- [x] **Título**: Modelos Conversation, Message con campos mode, attachments, related_plan_id
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_01_a
    description: "Modelos Conversation, Message con campos mode, attachments, related_plan_id"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_conversation_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_02` — Migración Alembic + RLS

- [x] **Título**: Migración Alembic + RLS
- **Tiempo estimado**: 3 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_02_a
    description: "Migración Alembic + RLS"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_conversation_migration.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_03` — Endpoints REST /conversations y WebSocket /ws/conversation/{id}

- [x] **Título**: Endpoints REST /conversations y WebSocket /ws/conversation/{id}
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_03_a
    description: "Endpoints REST /conversations y WebSocket /ws/conversation/{id}"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_conversation_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_04` — Compresión jerárquica de mensajes antiguos (sub-agente de resumen)

- [x] **Título**: Compresión jerárquica de mensajes antiguos (sub-agente de resumen)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_03_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_04_a
    description: "Compresión jerárquica de mensajes antiguos (sub-agente de resumen)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_conversation_compression.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Modos de Chat

#### `task_03_05` — Selector de modo persistente en cabecera del chat (Planning, Discusión, Ejecución)

- [ ] **Título**: Selector de modo persistente en cabecera del chat (Planning, Discusión, Ejecución)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_05_a
    description: "Selector de modo persistente en cabecera del chat (Planning, Discusión, Ejecución)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/chat-mode-selector.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_06` — System prompts y configuración de tools por modo

- [ ] **Título**: System prompts y configuración de tools por modo
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_03_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_06_a
    description: "System prompts y configuración de tools por modo"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_chat_modes.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_07` — Cambio de modo: mensaje de sistema visible, contexto preservado, comportamiento del equipo cambia

- [ ] **Título**: Cambio de modo: mensaje de sistema visible, contexto preservado, comportamiento del equipo cambia
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_03_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_07_a
    description: "Cambio de modo: mensaje de sistema visible, contexto preservado, comportamiento del equipo cambia"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/chat-mode-change.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_08` — Modos custom configurables a nivel tenant

- [ ] **Título**: Modos custom configurables a nivel tenant
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_08_a
    description: "Modos custom configurables a nivel tenant"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_custom_chat_modes.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Multi-Agente en Modo Planning

#### `task_03_09` — Coordinación con LangGraph: PM como portavoz, otros agentes intervienen según pertinencia

- [ ] **Título**: Coordinación con LangGraph: PM como portavoz, otros agentes intervienen según pertinencia
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_09_a
    description: "Coordinación con LangGraph: PM como portavoz, otros agentes intervienen según pertinencia"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_planning_coordination.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_10` — Construcción de contexto: chat actual + Kanban estado + planes anteriores + memoria + KBs

- [ ] **Título**: Construcción de contexto: chat actual + Kanban estado + planes anteriores + memoria + KBs
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_03_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_10_a
    description: "Construcción de contexto: chat actual + Kanban estado + planes anteriores + memoria + KBs"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_planning_context.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_11` — Generación de borradores estructurados del plan en el chat con tablas y listas

- [ ] **Título**: Generación de borradores estructurados del plan en el chat con tablas y listas
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_03_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_11_a
    description: "Generación de borradores estructurados del plan en el chat con tablas y listas"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plan-draft-in-chat.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_12` — @-mentions a agentes específicos para dirigirse a uno concreto

- [ ] **Título**: @-mentions a agentes específicos para dirigirse a uno concreto
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_03_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_12_a
    description: "@-mentions a agentes específicos para dirigirse a uno concreto"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/agent-mentions.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Generación y Persistencia del Plan

#### `task_03_13` — Botón 'Generar Plan' contextual: aparece cuando el equipo ha producido propuesta estructurada

- [ ] **Título**: Botón 'Generar Plan' contextual: aparece cuando el equipo ha producido propuesta estructurada
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_13_a
    description: "Botón 'Generar Plan' contextual: aparece cuando el equipo ha producido propuesta estructurada"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/generate-plan-button.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_14` — Persistencia del Plan con plantilla canónica (cabecera + descripción + fases + tareas con tests automáticos)

- [ ] **Título**: Persistencia del Plan con plantilla canónica (cabecera + descripción + fases + tareas con tests automáticos)
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_14_a
    description: "Persistencia del Plan con plantilla canónica (cabecera + descripción + fases + tareas con tests automáticos)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_persistence.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_15` — Validación DAG al persistir (detección de ciclos)

- [ ] **Título**: Validación DAG al persistir (detección de ciclos)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_15_a
    description: "Validación DAG al persistir (detección de ciclos)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_dag_validation.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_16` — Máquina de estados del Plan completa (pending_approval, draft, approved, in_progress, blocked, pending_human_validation, completed, cancelled, rejected, archived)

- [ ] **Título**: Máquina de estados del Plan completa (pending_approval, draft, approved, in_progress, blocked, pending_human_validation, completed, cancelled, rejected, archived)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_16_a
    description: "Máquina de estados del Plan completa (pending_approval, draft, approved, in_progress, blocked, pending_human_validation, completed, cancelled, rejected, archived)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_state_machine.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Pestaña Planes y Vista de Detalle

#### `task_03_17` — Pestaña 'Planes' del proyecto con listado, filtros y badges de estado

- [ ] **Título**: Pestaña 'Planes' del proyecto con listado, filtros y badges de estado
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_17_a
    description: "Pestaña 'Planes' del proyecto con listado, filtros y badges de estado"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plans-tab.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_18` — Vista de detalle del plan con renderizado de la plantilla canónica

- [ ] **Título**: Vista de detalle del plan con renderizado de la plantilla canónica
- **Tiempo estimado**: 14 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_03_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_18_a
    description: "Vista de detalle del plan con renderizado de la plantilla canónica"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plan-detail-view.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_19` — Grafo visual del DAG de tareas (con D3.js o react-flow)

- [ ] **Título**: Grafo visual del DAG de tareas (con D3.js o react-flow)
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_03_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_19_a
    description: "Grafo visual del DAG de tareas (con D3.js o react-flow)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plan-dag-view.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_20` — Vista Gantt con duración estimada y línea crítica

- [ ] **Título**: Vista Gantt con duración estimada y línea crítica
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_03_19`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_20_a
    description: "Vista Gantt con duración estimada y línea crítica"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plan-gantt-view.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_21` — Comentarios in-line en el plan (que el equipo recoge si se refina)

- [ ] **Título**: Comentarios in-line en el plan (que el equipo recoge si se refina)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_03_20`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_21_a
    description: "Comentarios in-line en el plan (que el equipo recoge si se refina)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plan-comments.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase F — Cálculo de Coste y Aprobación

#### `task_03_22` — Cálculo de coste humano (tarifa única tenant × tiempo estimado por tarea)

- [ ] **Título**: Cálculo de coste humano (tarifa única tenant × tiempo estimado por tarea)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_22_a
    description: "Cálculo de coste humano (tarifa única tenant × tiempo estimado por tarea)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_human_cost_calc.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_23` — Cálculo de coste IA (catálogo de precios placeholder + rango por complejidad)

- [ ] **Título**: Cálculo de coste IA (catálogo de precios placeholder + rango por complejidad)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_23_a
    description: "Cálculo de coste IA (catálogo de precios placeholder + rango por complejidad)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_ai_cost_calc.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_24` — Desglose de coste tabular en la UI

- [ ] **Título**: Desglose de coste tabular en la UI
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_03_23`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_24_a
    description: "Desglose de coste tabular en la UI"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/plan-cost-breakdown.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_25` — Flujo de aprobación con doble firma opcional sobre umbral configurable

- [ ] **Título**: Flujo de aprobación con doble firma opcional sobre umbral configurable
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_24`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_25_a
    description: "Flujo de aprobación con doble firma opcional sobre umbral configurable"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_plan_approval.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_26` — Configuración de tarifa horaria del tenant en panel admin

- [ ] **Título**: Configuración de tarifa horaria del tenant en panel admin
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_03_25`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_26_a
    description: "Configuración de tarifa horaria del tenant en panel admin"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/tenant-hourly-rate.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase G — Sincronización al Kanban

#### `task_03_27` — Botón 'Sincronizar al Kanban' con opciones (total / por fase / selección custom)

- [ ] **Título**: Botón 'Sincronizar al Kanban' con opciones (total / por fase / selección custom)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_03_27_a
    description: "Botón 'Sincronizar al Kanban' con opciones (total / por fase / selección custom)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/sync-to-kanban.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_28` — Materialización de tareas en el Kanban con dependencias en task_dependencies

- [ ] **Título**: Materialización de tareas en el Kanban con dependencias en task_dependencies
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_27`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_28_a
    description: "Materialización de tareas en el Kanban con dependencias en task_dependencies"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_sync_kanban.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_29` — Idempotencia de la sincronización (no duplicar tareas si se reintenta)

- [ ] **Título**: Idempotencia de la sincronización (no duplicar tareas si se reintenta)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_28`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_29_a
    description: "Idempotencia de la sincronización (no duplicar tareas si se reintenta)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_sync_idempotency.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_30` — Garantías DAG en transiciones (rechazo 422 si dependencias no done)

- [ ] **Título**: Garantías DAG en transiciones (rechazo 422 si dependencias no done)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_03_29`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_30_a
    description: "Garantías DAG en transiciones (rechazo 422 si dependencias no done)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_dag_enforcement.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_03_31` — Documentación: ADRs, guías y changelog

- [ ] **Título**: Documentación: ADRs, guías y changelog
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_03_30`
- **Tests automáticos**:
  ```yaml
  - id: auto_03_31_a
    description: "Documentación: ADRs, guías y changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/03-chat-planning-aprobacion.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_03_01
  description: "Conversación de planning produce un plan utilizable"
  hint: "Usuario habla con el equipo: 'Necesito construir una API de gestión de inventario con autenticación JWT'"
  checklist:
    - "El PM agente hace preguntas de descubrimiento relevantes"
    - "El Arquitecto interviene cuando hay decisiones técnicas"
    - "Tras 3-5 turnos el equipo presenta un plan estructurado en el chat"
    - "Aparece el botón 'Generar Plan'"
    - "Al pulsar, el plan persiste con todas las tareas, dependencias y costes"

- id: human_03_02
  description: "Cambio de modos sin pérdida de contexto"
  hint: "Empezar en Planning, cambiar a Discusión, volver a Planning"
  checklist:
    - "El historial completo sigue visible tras cada cambio"
    - "El equipo en Discusión recuerda lo conversado en Planning"
    - "Al volver a Planning, el equipo retoma la propuesta donde la dejó"
    - "Los cambios de modo aparecen marcados como hito en el historial"

- id: human_03_03
  description: "Detalle del plan es revisable"
  hint: "Abrir un plan en pending_approval"
  checklist:
    - "Cabecera con coste humano vs IA y ahorro estimado"
    - "Descripción detallada con alcance, supuestos, decisiones y riesgos"
    - "Tareas con sus dependencias visibles en grafo DAG"
    - "Vista Gantt muestra línea crítica"
    - "Desglose de coste por tarea con totales"
    - "Posibilidad de añadir comentarios in-line antes de aprobar"

- id: human_03_04
  description: "Sincronización al Kanban respeta DAG"
  hint: "Aprobar plan con tareas con dependencias y sincronizar"
  checklist:
    - "Las tareas se crean en el Kanban en estado backlog"
    - "Tareas sin dependencias pasan automáticamente a ready"
    - "Tareas con dependencias quedan en backlog"
    - "Al completar una dependencia, la sucesora pasa a ready automáticamente"
    - "Intentar mover una tarea con dependencias pendientes a in_progress devuelve error 422"

- id: human_03_05
  description: "Doble firma sobre umbral funciona"
  hint: "Configurar umbral 500 € y crear plan con coste IA estimado superior"
  checklist:
    - "Tras la primera aprobación el plan pasa a pending_second_approval"
    - "Solo otro usuario con permisos puede aprobar la segunda firma"
    - "Tras la segunda firma el plan pasa a approved"
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

Tras cerrar este plan, el siguiente es **Plan 04** (`04-memoria-rag-kbs.md`).
