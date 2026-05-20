---
plan_id: 02-ejecucion-agentes
title: Ejecución de Agentes
status: pending_approval
blocking_plan: [01-dominio-minimo]
started_at: null
completed_at: null
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 80-100
estimated_cost_human_eur: 32.000 € – 40.000 €
estimated_cost_ai_eur: 150 € – 250 €
created_by: system_architect
spec_sections_referenced: [5.5, 12, 13, 21]
docs_language: es
---

# Plan 02 — Ejecución de Agentes

## Cabecera

| Campo | Valor |
|-------|-------|
| **ID del Plan** | `02-ejecucion-agentes` |
| **Estado** | `pending_approval` |
| **Bloqueado por** | `01-dominio-minimo` |
| **Tiempo estimado (calendario)** | 4-5 semanas |
| **Tiempo estimado (persona-días)** | 80-100 |
| **Previsión de coste — humano** | 32.000 € – 40.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA** | 150 € – 250 € |
| **Aprobador propuesto** | System Admin |
| **Rama git** | `plan/02-ejecucion-agentes` |
| **Secciones del .docx** | [5.5, 12, 13, 21] |

---

## Descripción Detallada

### Resumen Ejecutivo

Dar vida al sistema: orquestador, workers Celery, agent-runtime con LangGraph, tools builtin funcionales, captura completa de ejecuciones, aplicación real de human_approval_policy. Sin testing heterogéneo todavía (Fase 6).

### Contexto

Tras Fase 1 el dominio está modelado pero estático. Ahora se ejecutan agentes reales: el agent loop completo con LangGraph corre dentro de contenedores agent-runtime aislados, observable en tiempo real desde la UI.

### Alcance

**Entra en este plan**:

- Servicio Orchestrator con políticas skill_match / load_balanced / round_robin / manual.
- Celery con colas: default, heavy, gpu (opcional), ingestion, test (placeholder), review (placeholder), privileged.
- Worker base que recoge jobs y lanza contenedores agent-runtime.
- Imagen agent-runtime Python 3.12 + LangGraph + tools builtin.
- Agent Loop completo: perceive → recall → plan → act → observe → reflect → finalize → self_review.
- Tools builtin funcionales: shell_exec, file_*, http_request (con allowlist), kanban_update, task_comment, notify_user, agent_invoke. Placeholders para memory_*, document_convert (Fase 4).
- Captura completa de Executions con steps_log JSONB.
- UI de Timeline de Ejecución (paso a paso jerárquico).
- Streaming de logs y eventos vía WebSocket.
- Transiciones automáticas del Kanban con fn_compute_task_ready.
- Aislamiento estricto de contenedores (red dedicada, cap-drop, seccomp).
- Salvaguardas: max_iterations, timeouts, detección de loops.
- Aplicación real de human_approval_policy: pausar en awaiting_human_approval, notificar, aprobación/rechazo.

**Queda fuera (otras fases)**:

- Tests automáticos heterogéneos (Fase 6).
- Memoria persistente real (Fase 4).
- RAG (Fase 4).
- MCP (Fase 5).
- Modelo linked vs forked operativo en ejecución (las ejecuciones usan el agente efectivo independientemente del scope; ya cubierto en Fase 1).

### Decisiones Clave

- LangGraph como motor del agent loop (vs implementación manual): madurez, soporte de checkpointing, ecosistema.
- Celery con Redis Streams como broker (no RabbitMQ): menos componentes, ya tenemos Redis.
- Streams Redis específicos por ejecución para los logs en tiempo real (no DB writes constantes).
- Aislamiento por contenedor obligatorio sin excepción: ningún tool builtin se ejecuta en el worker.

### Riesgos Identificados

| Riesgo | Probabilidad | Impacto | Mitigación |
|--------|--------------|---------|------------|
| Bug en aislamiento permite escape de contenedor | Baja | Crítico | Pentest interno antes de cerrar fase. Configuración seccomp + AppArmor validada con test. |
| Loops infinitos consumen recursos | Media | Alto | max_iterations + detección de loops repetitivos + circuit breaker + budget caps. |
| WebSocket no escala con muchas ejecuciones simultáneas | Media | Medio | Sticky sessions con nginx. Pruebas de carga con 50+ ejecuciones. |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Orchestrator y Celery

#### `task_02_01` — Servicio Orchestrator FastAPI que escucha eventos Redis Streams

- [ ] **Título**: Servicio Orchestrator FastAPI que escucha eventos Redis Streams
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_02_01_a
    description: "Servicio Orchestrator FastAPI que escucha eventos Redis Streams"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_orchestrator.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_02` — Configuración Celery con 7 colas (default, heavy, gpu, ingestion, test, review, privileged)

- [ ] **Título**: Configuración Celery con 7 colas (default, heavy, gpu, ingestion, test, review, privileged)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_02_a
    description: "Configuración Celery con 7 colas (default, heavy, gpu, ingestion, test, review, privileged)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_celery_queues.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_03` — Políticas de asignación: skill_match (similitud coseno), load_balanced, round_robin, manual

- [ ] **Título**: Políticas de asignación: skill_match (similitud coseno), load_balanced, round_robin, manual
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_03_a
    description: "Políticas de asignación: skill_match (similitud coseno), load_balanced, round_robin, manual"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_assignment_policies.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_04` — fn_compute_task_ready: recálculo automático del estado 'ready' al pasar dependencias a done

- [ ] **Título**: fn_compute_task_ready: recálculo automático del estado 'ready' al pasar dependencias a done
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_04_a
    description: "fn_compute_task_ready: recálculo automático del estado 'ready' al pasar dependencias a done"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_task_dag.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Worker y Contenedor agent-runtime

#### `task_02_05` — Dockerfile agent-runtime:v1 con Python 3.12 + LangGraph + libs internas

- [ ] **Título**: Dockerfile agent-runtime:v1 con Python 3.12 + LangGraph + libs internas
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_02_05_a
    description: "Dockerfile agent-runtime:v1 con Python 3.12 + LangGraph + libs internas"
    check_type: automated
    runtime: generic-shell
    command: "docker build -t agent-runtime:v1 docker/agent-runtimes/agent-runtime/"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_06` — Worker Celery base que lanza contenedores con docker SDK

- [ ] **Título**: Worker Celery base que lanza contenedores con docker SDK
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_06_a
    description: "Worker Celery base que lanza contenedores con docker SDK"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_worker_launches_container.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_07` — Aislamiento estricto: red dedicada, cap-drop ALL, seccomp default-deny, AppArmor, read-only FS

- [ ] **Título**: Aislamiento estricto: red dedicada, cap-drop ALL, seccomp default-deny, AppArmor, read-only FS
- **Tiempo estimado**: 10 h
- **Complejidad**: l
- **Rol sugerido**: devops + security
- **Dependencias**: `task_02_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_07_a
    description: "Aislamiento estricto: red dedicada, cap-drop ALL, seccomp default-deny, AppArmor, read-only FS"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_container_isolation.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_08` — Inyección de credenciales vía Docker secrets (Vault → secrets file → /run/secrets/)

- [ ] **Título**: Inyección de credenciales vía Docker secrets (Vault → secrets file → /run/secrets/)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_02_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_08_a
    description: "Inyección de credenciales vía Docker secrets (Vault → secrets file → /run/secrets/)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_secrets_injection.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_09` — Test específico que verifica que NO se puede acceder al socket Docker desde dentro del contenedor

- [ ] **Título**: Test específico que verifica que NO se puede acceder al socket Docker desde dentro del contenedor
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: security
- **Dependencias**: `task_02_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_09_a
    description: "Test específico que verifica que NO se puede acceder al socket Docker desde dentro del contenedor"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_no_docker_socket.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Agent Loop con LangGraph

#### `task_02_10` — Grafo de estado del agent loop: nodos perceive, recall, plan, act, observe, reflect, finalize, self_review

- [ ] **Título**: Grafo de estado del agent loop: nodos perceive, recall, plan, act, observe, reflect, finalize, self_review
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_02_10_a
    description: "Grafo de estado del agent loop: nodos perceive, recall, plan, act, observe, reflect, finalize, self_review"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_agent_graph.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_11` — Persistencia de steps_log JSONB con detalle de cada paso

- [ ] **Título**: Persistencia de steps_log JSONB con detalle de cada paso
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_11_a
    description: "Persistencia de steps_log JSONB con detalle de cada paso"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_steps_log.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_12` — Captura completa de tool_calls, model_calls (con tokens y coste), memory_reads (placeholder)

- [ ] **Título**: Captura completa de tool_calls, model_calls (con tokens y coste), memory_reads (placeholder)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_12_a
    description: "Captura completa de tool_calls, model_calls (con tokens y coste), memory_reads (placeholder)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_execution_capture.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_13` — Salvaguardas: max_iterations, max_tokens, max_cost, max_wall_clock, max_tool_calls

- [ ] **Título**: Salvaguardas: max_iterations, max_tokens, max_cost, max_wall_clock, max_tool_calls
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_13_a
    description: "Salvaguardas: max_iterations, max_tokens, max_cost, max_wall_clock, max_tool_calls"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_safeguards.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_14` — Detección de loops repetitivos (misma acción >3 veces aborta con código específico)

- [ ] **Título**: Detección de loops repetitivos (misma acción >3 veces aborta con código específico)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_02_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_14_a
    description: "Detección de loops repetitivos (misma acción >3 veces aborta con código específico)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_loop_detection.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Tools Builtin Funcionales

#### `task_02_15` — Tool shell_exec con allowlist de comandos por proyecto + timeout + captura stdout/stderr

- [ ] **Título**: Tool shell_exec con allowlist de comandos por proyecto + timeout + captura stdout/stderr
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_02_15_a
    description: "Tool shell_exec con allowlist de comandos por proyecto + timeout + captura stdout/stderr"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tool_shell_exec.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_16` — Tools file_read, file_write, file_list scoped a /workspace

- [ ] **Título**: Tools file_read, file_write, file_list scoped a /workspace
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_16_a
    description: "Tools file_read, file_write, file_list scoped a /workspace"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tool_file_ops.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_17` — Tool http_request con allowlist de dominios por proyecto + timeout + max body size

- [ ] **Título**: Tool http_request con allowlist de dominios por proyecto + timeout + max body size
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_17_a
    description: "Tool http_request con allowlist de dominios por proyecto + timeout + max body size"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tool_http.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_18` — Tools kanban_update, task_comment, notify_user, agent_invoke

- [ ] **Título**: Tools kanban_update, task_comment, notify_user, agent_invoke
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_18_a
    description: "Tools kanban_update, task_comment, notify_user, agent_invoke"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tools_orchestration.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_19` — Placeholders memory_recall, memory_store, document_convert que devuelven 501 hasta Fase 4

- [ ] **Título**: Placeholders memory_recall, memory_store, document_convert que devuelven 501 hasta Fase 4
- **Tiempo estimado**: 2 h
- **Complejidad**: xs
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_19_a
    description: "Placeholders memory_recall, memory_store, document_convert que devuelven 501 hasta Fase 4"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_placeholders.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — UI y Tiempo Real

#### `task_02_20` — WebSocket /ws/executions/{id} con streaming de eventos del Redis Stream

- [ ] **Título**: WebSocket /ws/executions/{id} con streaming de eventos del Redis Stream
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_02_20_a
    description: "WebSocket /ws/executions/{id} con streaming de eventos del Redis Stream"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/execution-streaming.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_21` — WebSocket /ws/kanban/{project_id} con updates de transiciones de tarea

- [ ] **Título**: WebSocket /ws/kanban/{project_id} con updates de transiciones de tarea
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_20`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_21_a
    description: "WebSocket /ws/kanban/{project_id} con updates de transiciones de tarea"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/kanban-realtime.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_22` — UI Timeline de Ejecución (jerárquico, expansible, con costes y tiempos por paso)

- [ ] **Título**: UI Timeline de Ejecución (jerárquico, expansible, con costes y tiempos por paso)
- **Tiempo estimado**: 14 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_02_21`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_22_a
    description: "UI Timeline de Ejecución (jerárquico, expansible, con costes y tiempos por paso)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/execution-timeline.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_23` — UI Kanban dual reactiva en tiempo real a los eventos del WebSocket

- [ ] **Título**: UI Kanban dual reactiva en tiempo real a los eventos del WebSocket
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_02_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_23_a
    description: "UI Kanban dual reactiva en tiempo real a los eventos del WebSocket"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/kanban-live.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase F — Validación Humana y Cierre

#### `task_02_24` — Motor de aplicación de human_approval_policy: interceptar acciones de categorías sensibles, pausar ejecución, persistir solicitud

- [ ] **Título**: Motor de aplicación de human_approval_policy: interceptar acciones de categorías sensibles, pausar ejecución, persistir solicitud
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_02_24_a
    description: "Motor de aplicación de human_approval_policy: interceptar acciones de categorías sensibles, pausar ejecución, persistir solicitud"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_human_approval_motor.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_25` — Notificación in-app de solicitud de aprobación (canales externos en Fase 10)

- [ ] **Título**: Notificación in-app de solicitud de aprobación (canales externos en Fase 10)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_24`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_25_a
    description: "Notificación in-app de solicitud de aprobación (canales externos en Fase 10)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/approval-request.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_26` — UI de aprobación con botones Aprobar/Rechazar y motivo opcional

- [ ] **Título**: UI de aprobación con botones Aprobar/Rechazar y motivo opcional
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_02_25`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_26_a
    description: "UI de aprobación con botones Aprobar/Rechazar y motivo opcional"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/approval-ui.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_27` — Timeout automático tras 24h sin respuesta (configurable)

- [ ] **Título**: Timeout automático tras 24h sin respuesta (configurable)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_02_26`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_27_a
    description: "Timeout automático tras 24h sin respuesta (configurable)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_approval_timeout.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_02_28` — Documentación: ADRs, guías, changelog del plan

- [ ] **Título**: Documentación: ADRs, guías, changelog del plan
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_02_27`
- **Tests automáticos**:
  ```yaml
  - id: auto_02_28_a
    description: "Documentación: ADRs, guías, changelog del plan"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/02-ejecucion-agentes.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_02_01
  description: "Un agente puede ejecutar una tarea simple end-to-end"
  hint: "Crear tarea 'Escribe un poema sobre el mar' asignada a un agente Writer, observar la ejecución"
  checklist:
    - "La tarea pasa por backlog → ready → in_progress → in_review → done"
    - "El Timeline muestra paso a paso lo que hizo el agente"
    - "El output (el poema) está persistido y visible"
    - "El coste registrado es coherente (tokens × precio del modelo)"

- id: human_02_02
  description: "Aislamiento del contenedor es real"
  hint: "Pentest interno básico desde dentro del agent-runtime"
  checklist:
    - "Desde el contenedor NO se puede hacer ls /var/run/docker.sock (no existe)"
    - "Desde el contenedor NO se pueden ver procesos del host"
    - "Desde el contenedor NO se puede acceder a otros contenedores por red"
    - "Si el agente intenta escribir fuera de /workspace, falla con permission denied"
    - "Si el agente intenta http_request a un dominio fuera de la allowlist, falla"

- id: human_02_03
  description: "Las salvaguardas funcionan"
  hint: "Crear tarea diseñada para disparar las salvaguardas"
  checklist:
    - "Tarea con max_iterations=5: el agente se aborta tras 5 iteraciones"
    - "Tarea con presupuesto agotado: la siguiente llamada al LLM falla con budget_exceeded"
    - "Tarea con timeout 30s: se mata con SIGTERM tras 30s y SIGKILL tras 60s"
    - "Si el agente repite la misma acción 3 veces, el detector de loops aborta"

- id: human_02_04
  description: "Validación humana pausa correctamente"
  hint: "Configurar proyecto con policy 'production_deploy: require_human' y disparar una tarea que la requiera"
  checklist:
    - "La tarea pasa a awaiting_human_approval"
    - "Aparece notificación in-app al project_owner"
    - "Al aprobar, la tarea continúa con la acción aplicada"
    - "Al rechazar, la tarea vuelve a in_progress y el agente recibe feedback"
    - "Tras 24h sin respuesta, la tarea pasa a blocked"

- id: human_02_05
  description: "Tiempo real funciona"
  hint: "Abrir varias pestañas con Timeline y Kanban del mismo proyecto"
  checklist:
    - "Eventos de ejecución se reflejan en menos de 1s en todas las pestañas"
    - "Transiciones de tarea (in_progress → done) se ven en el Kanban sin refrescar"
    - "Cerrar y reabrir el WebSocket recupera el estado actual sin pérdida"

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

Tras cerrar este plan, el siguiente es **Plan 03** (`03-chat-planning-aprobacion.md`).
