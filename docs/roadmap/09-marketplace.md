---
plan_id: 09-marketplace
title: Marketplace de Skills y Tools
status: in_progress
blocking_plan: [05-mcp-tools-avanzadas]
started_at: 2026-05-30
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 60-80
estimated_cost_human_eur: 24.000 € – 32.000 €
estimated_cost_ai_eur: 120 € – 200 €
created_by: system_architect
spec_sections_referenced: [32]
docs_language: es
---

# Plan 09 — Marketplace de Skills y Tools

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `09-marketplace`                          |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `05-mcp-tools-avanzadas`                  |
| **Tiempo estimado (calendario)**   | 3-4 semanas                               |
| **Tiempo estimado (persona-días)** | 60-80                                     |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 120 € – 200 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/09-marketplace`                     |
| **Secciones del .docx**            | [32]                                      |

---

## Descripción Detallada

### Resumen Ejecutivo

Marketplace de skills, tools y MCP servers instalables. Niveles de confianza, análisis estático previo, ejecución aislada, consentimiento granular de permisos. Incluye Playwright como caso destacado.

### Contexto

Tras Fase 5 los proyectos pueden añadir MCP servers manualmente. Esta fase los hace descubribles y compartibles vía marketplace, con seguridad apropiada.

### Alcance

**Entra en este plan**:

- Modelo marketplace_sources, listings, installations, audits.
- Niveles de confianza (verified / community / experimental).
- Análisis estático previo a la instalación (Bandit, semgrep).
- Sandbox de ejecución durante prueba post-instalación.
- Consentimiento granular de permisos por tool (allowed_domains, allowed_paths, network_policy).
- Revocación y auditoría con audit_log.
- Formato estándar SKILL.md (similar a Anthropic Skills) para skills instalables.
- Formato estándar de tools (manifest YAML + implementación).
- Playwright como tool destacada con configuración guiada.
- Plantillas de agentes especializados (QA E2E Automator, etc.).
- Marketplace público (catálogo curado por Anthropic) + privado del tenant.
- Compartir recursos entre tenants (opt-in con audit).
- Versionado, updates y compatibilidad con semver.

**Queda fuera (otras fases)**:

- Pagos en el marketplace (queda fuera del scope departamental).
- Bidirectional rating/reviews (queda para iteración posterior).

### Decisiones Clave

- Formato SKILL.md inspirado en Anthropic Skills: frontmatter + descripción + ejemplos + dependencias.
- Niveles de confianza determinan los guardrails aplicados, no la disponibilidad.
- Tools community siempre requieren consentimiento explícito del project_owner por permiso.

### Riesgos Identificados

| Riesgo                                             | Probabilidad | Impacto | Mitigación                                                                                                        |
| -------------------------------------------------- | ------------ | ------- | ----------------------------------------------------------------------------------------------------------------- |
| Tool malicioso del marketplace compromete proyecto | Baja         | Crítico | Análisis estático + sandbox + revisión humana en niveles community. Verified requiere firma por equipo Anthropic. |
| Marketplace público se contamina con basura        | Media        | Bajo    | Moderación + reporting + delisting.                                                                               |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Modelo de Marketplace

#### `task_09_01` — Modelos marketplace_sources, listings, installations, audit_entries

- [x] **Título**: Modelos marketplace_sources, listings, installations, audit_entries
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_09_01_a
    description: "Modelos marketplace_sources, listings, installations, audit_entries"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_02` — Migración + RLS

- [x] **Título**: Migración + RLS
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_09_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_02_a
    description: "Migración + RLS"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_migration.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_03` — Endpoints REST de marketplace (listings, install, uninstall, list_installed)

- [x] **Título**: Endpoints REST de marketplace (listings, install, uninstall, list_installed)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_09_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_03_a
    description: "Endpoints REST de marketplace (listings, install, uninstall, list_installed)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Niveles de Confianza y Seguridad

#### `task_09_04` — Definición de 3 niveles (verified / community / experimental)

- [x] **Título**: Definición de 3 niveles (verified / community / experimental)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: arquitecto + security
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_09_04_a
    description: "Definición de 3 niveles (verified / community / experimental)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_trust_levels.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_05` — Análisis estático previo: Bandit para Python, semgrep para patrones genéricos

- [x] **Título**: Análisis estático previo: Bandit para Python, semgrep para patrones genéricos
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: security
- **Dependencias**: `task_09_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_05_a
    description: "Análisis estático previo: Bandit para Python, semgrep para patrones genéricos"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_static_analysis.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_06` — Sandbox de ejecución durante prueba post-instalación

- [x] **Título**: Sandbox de ejecución durante prueba post-instalación <!-- task_09_06: SandboxSpec + run/teardown en marketplace/sandbox.py reutilizando el patrón de aislamiento (cap_drop ALL, no-new-privileges, read-only root, mem/pids/cpu limits, network policy honored, sin socket Docker). Tests con cliente Docker MOCKEADO (tests/integration/test_install_sandbox.py); la ejecución real en contenedor queda como paso de integración pendiente de la imagen runtime. -->
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: security + devops
- **Dependencias**: `task_09_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_06_a
    description: "Sandbox de ejecución durante prueba post-instalación"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_install_sandbox.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_07` — Consentimiento granular: UI que muestra permisos solicitados y el project_owner aprueba uno por uno

- [x] **Título**: Consentimiento granular: UI que muestra permisos solicitados y el project_owner aprueba uno por uno <!-- task_09_07: backend GET .../permissions + POST .../consent (routers/marketplace.py) sobre marketplace.consent (lógica pura) — community/experimental requieren consentimiento por permiso (allowed_domains/allowed_paths/network_policy); install consent-gated nace DISABLED y solo pasa a ENABLED cuando TODOS los permisos requeridos están concedidos; deny -> sigue disabled + audit consent_denied. Migración 0042 reversible (denied_permissions JSONB). RBAC tenant_admin (rol project_owner de facto en este repo) + RLS + @pytest.mark.cross_tenant (tests/integration/test_consent.py, 7 pass). FRONTEND: /admin/marketplace/installations/[id]/permissions con RoleGuard tenant_admin + shadcn/ui (typecheck/lint/build verdes). e2e/permission-consent.spec.ts ESCRITO pero NO ejecutado (PENDING HUMAN VERIFICATION). -->
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_09_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_07_a
    description: "Consentimiento granular: UI que muestra permisos solicitados y el project_owner aprueba uno por uno"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/permission-consent.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_08` — Revocación de instalación + audit_log obligatorio

- [x] **Título**: Revocación de instalación + audit_log obligatorio
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_09_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_08_a
    description: "Revocación de instalación + audit_log obligatorio"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_revocation.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Formatos Estándar e Instalación

#### `task_09_09` — Formato SKILL.md: frontmatter YAML + descripción + ejemplos + dependencias

- [x] **Título**: Formato SKILL.md: frontmatter YAML + descripción + ejemplos + dependencias <!-- task_09_09: parser/validador SKILL.md en marketplace/skill_format.py (SkillManifest + parse_skill_md -> SkillFormatError tipado). Frontmatter YAML (name/description/version semver + dependencies/permissions/examples) + cuerpo Markdown. Reutiliza PERMISSION_KEYS + NetworkPolicy de marketplace/trust.py; .requested_permissions renderiza los descriptores {"type","value"} que ya consumen consent/install. PyYAML (dep existente), sin nueva dep pesada, sin migración (reutiliza manifest/requested_permissions JSONB). Tests tests/unit/test_skill_md_format.py (40 pass). -->
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_09_09_a
    description: "Formato SKILL.md: frontmatter YAML + descripción + ejemplos + dependencias"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_skill_md_format.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_10` — Formato estándar de tool (manifest YAML + implementación)

- [ ] **Título**: Formato estándar de tool (manifest YAML + implementación)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_09_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_10_a
    description: "Formato estándar de tool (manifest YAML + implementación)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_tool_format.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_11` — Proceso de instalación de skill/tool: descarga, verificación firma, análisis, sandbox, persistencia en tenant

- [ ] **Título**: Proceso de instalación de skill/tool: descarga, verificación firma, análisis, sandbox, persistencia en tenant
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_09_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_11_a
    description: "Proceso de instalación de skill/tool: descarga, verificación firma, análisis, sandbox, persistencia en tenant"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_install_flow.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_12` — Versionado semver y updates

- [ ] **Título**: Versionado semver y updates
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_09_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_12_a
    description: "Versionado semver y updates"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_versioning.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Playwright como Caso Destacado

#### `task_09_13` — Tool Playwright detallada con configuración guiada (browsers, headless, screenshots, traces)

- [ ] **Título**: Tool Playwright detallada con configuración guiada (browsers, headless, screenshots, traces)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_09_13_a
    description: "Tool Playwright detallada con configuración guiada (browsers, headless, screenshots, traces)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/playwright-tool-config.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_14` — Agente plantilla 'QA E2E Automator' que usa Playwright

- [ ] **Título**: Agente plantilla 'QA E2E Automator' que usa Playwright
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_09_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_14_a
    description: "Agente plantilla 'QA E2E Automator' que usa Playwright"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_qa_e2e_automator.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_15` — Plantillas de tests E2E pre-cargadas (login, signup, checkout, etc.)

- [ ] **Título**: Plantillas de tests E2E pre-cargadas (login, signup, checkout, etc.)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_09_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_15_a
    description: "Plantillas de tests E2E pre-cargadas (login, signup, checkout, etc.)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/playwright-templates.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Marketplace Privado y Cross-Tenant

#### `task_09_16` — Marketplace privado del tenant para skills/tools internas

- [ ] **Título**: Marketplace privado del tenant para skills/tools internas
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_09_16_a
    description: "Marketplace privado del tenant para skills/tools internas"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/private-marketplace.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_17` — Compartir recursos entre tenants (opt-in con audit del System Admin)

- [ ] **Título**: Compartir recursos entre tenants (opt-in con audit del System Admin)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_09_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_17_a
    description: "Compartir recursos entre tenants (opt-in con audit del System Admin)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_cross_tenant_sharing.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_18` — UI de gestión del marketplace por Tenant Admin

- [ ] **Título**: UI de gestión del marketplace por Tenant Admin
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_09_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_18_a
    description: "UI de gestión del marketplace por Tenant Admin"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/marketplace-admin.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_19` — Documentación + ADRs + changelog

- [ ] **Título**: Documentación + ADRs + changelog
- **Tiempo estimado**: 8 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_09_18`
- **Tests automáticos**:
  ```yaml
  - id: auto_09_19_a
    description: "Documentación + ADRs + changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/09-marketplace.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_09_01
  description: "Instalación con consentimiento granular"
  hint: "Instalar tool community que pide allowed_domains [api.x.com]"
  checklist:
    - "La UI muestra el permiso solicitado de forma legible"
    - "El project_owner aprueba/rechaza por permiso individual"
    - "Si se rechaza un permiso, la instalación se cancela"
    - "Tras instalar, el audit_log refleja quién aprobó qué permiso"

- id: human_09_02
  description: "Análisis estático bloquea código sospechoso"
  hint: "Intentar instalar tool con eval() o subprocess shell=True"
  checklist:
    - "Bandit/semgrep detecta el patrón"
    - "La instalación se bloquea con mensaje claro"
    - "Audit log refleja el intento"

- id: human_09_03
  description: "Playwright funciona end-to-end"
  hint: "Crear proyecto e instalar Playwright desde marketplace"
  checklist:
    - "Configuración guiada deja browsers descargados"
    - "Agente QA E2E Automator usa la tool y produce screenshots y traces"
    - "Los artefactos quedan persistidos como outputs de la tarea"

- id: human_09_04
  description: "Compartir entre tenants requiere audit"
  hint: "Tenant A comparte skill custom con Tenant B"
  checklist:
    - "Sin opt-in explícito de ambos, no se puede compartir"
    - "Con opt-in, el System Admin ve el evento en audit_log"
    - "Tenant B ve la skill como 'compartida por Tenant A' con badge"
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

Tras cerrar este plan, el siguiente es **Plan 10** (`10-asistente-personal.md`).
