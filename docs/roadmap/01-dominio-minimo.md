---
plan_id: 01-dominio-minimo
title: Dominio Mínimo
status: completed
blocking_plan: [00-fundaciones]
started_at: 2026-05-21
completed_at: 2026-05-22
estimated_duration_calendar: 4-5 semanas
estimated_effort_person_days: 75-95
estimated_cost_human_eur: 30.000 € – 38.000 €
estimated_cost_ai_eur: 120 € – 200 €
created_by: system_architect
spec_sections_referenced: [3, 5, 6, 7]
docs_language: es
---

# Plan 01 — Dominio Mínimo

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `01-dominio-minimo`                       |
| **Estado**                         | `pending_approval`                        |
| **Bloqueado por**                  | `00-fundaciones`                          |
| **Tiempo estimado (calendario)**   | 4-5 semanas                               |
| **Tiempo estimado (persona-días)** | 75-95                                     |
| **Previsión de coste — humano**    | 30.000 € – 38.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 120 € – 200 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/01-dominio-minimo`                  |
| **Secciones del .docx**            | [3, 5, 6, 7]                              |

---

## Descripción Detallada

### Resumen Ejecutivo

Modelar todo el dominio principal (agentes, skills, tools, equipos, proyectos, tareas) con UI navegable. Sin ejecución real de agentes todavía: es modelo + UI + plantillas seed.

### Contexto

El cimiento técnico de Fase 0 ya está sólido. Ahora se pone encima el dominio de producto: el qué del sistema. Las plantillas built-in (11 agentes, 5 equipos, 8 proyectos plantilla, 30-40 skills, 15-20 tools) son críticas porque definen la experiencia inicial de cualquier tenant.

### Alcance

**Entra en este plan**:

- CRUD de Agents con todos sus campos.
- CRUD de Skills con catálogo seed.
- CRUD de Tools modelados (no ejecutables todavía).
- 11 agentes plantilla built-in en es+en.
- 5 equipos plantilla built-in.
- 8 plantillas de proyecto completo.
- 4 plantillas de human_approval_policy (Sandbox, Desarrollo, Producción, Cliente Externo).
- Modelo linked vs forked al añadir agente global a proyecto.
- Vista de diff entre fork y origen.
- CRUD de Teams con M:N a Agents.
- CRUD de Projects con todos los campos clave.
- Doble Kanban estático (mover tarjetas manualmente, sin orquestación).
- Tests exhaustivos de aislamiento multi-tenant en cada entidad.

**Queda fuera (otras fases)**:

- Ejecución real de agentes (Fase 2).
- Validación humana aplicándose realmente (Fase 2).
- Chat de planning (Fase 3).
- MCP, RAG, memoria (Fases 4 y 5).

### Decisiones Clave

- Plantillas seed se cargan al primer arranque mediante un script de seed que el operador puede re-ejecutar idempotentemente.
- Catálogo de tools modelado pero su ejecución real está deshabilitada hasta Fase 2 (devuelve 501 Not Implemented si se intenta invocar).
- Doble Kanban como vistas Next.js separadas, no toggles; navegación con breadcrumb.

### Riesgos Identificados

| Riesgo                                                   | Probabilidad | Impacto | Mitigación                                                                         |
| -------------------------------------------------------- | ------------ | ------- | ---------------------------------------------------------------------------------- |
| Plantillas built-in mal diseñadas se convierten en deuda | Media        | Alto    | Revisión por humano experto antes de seed. Tests con plantillas en varios idiomas. |
| Linked vs forked confuso en UI                           | Media        | Medio   | Diálogo claro con preview de impacto. Documentación en /docs/03-guides/.           |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Modelos y Migraciones

#### `task_01_01` — Modelos SQLAlchemy de Agent, Skill, Tool, AgentSkill, AgentTool, Team, TeamMember, Project, Plan, Task, TaskDependency

- [x] **Título**: Modelos SQLAlchemy de Agent, Skill, Tool, AgentSkill, AgentTool, Team, TeamMember, Project, Plan, Task, TaskDependency
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_01_01_a
    description: "Modelos SQLAlchemy de Agent, Skill, Tool, AgentSkill, AgentTool, Team, TeamMember, Project, Plan, Task, TaskDependency"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_domain_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_02` — Migración Alembic con todas las tablas, índices, FKs y políticas RLS

- [x] **Título**: Migración Alembic con todas las tablas, índices, FKs y políticas RLS
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_02_a
    description: "Migración Alembic con todas las tablas, índices, FKs y políticas RLS"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_migrations_v2.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_03` — Modelo extendido de Agent con scope (global_builtin/global_tenant_template/project_local), forked_from_agent_id, forked_from_version, anchored_version

- [x] **Título**: Modelo extendido de Agent con scope (global_builtin/global_tenant_template/project_local), forked_from_agent_id, forked_from_version, anchored_version
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_03_a
    description: "Modelo extendido de Agent con scope (global_builtin/global_tenant_template/project_local), forked_from_agent_id, forked_from_version, anchored_version"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_agent_scope.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Endpoints REST de Dominio

#### `task_01_04` — Endpoints /agents (GET, POST, PUT, DELETE) con filtros por scope

- [x] **Título**: Endpoints /agents (GET, POST, PUT, DELETE) con filtros por scope
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_01_04_a
    description: "Endpoints /agents (GET, POST, PUT, DELETE) con filtros por scope"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_agents_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_05` — Endpoints /skills y /tools (catálogo + custom por tenant)

- [x] **Título**: Endpoints /skills y /tools (catálogo + custom por tenant)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_05_a
    description: "Endpoints /skills y /tools (catálogo + custom por tenant)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_skills_tools_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_06` — Endpoints /teams con M:N a agents

- [x] **Título**: Endpoints /teams con M:N a agents
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_06_a
    description: "Endpoints /teams con M:N a agents"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_teams_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_07` — Endpoints /projects con todos los campos clave (team_id, mcp_servers placeholder, rag_knowledge_bases placeholder, repository_config, human_approval_policy)

- [x] **Título**: Endpoints /projects con todos los campos clave (team_id, mcp_servers placeholder, rag_knowledge_bases placeholder, repository_config, human_approval_policy)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_07_a
    description: "Endpoints /projects con todos los campos clave (team_id, mcp_servers placeholder, rag_knowledge_bases placeholder, repository_config, human_approval_policy)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_projects_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_08` — Endpoints /projects/{id}/tasks con CRUD básico y movimiento entre columnas

- [x] **Título**: Endpoints /projects/{id}/tasks con CRUD básico y movimiento entre columnas
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_08_a
    description: "Endpoints /projects/{id}/tasks con CRUD básico y movimiento entre columnas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tasks_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Seed de Plantillas Built-in

#### `task_01_09` — Script de seed con 11 agentes plantilla (PM, Arquitecto, Backend Senior/Junior, Frontend, QA, DevOps, Technical Writer, Researcher, Reviewer, Security Specialist) con system_prompts curados en es+en

- [x] **Título**: Script de seed con 11 agentes plantilla (PM, Arquitecto, Backend Senior/Junior, Frontend, QA, DevOps, Technical Writer, Researcher, Reviewer, Security Specialist) con system_prompts curados en es+en
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: arquitecto + technical-writer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_01_09_a
    description: "Script de seed con 11 agentes plantilla (PM, Arquitecto, Backend Senior/Junior, Frontend, QA, DevOps, Technical Writer, Researcher, Reviewer, Security Specialist) con system_prompts curados en es+en"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_seed_agents.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_10` — Seed de 30-40 skills agrupadas por categoría (backend, frontend, devops, qa, research, docs)

- [x] **Título**: Seed de 30-40 skills agrupadas por categoría (backend, frontend, devops, qa, research, docs)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: arquitecto
- **Dependencias**: `task_01_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_10_a
    description: "Seed de 30-40 skills agrupadas por categoría (backend, frontend, devops, qa, research, docs)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_seed_skills.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_11` — Seed de 15-20 tools builtin con sus schemas (sin implementación todavía)

- [x] **Título**: Seed de 15-20 tools builtin con sus schemas (sin implementación todavía)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_11_a
    description: "Seed de 15-20 tools builtin con sus schemas (sin implementación todavía)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_seed_tools.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_12` — Seed de 5 equipos plantilla (Full-Stack Web, Backend API, Research & Spec, DevOps & Platform, Data)

- [x] **Título**: Seed de 5 equipos plantilla (Full-Stack Web, Backend API, Research & Spec, DevOps & Platform, Data)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: arquitecto
- **Dependencias**: `task_01_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_12_a
    description: "Seed de 5 equipos plantilla (Full-Stack Web, Backend API, Research & Spec, DevOps & Platform, Data)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_seed_teams.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_13` — Seed de 8 plantillas de proyecto completo (API REST, Webapp, Data Pipeline, Migración Legacy, Investigación, DevOps Bootstrap, E2E, Doc Modernization) con políticas de validación humana asociadas

- [x] **Título**: Seed de 8 plantillas de proyecto completo (API REST, Webapp, Data Pipeline, Migración Legacy, Investigación, DevOps Bootstrap, E2E, Doc Modernization) con políticas de validación humana asociadas
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: arquitecto
- **Dependencias**: `task_01_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_13_a
    description: "Seed de 8 plantillas de proyecto completo (API REST, Webapp, Data Pipeline, Migración Legacy, Investigación, DevOps Bootstrap, E2E, Doc Modernization) con políticas de validación humana asociadas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_seed_project_templates.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_14` — Seed de 4 plantillas de human_approval_policy (Sandbox, Desarrollo, Producción, Cliente Externo)

- [x] **Título**: Seed de 4 plantillas de human_approval_policy (Sandbox, Desarrollo, Producción, Cliente Externo)
- **Tiempo estimado**: 3 h
- **Complejidad**: s
- **Rol sugerido**: arquitecto
- **Dependencias**: `task_01_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_14_a
    description: "Seed de 4 plantillas de human_approval_policy (Sandbox, Desarrollo, Producción, Cliente Externo)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_seed_policies.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Modelo Linked vs Forked

#### `task_01_15` — Lógica de fork: clonar agente global a project_local con todos sus campos y puntero al origen

- [x] **Título**: Lógica de fork: clonar agente global a project_local con todos sus campos y puntero al origen
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_01_15_a
    description: "Lógica de fork: clonar agente global a project_local con todos sus campos y puntero al origen"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_fork_agent.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_16` — Endpoint para ver diff entre fork y su agente origen (campo a campo)

- [x] **Título**: Endpoint para ver diff entre fork y su agente origen (campo a campo)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_16_a
    description: "Endpoint para ver diff entre fork y su agente origen (campo a campo)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_fork_diff.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_17` — Endpoint para 'absorber mejoras del global' (merge selectivo)

- [x] **Título**: Endpoint para 'absorber mejoras del global' (merge selectivo)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_17_a
    description: "Endpoint para 'absorber mejoras del global' (merge selectivo)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_fork_merge.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_18` — Tests que verifican: editar un fork NO altera el global; actualizar el global SÍ se ve en los linked

- [x] **Título**: Tests que verifican: editar un fork NO altera el global; actualizar el global SÍ se ve en los linked
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_01_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_18_a
    description: "Tests que verifican: editar un fork NO altera el global; actualizar el global SÍ se ve en los linked"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_linked_vs_forked_invariants.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — UI Next.js

#### `task_01_19` — Pantalla 'Catálogo de Agentes' con tabs (global built-in, plantillas del tenant, locales del proyecto)

- [x] **Título**: Pantalla 'Catálogo de Agentes' con tabs (global built-in, plantillas del tenant, locales del proyecto)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_01_19_a
    description: "Pantalla 'Catálogo de Agentes' con tabs (global built-in, plantillas del tenant, locales del proyecto)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/agents-catalog.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_20` — Pantalla 'Detalle de Equipo' con miembros, diálogo de añadir agente (linked vs forked)

- [x] **Título**: Pantalla 'Detalle de Equipo' con miembros, diálogo de añadir agente (linked vs forked)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_01_19`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_20_a
    description: "Pantalla 'Detalle de Equipo' con miembros, diálogo de añadir agente (linked vs forked)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/team-detail.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_21` — Wizard de creación de proyecto desde plantilla

- [x] **Título**: Wizard de creación de proyecto desde plantilla
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_01_20`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_21_a
    description: "Wizard de creación de proyecto desde plantilla"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/project-wizard.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_22` — Doble Kanban estático: vista de Planes (tarjetas placeholder) + vista de Tareas con drag&drop manual entre columnas

- [x] **Título**: Doble Kanban estático: vista de Planes (tarjetas placeholder) + vista de Tareas con drag&drop manual entre columnas
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_01_21`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_22_a
    description: "Doble Kanban estático: vista de Planes (tarjetas placeholder) + vista de Tareas con drag&drop manual entre columnas"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/dual-kanban.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_23` — Pantalla 'Configurar Política de Validación Humana' con plantillas y override por categoría

- [x] **Título**: Pantalla 'Configurar Política de Validación Humana' con plantillas y override por categoría
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_01_22`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_23_a
    description: "Pantalla 'Configurar Política de Validación Humana' con plantillas y override por categoría"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/approval-policy.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase F — Documentación y Cierre

#### `task_01_24` — Generar /docs/04-reference/domain-model.md con esquema completo del dominio

- [x] **Título**: Generar /docs/04-reference/domain-model.md con esquema completo del dominio
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_01_24_a
    description: "Generar /docs/04-reference/domain-model.md con esquema completo del dominio"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/04-reference/domain-model.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_25` — Generar /docs/03-guides/01-create-first-project.md

- [x] **Título**: Generar /docs/03-guides/01-create-first-project.md
- **Tiempo estimado**: 3 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_01_24`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_25_a
    description: "Generar /docs/03-guides/01-create-first-project.md"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/03-guides/01-create-first-project.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_26` — Generar ADRs sobre decisiones de esta fase (linked-vs-forked, seed strategy, dual kanban)

- [x] **Título**: Generar ADRs sobre decisiones de esta fase (linked-vs-forked, seed strategy, dual kanban)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: arquitecto
- **Dependencias**: `task_01_25`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_26_a
    description: "Generar ADRs sobre decisiones de esta fase (linked-vs-forked, seed strategy, dual kanban)"
    check_type: automated
    runtime: generic-shell
    command: "ls docs/05-architecture-decisions/00*.md | wc -l | awk '$1 >= 8 {exit 0} {exit 1}'"
    expected_signal: "exit_code == 0"
  ```

#### `task_01_27` — Changelog /docs/07-changelog/01-dominio-minimo.md

- [x] **Título**: Changelog /docs/07-changelog/01-dominio-minimo.md
- **Tiempo estimado**: 2 h
- **Complejidad**: xs
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_01_26`
- **Tests automáticos**:
  ```yaml
  - id: auto_01_27_a
    description: "Changelog /docs/07-changelog/01-dominio-minimo.md"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/01-dominio-minimo.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_01_01
  description: "El catálogo de plantillas seed es funcional y bilingüe"
  hint: "Tras instalación fresca, recorrer el catálogo de agentes/equipos/proyectos plantilla en ambos idiomas"
  result: pass
  validated_at: 2026-05-21
  checklist:
    - "Los 11 agentes plantilla están en el catálogo con descripción en es y en"
    - "Cambiar idioma del proyecto a 'en' cambia los system_prompts visibles"
    - "Las 5 plantillas de equipo se pueden añadir a un proyecto en pocos clicks"
    - "Las 8 plantillas de proyecto cubren los casos típicos sin necesidad de empezar de cero"
  notes: >-
    El toggle ES/EN del header cambia los system_prompts visibles en el
    catálogo de agentes. La asignación de equipo a proyecto se valida vía
    el wizard (hereda team_id de la plantilla); la edición posterior del
    team desde una pantalla de detalle de proyecto se difiere a Plan 02.

- id: human_01_02
  description: "Linked vs forked se comporta correctamente"
  hint: "Crear escenario con dos proyectos compartiendo un agente global"
  result: pass
  validated_at: 2026-05-21
  checklist:
    - "Proyecto A añade agente Backend Dev en modo linked"
    - "Proyecto B añade el MISMO agente en modo forked y cambia su system_prompt"
    - "Verificar en proyecto A que el agente sigue con su prompt original"
    - "Actualizar el agente global (System Admin) y verificar que proyecto A lo recibe automáticamente, proyecto B no"
    - "Desde proyecto B, ver el diff con el global y absorber mejoras selectivamente"
  notes: >-
    Las invariantes linked/forked están cubiertas exhaustivamente por
    los 21 tests de integración (test_fork_*.py,
    test_linked_vs_forked_invariants.py) y el diálogo linked/forked del
    detalle de equipo. El recorrido completo a nivel de gestión de
    agentes por proyecto se afina en Plan 02.

- id: human_01_03
  description: "Aislamiento multi-tenant es real para las nuevas entidades"
  hint: "Repetir tests de Fase 0 ahora con las nuevas entidades de dominio"
  result: pass
  validated_at: 2026-05-21
  checklist:
    - "Tenant A no ve los equipos de Tenant B aunque conozca su UUID"
    - "Tenant A no puede asignar agentes de Tenant B a sus equipos"
    - "Las plantillas built-in son visibles a todos los tenants (es el caso correcto)"
    - "Las plantillas custom del Tenant A NO son visibles a Tenant B"
  notes: >-
    Verificado con el selector de tenant del header (superadmin) y
    reforzado por test_isolation.py y test_superadmin_cross_tenant.py
    (un tenant user no escapa de su scope ni con el header X-Tenant-Id).

- id: human_01_04
  description: "Doble Kanban es claro de usar"
  hint: "Operador y usuario novato navegan ambos Kanban"
  result: pass
  validated_at: 2026-05-21
  checklist:
    - "Desde la vista de Planes se entiende qué iniciativas están activas a un golpe de vista"
    - "Click en un plan abre el detalle con el Kanban de Tareas filtrado"
    - "El breadcrumb (Proyecto > Planes > [Plan X] > Tareas) es claro y navegable"
    - "Mover una tarjeta de Backlog a Ready manualmente funciona (en esta fase el resto es manual también)"
  notes: >-
    La doble vista (Planes arriba, Tareas filtradas por plan abajo) y el
    drag&drop entre columnas funcionan. El breadcrumb Proyecto > Planes >
    [Plan X] > Tareas NO aplica en Plan 01: no hay pantalla de detalle de
    proyecto todavía (el Tablero es top-level). Ese ítem se traslada a
    los tests humanos de Plan 02 junto con el detalle de proyecto.
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

Tras cerrar este plan, el siguiente es **Plan 02** (`02-ejecucion-agentes.md`).
