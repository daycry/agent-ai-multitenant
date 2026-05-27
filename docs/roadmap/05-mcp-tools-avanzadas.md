---
plan_id: 05-mcp-tools-avanzadas
title: MCP y Tools Avanzadas
status: in_progress
blocking_plan: [04-memoria-rag-kbs, 04.5-agent-runtime-integration]
started_at: 2026-05-26
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 40-55
estimated_cost_human_eur: 16.000 € – 22.000 €
estimated_cost_ai_eur: 80 € – 120 €
created_by: system_architect
spec_sections_referenced: [9]
docs_language: es
---

# Plan 05 — MCP y Tools Avanzadas

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `05-mcp-tools-avanzadas`                  |
| **Estado**                         | `pending_approval`                        |
| **Bloqueado por**                  | `04-memoria-rag-kbs`                      |
| **Tiempo estimado (calendario)**   | 2-3 semanas                               |
| **Tiempo estimado (persona-días)** | 40-55                                     |
| **Previsión de coste — humano**    | 16.000 € – 22.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 80 € – 120 €                              |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/05-mcp-tools-avanzadas`             |
| **Secciones del .docx**            | [9]                                       |

---

## Descripción Detallada

### Resumen Ejecutivo

Cliente MCP genérico que soporta stdio + sse + streamable_http. Cada proyecto declara sus MCP servers. Las tools MCP se descubren e inyectan al agente como tools nativas. Integraciones pre-verified con servidores comunes.

### Contexto

Hasta aquí los agentes usan solo tools builtin. Con MCP el sistema se abre a un ecosistema enorme de integraciones (GitHub, Slack, Postgres, GDrive, Jira, etc.) sin necesidad de escribir código por cada integración.

### Alcance

**Entra en este plan**:

- Cliente MCP en Python con soporte stdio + sse + streamable_http.
- Configuración por proyecto: Project.mcp_servers JSONB con URL, transport, auth, scopes.
- Descubrimiento automático de tools del servidor MCP al conectar.
- Inyección de tools MCP al agent runtime indistinguibles de las builtin.
- Catálogo de MCP servers verified: docling-mcp (ya integrado), github-mcp, postgres-mcp, filesystem-mcp, gdrive-mcp, gmail-mcp, gcalendar-mcp, slack-mcp, jira-mcp/linear-mcp.
- UI de configuración de MCP servers por proyecto.
- Inspección de tools disponibles por agente (panel diagnóstico).
- Tools de tipo http_endpoint y python_function (que en Fase 1 quedaron modelados pero no ejecutables) ahora funcionales.
- Sandbox para tools de tipo docker_command (lanzar contenedor para una tool específica).

**Queda fuera (otras fases)**:

- Marketplace de MCP servers (Fase 9).
- Tools privilegiadas con permisos elevados (Fase 11: guardrails decidirán).

### Decisiones Clave

- Tools MCP por defecto en network=none (red restringida) salvo allowlist explícito del proyecto.
- Timeouts agresivos por tool (default 30s) configurables.
- Auth de MCP servers vía Vault: tokens nunca en config plana.

### Riesgos Identificados

| Riesgo                                                   | Probabilidad | Impacto | Mitigación                                                                              |
| -------------------------------------------------------- | ------------ | ------- | --------------------------------------------------------------------------------------- |
| MCP servers de terceros mal mantenidos rompen el sistema | Media        | Medio   | Wrapping con timeout y circuit breaker. Marketplace en Fase 9 con niveles de confianza. |
| Demasiadas tools confunden al agente                     | Media        | Medio   | Filtrar al agente solo las tools relevantes a su skill set.                             |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Cliente MCP Genérico

#### `task_05_01` — Implementar cliente MCP Python con transports stdio, sse, streamable_http

- [x] **Título**: Implementar cliente MCP Python con transports stdio, sse, streamable_http
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_05_01_a
    description: "Implementar cliente MCP Python con transports stdio, sse, streamable_http"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mcp_client.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_02` — Descubrimiento de tools al conectar (handshake + list_tools)

- [x] **Título**: Descubrimiento de tools al conectar (handshake + list_tools)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_05_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_02_a
    description: "Descubrimiento de tools al conectar (handshake + list_tools)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mcp_discovery.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_03` — Adaptador que convierte tool MCP a tool del sistema (mismo schema que builtin)

- [x] **Título**: Adaptador que convierte tool MCP a tool del sistema (mismo schema que builtin)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_05_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_03_a
    description: "Adaptador que convierte tool MCP a tool del sistema (mismo schema que builtin)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mcp_adapter.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Configuración por Proyecto

#### `task_05_04` — Campo Project.mcp_servers (JSONB) con validación de schema

- [x] **Título**: Campo Project.mcp_servers (JSONB) con validación de schema
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_05_04_a
    description: "Campo Project.mcp_servers (JSONB) con validación de schema"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_mcp_config_schema.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_05` — Inyección de auth vía Vault al construir el cliente

- [x] **Título**: Inyección de auth vía Vault al construir el cliente
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_05_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_05_a
    description: "Inyección de auth vía Vault al construir el cliente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mcp_auth_injection.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_06` — UI de configuración de MCP servers en panel del proyecto

- [x] **Título**: UI de configuración de MCP servers en panel del proyecto
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_05_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_06_a
    description: "UI de configuración de MCP servers en panel del proyecto"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/mcp-config-ui.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_07` — Test de conexión desde la UI (botón 'Probar') que muestra tools descubiertas

- [x] **Título**: Test de conexión desde la UI (botón 'Probar') que muestra tools descubiertas
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_05_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_07_a
    description: "Test de conexión desde la UI (botón 'Probar') que muestra tools descubiertas"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/mcp-test-connection.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Integraciones Verified

#### `task_05_08` — Documentar e integrar docling-mcp (ya conectado en Fase 4) en el catálogo

- [x] **Título**: Documentar e integrar docling-mcp (ya conectado en Fase 4) en el catálogo
- **Tiempo estimado**: 2 h
- **Complejidad**: xs
- **Rol sugerido**: technical-writer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_05_08_a
    description: "Documentar e integrar docling-mcp (ya conectado en Fase 4) en el catálogo"
    check_type: automated
    runtime: generic-shell
    command: "grep -q docling-mcp docs/04-reference/mcp-servers.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_09` — Plantilla de configuración para github-mcp + tests de integración

- [x] **Título**: Plantilla de configuración para github-mcp + tests de integración
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_05_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_09_a
    description: "Plantilla de configuración para github-mcp + tests de integración"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_github_mcp.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_10` — Plantilla de configuración para postgres-mcp + tests con DB efímera

- [x] **Título**: Plantilla de configuración para postgres-mcp + tests con DB efímera
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_05_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_10_a
    description: "Plantilla de configuración para postgres-mcp + tests con DB efímera"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_postgres_mcp.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_11` — Plantillas para filesystem-mcp, gdrive-mcp, gmail-mcp, gcalendar-mcp, slack-mcp, jira-mcp/linear-mcp

- [x] **Título**: Plantillas para filesystem-mcp, gdrive-mcp, gmail-mcp, gcalendar-mcp, slack-mcp, jira-mcp/linear-mcp
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_05_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_11_a
    description: "Plantillas para filesystem-mcp, gdrive-mcp, gmail-mcp, gcalendar-mcp, slack-mcp, jira-mcp/linear-mcp"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_mcp_integrations.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Tools Avanzadas y Cierre

#### `task_05_12` — Activar tools de tipo http_endpoint (modeladas en Fase 1, ahora ejecutables con allowlist)

- [x] **Título**: Activar tools de tipo http_endpoint (modeladas en Fase 1, ahora ejecutables con allowlist)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_05_12_a
    description: "Activar tools de tipo http_endpoint (modeladas en Fase 1, ahora ejecutables con allowlist)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_http_endpoint_tools.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_13` — Activar tools de tipo python_function en sandbox seguro (subprocess aislado, no eval)

- [ ] **Título**: Activar tools de tipo python_function en sandbox seguro (subprocess aislado, no eval)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_05_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_13_a
    description: "Activar tools de tipo python_function en sandbox seguro (subprocess aislado, no eval)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_python_function_tools.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_14` — Activar tools de tipo docker_command (lanza contenedor efímero por tool)

- [ ] **Título**: Activar tools de tipo docker_command (lanza contenedor efímero por tool)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + devops
- **Dependencias**: `task_05_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_14_a
    description: "Activar tools de tipo docker_command (lanza contenedor efímero por tool)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docker_command_tools.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_15` — Panel diagnóstico de tools disponibles por agente

- [ ] **Título**: Panel diagnóstico de tools disponibles por agente
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_05_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_15_a
    description: "Panel diagnóstico de tools disponibles por agente"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/agent-tools-diagnostic.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_05_16` — Documentación: ADRs MCP, guías de configuración, changelog

- [ ] **Título**: Documentación: ADRs MCP, guías de configuración, changelog
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_05_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_05_16_a
    description: "Documentación: ADRs MCP, guías de configuración, changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/05-mcp-tools-avanzadas.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_05_01
  description: "MCP funciona con un servidor real"
  hint: "Configurar github-mcp en un proyecto con un PAT de prueba"
  checklist:
    - "La UI muestra las tools descubiertas del servidor"
    - "Un agente puede listar repos del usuario con la tool list_repos"
    - "Un agente puede crear un issue en un repo de prueba"
    - "Los tokens del Vault se inyectan sin aparecer en logs"

- id: human_05_02
  description: "Aislamiento de tools docker_command"
  hint: "Crear una tool que ejecuta un script en un contenedor python:3.12-alpine"
  checklist:
    - "La tool corre en un contenedor efímero separado"
    - "El contenedor tiene los mismos guardrails que agent-runtime"
    - "Al terminar, el contenedor se destruye y no deja rastro"

- id: human_05_03
  description: "Allowlist de http_endpoint se respeta"
  hint: "Configurar una tool http_endpoint que llama a un dominio fuera del allowlist"
  checklist:
    - "La invocación falla con error explícito sobre allowlist"
    - "El intento queda en audit_log con el dominio bloqueado"
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

Tras cerrar este plan, el siguiente es **Plan 06** (`06-testing-revision-git.md`).
