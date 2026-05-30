---
plan_id: 13-api-publica-webhooks
title: API Pública, Webhooks Entrantes y Eventos Externos
status: in_progress
blocking_plan: [01-dominio-minimo]
started_at: 2026-05-30
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 60-80
estimated_cost_human_eur: 24.000 € – 32.000 €
estimated_cost_ai_eur: 120 € – 200 €
created_by: system_architect
spec_sections_referenced: [26]
docs_language: es
---

# Plan 13 — API Pública, Webhooks Entrantes y Eventos Externos

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `13-api-publica-webhooks`                 |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `01-dominio-minimo`                       |
| **Tiempo estimado (calendario)**   | 3-4 semanas                               |
| **Tiempo estimado (persona-días)** | 60-80                                     |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 120 € – 200 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/13-api-publica-webhooks`            |
| **Secciones del .docx**            | [26]                                      |

---

## Descripción Detallada

### Resumen Ejecutivo

API REST pública versionada v1 con X-API-Token por tenant (scope limitado al propio tenant). Webhooks entrantes con plantillas pre-configuradas (GitHub push, Jira issue, Sentry error → tareas). SDK Python y TypeScript.

### Contexto

Hasta aquí el sistema es una isla. Esta fase lo conecta a las herramientas del tenant: CI, CRM, issue trackers, monitoring.

### Alcance

**Entra en este plan**:

- Modelo ApiToken con scope, vigencia, rate_limit, IP allowlist opcional.
- Endpoint admin para Tenant Admin crear/listar/revocar tokens.
- Middleware de validación X-API-Token (scope al tenant del token).
- Endpoints REST públicos versionados v1: /api/v1/projects, /api/v1/plans, /api/v1/tasks, /api/v1/conversations, /api/v1/kbs.
- Documentación OpenAPI 3.1 publicada en /api/v1/openapi.json + Swagger UI.
- Webhooks entrantes con HMAC verificable (configurable por origen).
- Plantillas pre-configuradas: GitHub push → crear/actualizar PR review task, GitHub PR review → crear tarea de respuesta, Jira issue creado → crear tarea, Sentry error → crear bug task, Linear issue, GitLab MR.
- Configuración por proyecto: qué webhooks acepta y a qué se mapean.
- SDK Python oficial.
- SDK TypeScript oficial.
- Rate limiting por token (default 100 req/min, configurable).

**Queda fuera (otras fases)**:

- Federación entre instalaciones (cada instalación es isla).
- GraphQL API (REST es suficiente).

### Decisiones Clave

- X-API-Token en header (no query param) por seguridad.
- Versionado v1 en path (/api/v1/...) en lugar de header (más explícito).
- Webhooks entrantes siempre por HTTPS y con firma HMAC obligatoria.

### Riesgos Identificados

| Riesgo                                       | Probabilidad | Impacto | Mitigación                                                                           |
| -------------------------------------------- | ------------ | ------- | ------------------------------------------------------------------------------------ |
| Token comprometido da acceso total al tenant | Media        | Alto    | TTL configurable, revocación inmediata, IP allowlist opcional, rotación recomendada. |
| Webhooks entrantes pueden ser DDoS vector    | Media        | Medio   | Rate limiting agresivo + circuit breaker.                                            |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Tokens y Autorización

#### `task_13_01` — Modelo ApiToken con scope, vigencia, rate_limit, IP allowlist

- [x] **Título**: Modelo ApiToken con scope, vigencia, rate_limit, IP allowlist
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_13_01_a
    description: "Modelo ApiToken con scope, vigencia, rate_limit, IP allowlist"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_api_token_model.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_02` — Endpoint admin del tenant para crear/listar/revocar tokens

- [x] **Título**: Endpoint admin del tenant para crear/listar/revocar tokens
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_02_a
    description: "Endpoint admin del tenant para crear/listar/revocar tokens"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_api_tokens_admin.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_03` — Middleware X-API-Token con cache Redis

- [x] **Título**: Middleware X-API-Token con cache Redis
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_13_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_03_a
    description: "Middleware X-API-Token con cache Redis"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_api_token_middleware.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_04` — Rate limiting por token con sliding window en Redis

- [x] **Título**: Rate limiting por token con sliding window en Redis
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_04_a
    description: "Rate limiting por token con sliding window en Redis"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_api_rate_limit.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Endpoints v1

#### `task_13_05` — Endpoints REST públicos: /api/v1/projects, /plans, /tasks, /conversations, /kbs

- [x] **Título**: Endpoints REST públicos: /api/v1/projects, /plans, /tasks, /conversations, /kbs
- **Tiempo estimado**: 16 h
- **Complejidad**: l
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_13_05_a
    description: "Endpoints REST públicos: /api/v1/projects, /plans, /tasks, /conversations, /kbs"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_api_v1_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_06` — Documentación OpenAPI 3.1 + Swagger UI

- [x] **Título**: Documentación OpenAPI 3.1 + Swagger UI
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_06_a
    description: "Documentación OpenAPI 3.1 + Swagger UI"
    check_type: automated
    runtime: generic-shell
    command: "curl -f http://api-server:8000/api/v1/openapi.json"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_07` — Versionado: header X-API-Version opcional + tracking de uso por versión

- [x] **Título**: Versionado: header X-API-Version opcional + tracking de uso por versión
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_07_a
    description: "Versionado: header X-API-Version opcional + tracking de uso por versión"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_api_versioning.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Webhooks Entrantes

#### `task_13_08` — Endpoint /webhooks/incoming/{origin}/{secret} con verificación HMAC

- [ ] **Título**: Endpoint /webhooks/incoming/{origin}/{secret} con verificación HMAC
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_13_08_a
    description: "Endpoint /webhooks/incoming/{origin}/{secret} con verificación HMAC"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_webhook_signature.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_09` — Plantillas pre-configuradas: GitHub push, PR review, Jira issue, Sentry error, Linear issue, GitLab MR

- [ ] **Título**: Plantillas pre-configuradas: GitHub push, PR review, Jira issue, Sentry error, Linear issue, GitLab MR
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_09_a
    description: "Plantillas pre-configuradas: GitHub push, PR review, Jira issue, Sentry error, Linear issue, GitLab MR"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_webhook_templates.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_10` — Mapeo webhook → acción del sistema (crear tarea, comentar tarea, escalar)

- [ ] **Título**: Mapeo webhook → acción del sistema (crear tarea, comentar tarea, escalar)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_10_a
    description: "Mapeo webhook → acción del sistema (crear tarea, comentar tarea, escalar)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_webhook_mapping.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_11` — UI configuración de webhooks entrantes por proyecto

- [ ] **Título**: UI configuración de webhooks entrantes por proyecto
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_13_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_11_a
    description: "UI configuración de webhooks entrantes por proyecto"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/webhooks-incoming.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_12` — Replay desde audit (debugging)

- [ ] **Título**: Replay desde audit (debugging)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_13_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_12_a
    description: "Replay desde audit (debugging)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_webhook_replay.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — SDKs y Cierre

#### `task_13_13` — SDK Python con tipos generados (autogenerado desde OpenAPI con openapi-python-client)

- [ ] **Título**: SDK Python con tipos generados (autogenerado desde OpenAPI con openapi-python-client)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_13_13_a
    description: "SDK Python con tipos generados (autogenerado desde OpenAPI con openapi-python-client)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_sdk_python.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_14` — SDK TypeScript con tipos generados (openapi-typescript-codegen)

- [ ] **Título**: SDK TypeScript con tipos generados (openapi-typescript-codegen)
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_13_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_14_a
    description: "SDK TypeScript con tipos generados (openapi-typescript-codegen)"
    check_type: automated
    runtime: node-vitest
    command: "npm test -- sdk-typescript"
    expected_signal: "exit_code == 0"
  ```

#### `task_13_15` — Documentación + ejemplos de uso + ADRs + changelog

- [ ] **Título**: Documentación + ejemplos de uso + ADRs + changelog
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_13_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_13_15_a
    description: "Documentación + ejemplos de uso + ADRs + changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/13-api-publica-webhooks.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_13_01
  description: "Token funciona y respeta scope"
  hint: "Crear token de Tenant A e intentar acceder a recursos de Tenant B"
  checklist:
    - "Con el token, se puede listar proyectos del propio tenant"
    - "Intentar acceder a /api/v1/projects con ID de otro tenant devuelve 404"
    - "Si el token tiene IP allowlist, conexión desde IP no autorizada falla"

- id: human_13_02
  description: "Webhook GitHub crea tareas"
  hint: "Configurar webhook desde GitHub real apuntando a /webhooks/incoming/github/..."
  checklist:
    - "Push a branch crea tarea de revisión en el proyecto correspondiente"
    - "PR opened crea tarea de revisión técnica"
    - "Issues crean tareas automáticas"
    - "Si la firma HMAC falla, devuelve 401"

- id: human_13_03
  description: "Rate limiting funciona"
  hint: "Hacer >100 req/min con el mismo token"
  checklist:
    - "La request 101 devuelve 429 Too Many Requests"
    - "Header X-RateLimit-Remaining decrementa correctamente"
    - "Tras 60s la ventana se reinicia"

- id: human_13_04
  description: "SDK Python es usable"
  hint: "Pip install el SDK y ejecutar ejemplo del README"
  checklist:
    - "pip install funciona desde el registry interno"
    - "Ejemplo del README ejecuta sin errores"
    - "Type hints disponibles en IDE"
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

Tras cerrar este plan, el siguiente es **Plan 14** (`14-evals-estadisticas.md`).
