---
plan_id: 09-marketplace
title: Marketplace de Skills y Tools
status: pending_human_validation
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
| **Bloqueado por**                  | `05-mcp-tools-avanzadas`                  |
| **Tiempo estimado (calendario)**   | 3-4 semanas                               |
| **Tiempo estimado (persona-días)** | 60-80                                     |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 120 € – 200 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/09-marketplace`                     |
| **Secciones del .docx**            | [32]                                      |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

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

- [x] **Título**: Formato estándar de tool (manifest YAML + implementación) <!-- task_09_10: parser/validador del manifest YAML de tool en marketplace/tool_format.py (ToolManifest + ToolImplementation + parse_tool_manifest -> ToolFormatError tipado). Campos: name/version(semver)/description/kind(MarketplaceListingKind)/entrypoint/implementation(runtime+module+reference) + dependencies + input_schema/output_schema + permissions. Vocabulario de permisos y semver COMPARTIDOS con 09_09 vía nuevo helper marketplace/_format_common.py (is_valid_semver + parse_permissions_block + requested_permission_descriptors) — skill_format.py refactorizado para reusarlo, sin duplicar; .requested_permissions renderiza los descriptores {"type","value"} que consumen consent/install. PyYAML (dep existente), sin migración (reutiliza manifest/requested_permissions JSONB). Tests tests/unit/test_tool_manifest_format.py (44 pass); suite skill 09_09 sigue verde (regresión-cero). NOTA: el comando YAML del test apunta a test_tool_format.py pero el bloque de tarea exige test_tool_manifest_format.py — se respeta el bloque de tarea. -->
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
    command: "pytest tests/unit/test_tool_manifest_format.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_11` — Proceso de instalación de skill/tool: descarga, verificación firma, análisis, sandbox, persistencia en tenant

- [x] **Título**: Proceso de instalación de skill/tool: descarga, verificación firma, análisis, sandbox, persistencia en tenant <!-- task_09_11: orquestación end-to-end en marketplace/install.py (InstallOrchestrator) que encadena las puertas que implica la trust_policy (09_04): (1) FETCH tras ArtifactFetcher Protocol — LocalArtifactFetcher lee el artefacto en disco, sin red (los tests inyectan fixture; el fetch HTTP/git real queda pendiente del runtime de registry); (2) PARSE SKILL.md/tool manifest (09_09/09_10); (3) VERIFY SIGNATURE con cryptography Ed25519 cuando signature_required (verified) — artefacto manipulado/sin firmar RECHAZADO, la firma nunca se devuelve; (4) STATIC ANALYSIS (09_05) bloquea si supera max_allowed_severity; (5) SANDBOX smoke test (09_06, Docker MOCKEADO en tests; contenedor real pendiente de imagen runtime) cuando sandbox_required; (6) CONSENT (09_07) — community/experimental nacen DISABLED hasta conceder permisos; verified instala ENABLED; (7) PERSIST install + audit_entries, todo tenant-scoped (get_tenant_session + RLS). Cada fallo de puerta aborta con error tipado (InstallError jerarquía) + audit entry COMMITeada + SIN install habilitado. SIN migración (reutiliza columnas status/granted/denied y audit.detail JSONB existentes). Tests tests/integration/test_install_flow.py (7 pass, @pytest.mark.cross_tenant) — keypair Ed25519 real en test, firma fixture, verifica, artefacto manipulado rechazado. Suite marketplace completa verde (221 pass, 3 skip semgrep). NOTA: el endpoint POST /marketplace/installations (Fase A) sigue persistiendo directamente; cablear el orquestador en el endpoint requiere artefactos en disco por listing (rompería los seeds de test_marketplace_endpoints/consent/revocation), por lo que la integración viva del endpoint queda como paso pendiente del runtime de catálogo. -->
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

- [x] **Título**: Versionado semver y updates <!-- task_09_12: lógica semver pura en marketplace/versioning.py (parse_version/compare_versions/is_outdated/is_major_bump/latest_version + select_update_target -> UpdateAssessment) sobre la lib pip-clean "packaging" (dep existente, sin dep pesada nueva); gatea el strict-semver con is_valid_semver de _format_common (09_09/09_10) para rechazar lo que packaging aceptaría laxamente (p.ej. "1.2"). Compatibilidad: select_update_target NUNCA salta un MAJOR sin allow_major explícito (decisión vinculante) — propone el MINOR/PATCH más alto del mismo major y marca latest_is_major_bump. Update path en InstallOrchestrator.update(): re-ejecuta las MISMAS puertas del install (09_11: fetch/parse/firma Ed25519/análisis estático/sandbox) contra el artefacto de la nueva versión, re-apunta installation.listing_id+version y escribe audit_entry action="update" con el diff de versión + gate trail; un fallo de puerta aborta (InstallError) bajo action="update" (abort_action ahora configurable en _GateContext) dejando el install en su versión vieja. Endpoints: GET .../update-check (outdated + target compat + flag major; read-only, RLS) y POST .../update (allow_major opt-in + target_version pinned; tenant_admin) — orquestador inyectable vía dependency get_install_orchestrator. SIN migración (reutiliza columnas version + acción UPDATE del enum existentes). Tests tests/integration/test_marketplace_versioning.py (22 pass, @pytest.mark.cross_tenant): semver compare/order, outdated, major-bump opt-in, update re-corre puertas (artefacto manipulado rechazado, install se queda en 1.0.0), audita, cross-tenant + capa REST. Suite marketplace completa verde (243 pass, 3 skip semgrep). NOTA: el POST .../update vive correcto en el camino feliz (el orquestador solo flush/refresh dentro del session.begin() del request); el camino de aborto del orquestador hace commit intra-request (igual que 09_11), por lo que su integración viva en el endpoint comparte el mismo paso pendiente del runtime de catálogo que el install endpoint — el aborto se valida conduciendo el orquestador directamente. -->
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

- [x] **Título**: Tool Playwright detallada con configuración guiada (browsers, headless, screenshots, traces) <!-- task_09_13: Playwright como tool DESTACADA = listing GLOBAL verificado (tenant_id NULL, modelo híbrido Fase A) en marketplace/playwright.py. (1) PLAYWRIGHT_TOOL_YAML — manifest en el MISMO formato estándar de tool (09_10): name/version(semver)/description/kind=tool/entrypoint/implementation(runtime=node-playwright)/dependencies/input+output schema/permissions (allowed_domains de los sitios bajo prueba + network_policy=restricted); parsea por parse_tool_manifest compartido (no parser propio). (2) PlaywrightToolConfig (config GUIADA tipada + from_dict/to_dict) — browsers (chromium/firefox/webkit, multi-select dedup-orden), headless (bool), screenshots (off/on/only-on-failure), traces (off/on/retain-on-failure), base_url, timeout_ms (entero>0); rechaza browser/screenshot/trace/timeout inválidos + clave desconocida + no-mapping. config_schema() emite el descriptor JSON-Schema-ish que la UI renderiza y que el manifest embebe bajo manifest.config_schema. (3) seed_playwright_listing() — loader IDEMPOTENTE (upsert por source+tenant_id=NULL+name+version) que registra el listing VERIFIED GLOBAL bajo la official source (ensure_official_source); requiere sesión publisher BYPASSRLS (escribir tenant_id NULL está vedado a sesiones de tenant por RLS WITH CHECK). SIN migración (listing es fila; config_schema viaja en manifest JSONB). UI: /admin/marketplace/listings/[id]/playwright-config (form guiado client-side que lee config_schema del listing, valida en cliente igual que PlaywrightToolConfig). Tests tests/integration/test_playwright_tool.py (21 pass): manifest parsea+valida, config acepta válido + rechaza browser/screenshot/trace/timeout malos, seed inserta listing VERIFIED GLOBAL idempotente, @pytest.mark.cross_tenant el listing global es visible a ambos tenants vía marketplace_listings_global_read. pre-commit (black/ruff/mypy/prettier) + admin-panel typecheck/lint/build VERDES. e2e apps/admin-panel/e2e/playwright-tool-config.spec.ts ESCRITO pero NO ejecutado — node-playwright sin navegador (PENDING HUMAN VERIFICATION; npx playwright test e2e/playwright-tool-config.spec.ts). -->
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_09_13_a
    description: "Tool Playwright detallada con configuración guiada (browsers, headless, screenshots, traces)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_playwright_tool.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_09_14` — Agente plantilla 'QA E2E Automator' que usa Playwright

- [x] **Título**: Agente plantilla 'QA E2E Automator' que usa Playwright <!-- task_09_14: plantilla de agente GLOBAL 'QA E2E Automator' en seeds/qa_e2e_automator.py, REUTILIZANDO el MISMO modelo de agente de Plan 01/02/03 (tabla agents: scope='global_builtin', is_template=true, tenant_id=PLATFORM_TENANT_ID, prompts bilingües es/en en model_config.system_prompts) vía el dataclass BuiltinAgent + el _UPSERT_SQL compartido — NO un sistema de plantillas paralelo. Rol qa + prompt coherente (escribe y ejecuta specs Playwright .spec.ts, flujos login/signup/checkout, config guiada browsers/headless/screenshots/traces, sesgo a romper, respeta allowed_domains/network_policy multi-tenant). Referencia a la tool Playwright de 09_13 por la IDENTIDAD del listing de marketplace (name=playwright + version=1.0.0 + kind=tool) en model_config.marketplace_tools — Playwright es una fila marketplace_listings (no tools.id), así que no se cablea por el junction agent_tools. Loader idempotente seed_qa_e2e_automator (id uuid5 estable) cableado en seeds/__main__.py tras seed_builtin_agents; queda FUERA de BUILTIN_AGENTS para preservar el conteo de 11 built-ins de Plan 01 (es el 12º global_builtin). SIN migración (es una fila agents; la ref viaja en model_config JSONB). La plantilla es GLOBAL (visible a todo tenant vía agents_global_builtin_read, inmutable por sesiones tenant); la tool referenciada es listing GLOBAL tenant_id NULL (modelo híbrido Fase A). Tests tests/integration/test_qa_e2e_automator.py (6 pass, @pytest.mark.cross_tenant): valida contra el schema agent/template, referencia Playwright, prompt QA coherente bilingüe, re-seed idempotente, es el 12º global_builtin, visible a sesión tenant RLS. pre-commit (black/ruff/mypy) VERDE; sin tocar admin-panel. NOTA: el test runtime de la tarea es python-pytest (no e2e); no se requiere ni escribe .spec.ts nuevo aquí. -->
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

- [x] **Título**: Plantillas de tests E2E pre-cargadas (login, signup, checkout, etc.) <!-- task_09_15: registro CURADO + VERSIONADO de plantillas de specs Playwright E2E en marketplace/e2e_templates.py (modelo E2ETestTemplate + E2ETemplateParameter tipados, frozen/slots). 5 plantillas builtin para los flujos comunes: login, signup, checkout (los que nombra la tarea) + search + form-submit. Cada plantilla = skeleton de spec .spec.ts BIEN FORMADO y PARAMETRIZADO: URLs/selectores son parámetros {{declarados}}, no hard-coded. Loader load_e2e_templates() + get_e2e_template(name) (nombre desconocido -> E2ETemplateError tipado). Validación E2ETestTemplate.validate(): semver válido (reutiliza is_valid_semver de _format_common 09_09/09_10), nombres de parámetro únicos y NO vacíos, y acuerdo body<->params (cada {{placeholder}} está declarado y cada parámetro declarado se usa — sin substitución no declarada ni knob muerto). instantiate(values) substituye el mapping del operador (default si el parámetro lo tiene; required ausente -> error; clave desconocida -> error) dejando el spec SIN marcadores {{...}}. CONTENIDO curado de plataforma (sin tenant, mismo estatus que el listing Playwright GLOBAL — modelo híbrido Fase A): puro, importable, sin I/O, SIN tabla nueva, SIN migración, SIN frontera cross-tenant (sin superficie RLS). Re-exportado desde marketplace/__init__.py. Tests tests/integration/test_e2e_test_templates.py (python-pytest, 13 pass): todas las builtin cargan+validan, cada una declara sus parámetros, login/signup/checkout presentes, instanciación substituye correctamente + defaults + required-ausente/clave-desconocida errores, plantilla desconocida error, validación rechaza placeholder no declarado / parámetro muerto / semver malo. pre-commit (black/ruff/mypy/prettier) + admin-panel typecheck/lint/build VERDES. e2e apps/admin-panel/e2e/playwright-templates.spec.ts ESCRITO pero NO ejecutado — node-playwright sin navegador (PENDING HUMAN VERIFICATION; npx playwright test e2e/playwright-templates.spec.ts). NOTA: el runtime real de la tarea para verificar las definiciones es python-pytest (test_e2e_test_templates.py); el comando YAML node-playwright cubre el .spec.ts pendiente de humano. -->
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

- [x] **Título**: Marketplace privado del tenant para skills/tools internas <!-- task_09_16: marketplace privado del tenant (BACKEND + FRONTEND). Un tenant publica sus PROPIAS skills/tools internas como listings PRIVADOS (tenant_id = tenant del caller; el modelo híbrido + RLS de la Fase A ya lo soportan). BACKEND (routers/marketplace.py): POST /marketplace/private/listings (publicar) + PUT .../{id} (actualizar) + DELETE .../{id} (despublicar) — RBAC tenant_admin, RLS-scoped; el manifest se VALIDA con los parsers de la Fase C (skill_format para skill / tool_format para tool|mcp_server) vía el nuevo adaptador puro marketplace/private_listing.py (parse_private_listing -> ParsedPrivateListing; PrivateListingFormatError -> 422, NO se crea fila). tenant_id (= caller), la fuente privada (_ensure_private_source: source_type=private, owner_tenant_id=caller, idempotente, nombre único por tenant) y el trust_level (community) son SIEMPRE derivados en servidor — nunca del wire, así que un listing privado no puede falsificarse como global/verified (RLS WITH CHECK rechaza cualquier tenant_id ajeno). El browse (GET /marketplace/listings) ya devuelve catálogo global + privados propios del caller (RLS), NUNCA los privados de otro tenant. Auditoría append-only (action=share, detail.event=private_publish/update/unpublish). SIN migración (reutiliza tablas/columnas de la Fase A; head único 0043 sin cambios). FRONTEND: /admin/marketplace/private (TS strict, RoleGuard tenant_admin para publicar/despublicar, lib/api.ts, shadcn/ui) que lista solo los privados propios (filtra tenant_id != null) y publica pegando el manifest. Tests tests/integration/test_private_marketplace.py (6 pass): publish crea listing privado (skill + tool), browse muestra propio-privado + global pero NO el privado de otro tenant (@pytest.mark.cross_tenant — incluye 404 cross-tenant en detail/update/delete), RBAC niega a tenant_user (403), manifest malo rechazado (422, sin fila), update/unpublish solo el propio. pre-commit (black/ruff/mypy/prettier) + admin-panel typecheck/lint/build VERDES. e2e apps/admin-panel/e2e/private-marketplace.spec.ts ESCRITO pero NO ejecutado — node-playwright sin navegador (PENDING HUMAN VERIFICATION; npx playwright test e2e/private-marketplace.spec.ts). -->

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

- [x] **Título**: Compartir recursos entre tenants (opt-in con audit del System Admin) <!-- task_09_17: compartir cross-tenant OPT-IN + audit del System Admin (BACKEND). Un tenant OWNER comparte uno de sus listings PRIVADOS (09_16) con un único tenant TARGET; el target ve/instala el listing SOLO vía el grant; el System Admin audita todo. NUNCA es un bypass implícito de RLS. NUEVA tabla marketplace_shares (db/marketplace.py: listing_id + owner_tenant_id + target_tenant_id + granted_by/revoked_at/revoked_by + soft-delete; índice único parcial de share VIVO por (listing,target)). Migración 0044_marketplace_shares REVERSIBLE (head único 0044; downgrade objetivo de prueba 0040_sso_email_domains): (1) RLS dual-scope en marketplace_shares — política FOR ALL marketplace_shares_owner_manage (USING+WITH CHECK owner_tenant_id=tenant actual: el OWNER crea/lista/revoca solo sus grants, no puede falsificar owner) + política FOR SELECT marketplace_shares_target_read (el TARGET LEE los grants que le nombran, sin gestionarlos); (2) NUEVA política FOR SELECT ADITIVA marketplace_listings_shared_read en marketplace_listings — expone el listing al tenant actual SOLO si EXISTS un share VIVO (deleted_at NULL AND revoked_at NULL) que se lo concede; revocar elimina la visibilidad de inmediato; sin ruta de escritura para el target. El System Admin (sesión BYPASSRLS) ve TODOS los shares. Endpoints (routers/marketplace.py): POST /marketplace/shares (owner tenant_admin; rechaza compartir consigo mismo 422, listing ajeno/global 404 vía _load_private_listing+RLS, duplicado vivo o tenant target inexistente 409 vía índice+FK), GET /marketplace/shares (owner tenant_admin, RLS owner-scope), DELETE /marketplace/shares/{id} (owner tenant_admin, revoca+soft-delete, ajeno 404), y GET /admin/marketplace/shares (admin_router nuevo en require_system_admin + get_admin_session BYPASSRLS — enumera TODOS los shares para audit). Cada share/revoke escribe marketplace_audit_entry append-only action=share (detail.event=cross_tenant_share / cross_tenant_share_revoke) en la MISMA transacción. Default = nada compartido. Secretos/firmas nunca devueltos (el share nombra el listing, no lo embebe). Tests tests/integration/test_cross_tenant_sharing.py (7 pass, @pytest.mark.cross_tenant): el share hace el listing visible+instalable SOLO al target (bystander no lo ve), revoke quita visibilidad (+re-share permitido), share/revoke auditados, el System Admin enumera todos (no-admin 403), default sin compartir, RBAC tenant_user 403, no compartir/revocar cross-ownership. Migración up/down/up verde (test_marketplace_migration.py 11 pass). pre-commit (black/ruff/mypy) VERDE; suite marketplace sin regresión (70 pass en models+private+endpoints+consent+revocation). BACKEND-only: el runtime de la tarea es python-pytest; sin tocar admin-panel y sin .spec.ts nuevo (la UI cross-tenant es 09_18). -->
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

- [x] **Título**: UI de gestión del marketplace por Tenant Admin <!-- task_09_18: área cohesiva del admin-panel para que un Tenant Admin gestione el marketplace de su tenant (FRONTEND). Nueva página /admin/marketplace (apps/admin-panel/app/admin/marketplace/page.tsx) con 3 pestañas (Tabs primitive existente) + enlace a Privadas: (1) Catálogo — browse de GET /marketplace/listings (catálogo global público + privados propios del caller vía RLS; badge global/privado por tenant_id null/non-null; nunca muestra privados ajenos), enlaza a la config guiada de Playwright (09_13) cuando name=playwright+kind=tool; (2) Instaladas — GET /marketplace/installations, enlaza al consentimiento granular (09_07 /installations/[id]/permissions sin duplicar la UI), revoca (POST .../revoke) y desinstala (DELETE) gateado en <RoleGuard min=tenant_admin>; (3) Compartir — gestiona los shares cross-tenant del OWNER (09_17): crea (POST /marketplace/shares; el picker SOLO ofrece listings privados propios — un global ya es visible para todos, nada que compartir) y revoca (DELETE /marketplace/shares/{id}); copy explícito de que compartir es opt-in + auditado por el System Admin y NUNCA un bypass implícito de RLS (el target ve el listing solo vía el grant vivo). Reutiliza lib/api.ts (apiFetch), TanStack Query, shadcn/ui, RoleGuard, PageHeader, Badge; TS strict (sin any; tipos espejo de schemas/marketplace). Enlaza (no duplica) consent 09_07, Playwright config 09_13 y el marketplace privado 09_16. Sidebar: nuevo item Marketplace (admin-only, icon Store) en admin-shell.tsx. SIN migración (FRONTEND-only; reutiliza endpoints Fase A-E). VERIFY: cd apps/admin-panel && npm run typecheck && lint (solo warnings preexistentes ajenos) && build VERDES (/admin/marketplace en el route table); pre-commit (prettier) VERDE. e2e apps/admin-panel/e2e/marketplace-admin.spec.ts ESCRITO pero NO ejecutado — node-playwright sin navegador (PENDING HUMAN VERIFICATION; npx playwright test e2e/marketplace-admin.spec.ts): mock offline de /me + listings + installations + shares; drive de las 3 pestañas (catálogo lista global+privado y enlaza Playwright config; instaladas enlaza consent + revoke + uninstall; compartir crea grant explícito listing_id+target_tenant_id y revoca). -->
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

- [x] **Título**: Documentación + ADRs + changelog <!-- task_09_19: TECHNICAL WRITER (docs-only, sin product code). (1) docs/07-changelog/09-marketplace.md creado (estilo de 08-sso-empresarial.md): Resumen + Cambios por tarea 09_01..09_19 + tablas de Endpoints/Migraciones 0041..0044/variables-dependencias (bandit dev-dep; semgrep + docker opcionales/lazy) + Decisiones + Pendiente (e2e Playwright sin ejecutar; sandbox real-container; semgrep opcional; aborto install/update vs runtime de catálogo; persistir config guiada + bootstrap de seeds) + tests humanos pendientes + Verificacion (suite marketplace completa verde) + PR pendiente. (2) ADR 0032 (siguiente libre tras 0031) — tres decisiones no registradas: nivel de confianza gobierna guardrails no disponibilidad; catálogo híbrido global/privado + compartir por grant explícito y auditado (nunca bypass RLS); pipeline de instalación gated fail-closed (firma/análisis/sandbox/consent por permiso) + auditoría append-only. (3) docs/04-reference/marketplace.md creado (estilo rbac.md/auth-sso.md): tablas de endpoints con RBAC, niveles de confianza, garantías de seguridad (RLS, firma, análisis estático, sandbox, consent), formatos SKILL.md + manifest de tool, cross-link a ADR 0032/0001/0031. (4) roadmap: 09_19 [x], 09_01..09_18 verificados [x], frontmatter status: pending_human_validation + celda Estado de la Cabecera. pre-commit (prettier/markdown) VERDE; docs/07-changelog/09-marketplace.md existe (auto_09_19_a). -->
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
