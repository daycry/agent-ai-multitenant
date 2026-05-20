---
plan_id: 00-fundaciones
title: Fundaciones del Sistema
status: in_progress
blocking_plan: null
started_at: 2026-05-20
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 60-75
estimated_cost_human_eur: 24000-30000
estimated_cost_ai_eur: 80-150
created_by: system_architect
spec_sections_referenced: [4, 17, 18, 21, 24]
docs_language: es
---

# Plan 00 — Fundaciones del Sistema

## Cabecera

| Campo                              | Valor                                                               |
| ---------------------------------- | ------------------------------------------------------------------- |
| **ID del Plan**                    | `00-fundaciones`                                                    |
| **Estado**                         | `in_progress`                                                       |
| **Bloqueado por**                  | — (es el plan inicial)                                              |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                                         |
| **Tiempo estimado (persona-días)** | 60-75                                                               |
| **Previsión de coste — humano**    | 24.000 € – 30.000 € (con tarifa media 50 €/h)                       |
| **Previsión de coste — IA**        | 80 € – 150 € (Claude Code asistiendo el desarrollo)                 |
| **Ahorro estimado**                | ~99% (la mayor parte del trabajo es asistencia, no autonomía total) |
| **Aprobador propuesto**            | System Admin                                                        |
| **Rama git**                       | `plan/00-fundaciones`                                               |

---

## Descripción Detallada

### Resumen Ejecutivo

Crear el cimiento del sistema agéntico multi-tenant. Esta fase NO añade funcionalidad de producto visible; añade la infraestructura sobre la que todas las fases posteriores van a construir. Al cerrar este plan, un operador puede levantar el sistema con `docker compose up -d` en una máquina nueva, registrarse como System Admin desde el panel de administración, crear un primer tenant, asignar un Tenant Admin, y entrar a la app vacía sin errores ni inconsistencias de seguridad.

### Contexto

El sistema se ejecuta como un stack Docker Compose en una sola máquina (modelo mono-máquina, sección 21 del documento maestro). El multi-tenancy es a nivel de departamentos o equipos, no SaaS comercial masivo, pero sigue siendo crítico desde el día uno: cualquier filtración cross-tenant es inaceptable. Por eso esta fase invierte tiempo desproporcionado en los cimientos: aislamiento, auth correcta, healthchecks, logging trazable.

### Alcance

**Entra en este plan**:

- Setup del monorepo con estructura definida.
- Stack Docker Compose con servicios base (Postgres+pgvector, Redis, MinIO, Vault, ClamAV).
- FastAPI esqueleto con auth JWT, sesiones server-side en Redis.
- Modelo multi-tenant con RLS PostgreSQL desde el día uno.
- 4 roles base: System Admin, System Operator, Tenant Admin, Tenant User.
- Panel admin Next.js esqueleto con dashboard de salud del sistema.
- Healthchecks Docker + watchdog interno con backoff exponencial.
- Logging estructurado JSON y OpenTelemetry.
- CI/CD básico (lint, tests unitarios, build de imágenes).

**Queda fuera (otras fases)**:

- Cualquier modelo de dominio (agentes, equipos, proyectos, etc.) → Fase 01.
- Ejecución de agentes → Fase 02.
- SSO empresarial avanzado (OIDC/SAML/SCIM) → Fase 08.
- Backup automatizado con UI → Fase 12 (en esta fase solo `pg_dump` manual por cron).
- Instalador con UI tipo wizard → Fase 15.

### Supuestos

- La máquina objetivo tiene Docker 24+ y Docker Compose v2+.
- El operador tiene acceso SSH al host y permisos sudo para crear `/data/agent-platform/`.
- El stack se accede desde red local interna (sin TLS público en esta fase; se añade en Fase 15 con instalador).
- Los recursos mínimos asumidos son los del perfil "Recomendado" (16 CPU, 32 GB RAM), aunque el perfil "Mínimo" (8 CPU, 16 GB) debe funcionar para esta fase.

### Decisiones Clave

- **Sesiones server-side en Redis, no JWT stateless**: permite revocación inmediata y mejor auditoría. Coste: dependencia explícita de Redis para auth, pero ya es dependencia del sistema.
- **RLS PostgreSQL desde el día uno**: defensa en profundidad contra fugas cross-tenant por bugs en la aplicación. Cada query lleva implícitamente el filtro de tenant_id.
- **Vault desde el día uno** para credenciales generadas automáticamente al primer arranque, no .env con secretos.
- **Argon2id** para hash de passwords (no bcrypt, no scrypt). Parámetros conservadores.

### Riesgos Identificados

| Riesgo                                   | Probabilidad | Impacto | Mitigación                                                                                                   |
| ---------------------------------------- | ------------ | ------- | ------------------------------------------------------------------------------------------------------------ |
| Bug en RLS deja escape cross-tenant      | Media        | Crítico | Suite de tests automáticos de aislamiento obligatorios en CI antes de cerrar fase.                           |
| Pérdida de unseal keys de Vault          | Baja         | Crítico | Documentar procedimiento de backup de unseal keys en runbook obligatorio.                                    |
| Configuración de Docker insegura en host | Media        | Alto    | Validación de prerequisitos al arrancar (Docker, permisos, capabilities); script `scripts/validate-host.sh`. |
| Logs filtran PII                         | Media        | Alto    | Filtros de logging activos desde el día uno con detección de patrones (email, tokens, IBAN).                 |

---

## Tareas

> Cada tarea tiene checkbox, dependencias, complejidad y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Setup del Monorepo y CI/CD

Objetivo: dejar el repositorio estructurado y arrancable con pipelines mínimos.

#### `task_00_01` — Estructura del Monorepo

- [x] **Título**: Crear estructura monorepo según convención
- **Descripción**: Crear el árbol de directorios `apps/`, `packages/`, `docker/`, `docs/`, `scripts/`, `tests/`. Inicializar Git con `main` como rama default. Añadir `.gitignore` apropiado para Python + Node + Docker.
- **Tiempo estimado**: 4 h
- **Complejidad**: xs
- **Rol sugerido**: devops
- **Dependencias**: ninguna
- **Tests automáticos**:
  ```yaml
  - id: auto_00_01_a
    description: "Estructura de directorios canónica existe"
    check_type: automated
    runtime: generic-shell
    command: "test -d apps && test -d packages && test -d docker && test -d docs"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_02` — Pre-commit Hooks y Linters

- [x] **Título**: Configurar pre-commit con black, ruff, mypy, prettier, eslint
- **Descripción**: Configurar `.pre-commit-config.yaml` con hooks para los formatters y linters de Python y TypeScript. Configurar `pyproject.toml` con configuración de black, ruff, mypy (modo strict). Configurar `.eslintrc.json` y `.prettierrc`.
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_00_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_02_a
    description: "pre-commit run --all-files pasa sin errores en código de ejemplo"
    check_type: automated
    runtime: generic-shell
    command: "pre-commit run --all-files"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_03` — Pipeline CI Básico

- [ ] **Título**: Configurar GitHub Actions (o GitLab CI) con jobs lint, test, build
- **Descripción**: Pipeline que en cada push corre: lint Python (black + ruff + mypy), lint TS (prettier + eslint), unit tests, integration tests contra docker-compose de test, build de imágenes Docker. Cache de dependencias entre ejecuciones.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_00_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_03_a
    description: "El workflow CI valida la sintaxis al hacer push de prueba"
    check_type: automated
    runtime: generic-shell
    command: "actionlint .github/workflows/ci.yml || gitlab-ci-lint .gitlab-ci.yml"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Docker Compose Base

Objetivo: stack con servicios de infraestructura levantándose limpio.

#### `task_00_04` — docker-compose.yml Base

- [ ] **Título**: Definir docker-compose.yml con servicios postgres, redis, minio, vault, clamav
- **Descripción**: Crear `docker/docker-compose.yml` con los 5 servicios de infraestructura. PostgreSQL 16 con extensiones pgvector y pg_trgm habilitadas. Redis 7 con persistencia AOF+RDB. MinIO con consola web. Vault en modo dev solo en `docker-compose.dev.yml`, en producción modo server con KV v2. ClamAV con definiciones actualizadas. Todos con healthchecks Docker nativos. Volúmenes bind-mounted a `/data/agent-platform/`.
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_00_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_04_a
    description: "docker compose config valida sin errores"
    check_type: automated
    runtime: generic-shell
    command: "docker compose -f docker/docker-compose.yml config"
    expected_signal: "exit_code == 0"
  - id: auto_00_04_b
    description: "Todos los servicios pasan healthcheck en 60s"
    check_type: automated
    runtime: generic-shell
    command: "docker compose up -d && sleep 60 && docker compose ps --filter health=healthy | wc -l"
    expected_signal: "stdout contains 5"
    timeout_seconds: 120
  ```

#### `task_00_05` — Inicialización de Postgres

- [ ] **Título**: Script de inicialización de PostgreSQL con extensiones y roles
- **Descripción**: Script SQL ejecutado al primer arranque que crea las extensiones `pgvector` y `pg_trgm`, configura roles base (`app_user` con permisos limitados, `migrations_user` con DDL), y habilita logging de queries lentas.
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_05_a
    description: "Las extensiones pgvector y pg_trgm están instaladas"
    check_type: automated
    runtime: generic-shell
    command: 'docker compose exec -T postgres psql -U postgres -c "SELECT extname FROM pg_extension WHERE extname IN (''vector'',''pg_trgm'')" | grep -c vector'
    expected_signal: "stdout contains 1"
  ```

#### `task_00_06` — Inicialización de Vault

- [ ] **Título**: Bootstrap de Vault con KV v2 y unseal keys gestionadas
- **Descripción**: Script `scripts/init-vault.sh` que en el primer arranque inicializa Vault, genera 5 unseal keys (Shamir 3 of 5), las muestra al operador con instrucciones de almacenamiento seguro (NO en disco del host por defecto), habilita KV v2 en path `secret/`, y crea políticas iniciales para los servicios.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_00_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_06_a
    description: "Vault está inicializado y unsealed"
    check_type: automated
    runtime: generic-shell
    command: "docker compose exec -T vault vault status -format=json | jq -r '.initialized'"
    expected_signal: 'stdout == "true"'
  ```

### Fase C — FastAPI Esqueleto con Multi-Tenancy

Objetivo: API base con auth, multi-tenancy y RLS funcionando.

#### `task_00_07` — Modelos SQLAlchemy Base

- [ ] **Título**: Modelos Organization, User, UserOrganizationMembership, Session, AuditLog
- **Descripción**: Crear modelos SQLAlchemy 2.x async para las entidades base. Todas las tenant-scoped llevan `tenant_id UUID NOT NULL`. UUIDs v7 como PK. Soft-delete con `deleted_at`. Timestamps con TIMESTAMPTZ.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_07_a
    description: "Tests unitarios de los modelos pasan"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_08` — Migración Alembic Inicial

- [ ] **Título**: Crear migración inicial Alembic con tablas base + RLS
- **Descripción**: Migración inicial que crea las tablas con sus índices y activa políticas RLS por `tenant_id` en `current_setting('app.tenant_id')`. Migración reversible obligatoria.
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_08_a
    description: "alembic upgrade head y luego downgrade base funcionan"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_migrations.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_09` — Middleware Multi-Tenant

- [ ] **Título**: Middleware FastAPI que extrae tenant_id del JWT e inyecta en sesión PostgreSQL
- **Descripción**: Middleware que decodifica el JWT del header Authorization, extrae `tenant_id`, abre transacción y ejecuta `SET LOCAL app.tenant_id = '...'` para que RLS aplique. Si no hay JWT en endpoints autenticados → 401. Si hay JWT pero tenant_id no coincide con el del recurso → 403.
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_09_a
    description: "Test de aislamiento: user del tenant_A no puede leer recursos del tenant_B"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_isolation.py::test_cross_tenant_isolation -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_10` — Endpoints de Auth

- [ ] **Título**: Endpoints /auth/register, /auth/login, /auth/logout, /auth/me
- **Descripción**: Implementar registro con email+password (Argon2id), login que devuelve JWT firmado con clave del Vault, logout que revoca la sesión en Redis, /me que devuelve datos del usuario actual. Rate limiting de 5 intentos en 15 min por IP.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_10_a
    description: "Suite completa de endpoints de auth"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_auth.py -v"
    expected_signal: "exit_code == 0"
  - id: auto_00_10_b
    description: "Rate limiting funciona: el sexto intento de login devuelve 429"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_auth.py::test_rate_limit -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_11` — Endpoints Admin

- [ ] **Título**: Endpoints /admin/tenants (CRUD), /admin/users (CRUD), /admin/system-health
- **Descripción**: Endpoints solo accesibles a System Admin que permiten crear tenants, asignar Tenant Admins, listar usuarios cross-tenant (con disclaimer de auditoría), y consultar salud del sistema. Cada acción en audit_log con quién, cuándo, qué.
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_10`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_11_a
    description: "Tests de RBAC: System Admin puede CRUD tenants, Tenant Admin no"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_admin_rbac.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Panel Admin Esqueleto

Objetivo: UI Next.js mínima con login y dashboard.

#### `task_00_12` — Setup Next.js + Tailwind + shadcn/ui

- [ ] **Título**: Crear `apps/admin-panel/` con Next.js 14 App Router, Tailwind, shadcn/ui
- **Descripción**: Inicializar proyecto Next.js con TypeScript estricto. Configurar Tailwind y shadcn/ui. Crear estructura de carpetas: `app/`, `components/`, `lib/`, `types/`. Generar tipos del API con `openapi-typescript`.
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_00_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_12_a
    description: "Build de Next.js funciona sin errores"
    check_type: automated
    runtime: node-vitest
    command: "npm run build"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_13` — Pantalla de Login y Dashboard del System Admin

- [ ] **Título**: Implementar pantallas /login y /admin/dashboard
- **Descripción**: Login con form email+password, manejo de errores. Dashboard que muestra: lista de servicios del stack con su estado de salud (verde/amarillo/rojo), uso de recursos del host (CPU/RAM/disco) leídos del endpoint /admin/system-health. Refresh automático cada 30 s.
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_00_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_13_a
    description: "E2E del flujo de login y dashboard"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/admin-login.spec.ts"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Observabilidad y Healthchecks

Objetivo: trazabilidad y self-healing operativos.

#### `task_00_14` — Logging Estructurado JSON

- [ ] **Título**: Configurar structlog con campos estándar
- **Descripción**: Configurar `structlog` (o `python-json-logger`) en todos los servicios Python con campos obligatorios: timestamp, level, service, trace_id, span_id, tenant_id, user_id, project_id. Filtros automáticos para enmascarar PII (email, tokens, IBAN, DNI).
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_00_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_14_a
    description: "Tests verifican que PII se enmascara correctamente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_logging_pii.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_15` — OpenTelemetry

- [ ] **Título**: Instrumentar todos los servicios con OpenTelemetry
- **Descripción**: Instrumentación automática de FastAPI, SQLAlchemy, Redis, Celery con OpenTelemetry. Exporter a stdout en esta fase (Loki/Tempo en Fase 12). Propagación de trace_id en headers entre servicios.
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_00_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_15_a
    description: "Trace_id se propaga entre llamadas HTTP entre servicios"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tracing.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_16` — Watchdog de Servicios

- [ ] **Título**: Implementar watchdog Python con backoff exponencial
- **Descripción**: Servicio `apps/watchdog/` que cada 30 s consulta el estado de healthcheck de cada contenedor. Si un servicio cae: reinicia con backoff exponencial (10s, 30s, 90s, máx 5 intentos). Tras 5 fallos: alerta al canal del System Admin (en esta fase, solo log estructurado en stderr; las notificaciones reales llegan en Fase 10).
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_00_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_16_a
    description: "Si se mata un servicio, el watchdog lo reinicia en menos de 60s"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_watchdog.py::test_kill_and_recover -v"
    expected_signal: "exit_code == 0"
    timeout_seconds: 180
  ```

### Fase F — Documentación Inicial

Objetivo: dejar el repo navegable y entendible.

#### `task_00_17` — Estructura Canónica de /docs

- [ ] **Título**: Crear estructura canónica /docs/ con 7 carpetas
- **Descripción**: Crear las 7 carpetas obligatorias (01-overview, 02-getting-started, 03-guides, 04-reference, 05-architecture-decisions, 06-runbooks, 07-changelog) con READMEs vacíos en cada una. Crear `/docs/README.md` como índice principal.
- **Tiempo estimado**: 2 h
- **Complejidad**: xs
- **Rol sugerido**: technical-writer
- **Dependencias**: ninguna
- **Tests automáticos**:
  ```yaml
  - id: auto_00_17_a
    description: "Estructura canónica de /docs existe"
    check_type: automated
    runtime: generic-shell
    command: "for dir in 01-overview 02-getting-started 03-guides 04-reference 05-architecture-decisions 06-runbooks 07-changelog; do test -d docs/$dir || exit 1; done"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_18` — Documentación Inicial de Arquitectura

- [ ] **Título**: Escribir docs base: introduction, architecture, installation, first-run
- **Descripción**: Redactar `/docs/01-overview/01-introduction.md`, `/docs/01-overview/02-architecture.md`, `/docs/02-getting-started/01-installation.md`, `/docs/02-getting-started/03-first-run.md`. En idioma del proyecto (es por defecto en esta instalación). Diagramas con Mermaid embebido.
- **Tiempo estimado**: 8 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_00_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_18_a
    description: "Lint de markdown pasa en todos los docs"
    check_type: automated
    runtime: node-vitest
    command: "npx markdownlint docs/**/*.md"
    expected_signal: "exit_code == 0"
  ```

#### `task_00_19` — ADRs Iniciales

- [ ] **Título**: Generar ADRs 0001-0005 con decisiones técnicas clave de esta fase
- **Descripción**: Redactar al menos 5 ADRs documentando: 0001 PostgreSQL + RLS, 0002 sesiones server-side en Redis (no JWT stateless), 0003 Vault desde día uno, 0004 monorepo con apps/ y packages/, 0005 Argon2id para passwords.
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: arquitecto
- **Dependencias**: `task_00_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_00_19_a
    description: "Existen al menos 5 ADRs numerados secuencialmente"
    check_type: automated
    runtime: generic-shell
    command: "ls docs/05-architecture-decisions/0*.md | wc -l"
    expected_signal: "stdout >= 5"
  ```

#### `task_00_20` — Changelog del Plan 00

- [ ] **Título**: Generar entrada de changelog para este plan
- **Descripción**: Crear `/docs/07-changelog/00-fundaciones.md` siguiendo el formato canónico: cabecera con plan_id y fechas, resumen de lo construido, lista de tareas con sus commits, decisiones tomadas, link al PR.
- **Tiempo estimado**: 2 h
- **Complejidad**: xs
- **Rol sugerido**: technical-writer
- **Dependencias**: todas las anteriores
- **Tests automáticos**:
  ```yaml
  - id: auto_00_20_a
    description: "El archivo de changelog del plan existe y tiene frontmatter válido"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/00-fundaciones.md && head -1 docs/07-changelog/00-fundaciones.md | grep -q '^---'"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Estos tests se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Se realizan en el contenedor `review-runtime` levantado por el sistema. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_00_01
  description: "El sistema arranca con un solo comando en una máquina nueva"
  hint: "En una máquina limpia, clonar el repo, ejecutar 'docker compose up -d' y verificar que en 2 minutos todos los servicios están healthy"
  checklist:
    - "docker compose up -d termina sin errores en máquina nueva"
    - "Todos los servicios pasan healthcheck en menos de 2 minutos"
    - "El panel admin responde en http://localhost:PUERTO con la pantalla de login"
    - "Los logs no muestran tracebacks ni errores rojos en el arranque"
  related_tasks: [task_00_04, task_00_13, task_00_16]

- id: human_00_02
  description: "Multi-tenancy es real, no decorativa"
  hint: "Crear dos tenants (A y B), crear un usuario en cada uno, verificar manualmente desde curl/Postman que cruzar tokens da 403 o 404"
  checklist:
    - "Crear tenant A con su Tenant Admin"
    - "Crear tenant B con su Tenant Admin distinto"
    - "Con el token de admin_A, intentar listar usuarios de tenant_B → devuelve 403/404"
    - "Con el token de admin_A, intentar GET /api/users/{user_id_de_B} → devuelve 404"
    - "Logs de la API muestran que el filtro de tenant_id se aplica en todas las queries"
  related_tasks: [task_00_07, task_00_08, task_00_09]

- id: human_00_03
  description: "Self-healing funciona en escenario realista"
  hint: "Matar manualmente un servicio crítico y observar la recuperación"
  checklist:
    - "docker compose kill api-server → el watchdog lo reinicia en menos de 60s"
    - "docker compose kill postgres → el watchdog lo reinicia y los demás servicios se reconectan sin intervención humana"
    - "Tras 5 fallos consecutivos del mismo servicio, el watchdog alerta y deja de reintentar"
  related_tasks: [task_00_16]

- id: human_00_04
  description: "Observabilidad es útil, no solo verbosa"
  hint: "Hacer un par de operaciones en la API y verificar logs/traces"
  checklist:
    - "Los logs JSON tienen los campos estándar (timestamp, service, trace_id, tenant_id, etc.)"
    - "Un trace_id de una request HTTP se ve en logs de todos los servicios que participaron"
    - "Si se hace login con un email, ese email aparece enmascarado en los logs (e.g. 'a***@example.com')"
    - "Si por error se loguea un token, el filtro lo enmascara"
  related_tasks: [task_00_14, task_00_15]

- id: human_00_05
  description: "Documentación inicial es navegable y suficiente para arrancar"
  hint: "Un desarrollador nuevo debe poder seguir las guías para arrancar el sistema"
  checklist:
    - "/docs/01-overview/01-introduction.md explica qué es el sistema en menos de 5 minutos de lectura"
    - "/docs/02-getting-started/01-installation.md tiene instrucciones reproducibles"
    - "Los 5 ADRs iniciales están bien justificados con contexto, decisión y alternativas descartadas"
    - "Mermaid renderiza correctamente en GitHub/GitLab"
  related_tasks: [task_00_17, task_00_18, task_00_19]
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas (`task_00_01` a `task_00_20`) están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_00_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Cobertura de tests > 70% en código de auth y multi-tenancy.
6. ✅ Generada entrada en `/docs/07-changelog/00-fundaciones.md`.
7. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 01 — Dominio Mínimo** (`01-dominio-minimo.md`), que añade el modelo de dominio (agentes, equipos, proyectos, plantillas) sobre estos cimientos.
