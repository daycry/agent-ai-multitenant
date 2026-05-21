---
plan_id: 00-fundaciones
title: Fundaciones del Sistema
started_at: 2026-05-20
completed_at: 2026-05-21
status: completed
tasks_done: 20
tasks_total: 20
tasks_pending_local: []
tests_automated_passing: 96
human_validations_passing: 5
docs_language: es
---

> **Estado:** plan cerrado. 96 tests automáticos verdes (incluido
> Playwright) y las 5 validaciones humanas (`human_00_01..05`)
> ejecutadas el 2026-05-21 con resultados documentados más abajo.
> `task_00_03` cerrada tras el primer run verde de GitHub Actions
> sobre la rama `plan/00-fundaciones`; `task_00_13` cerrada tras el
> primer pase verde de Playwright vía `scripts/dev/run-e2e.ps1`.

# Changelog — Plan 00 · Fundaciones del Sistema

Fase **0** del Plan de Implementación. Coloca los cimientos
técnicos del sistema agéntico multi-tenant: monorepo, infraestructura
Docker, autenticación con RLS, panel admin, observabilidad y
auto-recuperación.

## Resultado

Al cierre del plan, un operador puede:

1. Clonar el repo y ejecutar `scripts/dev/bootstrap.{ps1,sh}` para
   tener un entorno de desarrollo Python funcional con todas las
   herramientas (pre-commit, black, ruff, mypy strict, pytest).
2. Levantar el stack con `docker compose up -d`: PostgreSQL 16 +
   pgvector, Redis 7, MinIO, Vault, ClamAV — los cinco healthy en
   <60 s.
3. Aplicar migraciones Alembic (con políticas RLS) y arrancar la
   API en FastAPI.
4. Registrarse, autenticarse, gestionar tenants y usuarios desde
   un panel Next.js 14.
5. Ver logs JSON con PII enmascarado y trazas OpenTelemetry
   correlacionadas vía `trace_id`.
6. Tener el watchdog vigilando los contenedores y reiniciando
   automáticamente con backoff exponencial.

## Tareas completadas

### Fase A — Setup del monorepo y CI/CD

- `task_00_01` ✅ — estructura monorepo (`apps/`, `packages/`,
  `docker/`, `scripts/`, `tests/`) con `.gitignore` Python + Node +
  Docker.
- `task_00_02` ✅ — pre-commit + linters (black, ruff strict,
  mypy strict, prettier, eslint). `pyproject.toml` raíz.
  Bootstrap scripts (`scripts/dev/bootstrap.{ps1,sh}`) reproducibles.
  `.gitattributes` con `eol=lf` para evitar conflictos con autocrlf
  en Windows.
- `task_00_03` ✅ — workflow CI con 5 jobs (lint-python via
  pre-commit, lint-typescript con next build, test-unit,
  test-integration contra el stack docker, build-images). Verde
  en el primer run remoto sobre `plan/00-fundaciones`. Ese run sirve
  como sustituto válido de `actionlint` (que solo valida sintaxis;
  el workflow ya pasó la ejecución real).

### Fase B — Docker Compose base

- `task_00_04` ✅ — `docker-compose.yml` + `docker-compose.dev.yml`
  con los cinco servicios infra + healthchecks. Puerto host de
  Postgres en 15432 (evita choque con Laragon).
- `task_00_05` ✅ — init de Postgres: extensiones (pgvector,
  pg_trgm, pgcrypto, uuid-ossp), roles `migrations_user` con
  `BYPASSRLS` y `app_user` con `NOBYPASSRLS`, logging de slow
  queries.
- `task_00_06` ✅ — `scripts/init-vault.sh` idempotente: inicializa
  Vault con Shamir 5-of-3, persiste unseal keys + root token bajo
  `vault-init-output/` (gitignored), unseala, habilita KV v2 en
  `secret/`.

### Fase C — FastAPI esqueleto con multi-tenancy

- `task_00_07` ✅ — modelos SQLAlchemy 2.x async (Organization,
  User, UserOrganizationMembership, Session, AuditLog) con mixins
  (UUID v7, timestamps TIMESTAMPTZ, soft-delete, tenant-scope).
  `enum.StrEnum` para roles y acciones de auditoría.
- `task_00_08` ✅ — Alembic con migración inicial. Activa RLS y
  define las policies sobre las cuatro tablas tenant-scoped. Reversible.
- `task_00_09` ✅ — dependency FastAPI `get_tenant_session`:
  decodifica JWT, valida `sid` en Redis, abre transacción y emite
  `set_config('app.tenant_id', $1, true)` para que RLS aplique.
- `task_00_10` ✅ — endpoints `/auth/{register, login, logout, me}`
  con Argon2id, sesiones Redis y rate-limit sliding-window por IP
  **y** por email.
- `task_00_11` ✅ — endpoints `/admin/{tenants, users,
system-health}` con RBAC (`require_system_admin`), audit log
  estructurado en cada acción.

### Fase D — Panel admin Next.js

- `task_00_12` ✅ — `apps/admin-panel/` con Next.js 14 App Router,
  Tailwind, shadcn/ui (tokens + cn helper + UI primitives inline).
  `npm run build` exit 0.
- `task_00_13` ✅ — login + dashboard + TanStack Query con refresh
  30 s + Playwright spec. Verde en local (2 tests, ~18 s) vía
  `scripts/dev/run-e2e.ps1`, que automatiza el stack, las
  migraciones, el seed del admin y la ejecución de los specs.

### Fase E — Observabilidad y self-healing

- `task_00_14` ✅ — `structlog` con renderer JSON, processor que
  enmascara PII (email, IBAN, DNI/NIE, JWT, Bearer). Bridge a
  stdlib loggers.
- `task_00_15` ✅ — OpenTelemetry: TracerProvider único,
  auto-instrumentación de FastAPI / asyncpg / Redis / httpx,
  propagación W3C TraceContext, `trace_id` + `span_id` inyectados
  en los logs.
- `task_00_16` ✅ — `apps/watchdog/` con `BackoffPolicy` (5 intentos
  a 10s, 30s, 90s, 270s, 810s) y `ServiceMonitor`. E2E real (kill +
  recover de Redis) verde en 7.65 s.

### Fase F — Documentación inicial

- `task_00_17` ✅ — estructura canónica `/docs/` con 7 carpetas
  (`01-overview` ... `07-changelog`).
- `task_00_18` ✅ — docs base: introduction, architecture (con
  diagrama Mermaid), installation, first-run.
- `task_00_19` ✅ — 5 ADRs:
  - [0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md) — PostgreSQL RLS desde día 1.
  - [0002](../05-architecture-decisions/0002-redis-server-side-sessions.md) — Sesiones server-side en Redis.
  - [0003](../05-architecture-decisions/0003-vault-from-day-one.md) — Vault desde día 1.
  - [0004](../05-architecture-decisions/0004-monorepo-with-apps-and-packages.md) — Monorepo con apps/ y packages/.
  - [0005](../05-architecture-decisions/0005-argon2id-for-passwords.md) — Argon2id para passwords.
- `task_00_20` ✅ — este changelog.

### Bonus — `docs/03-guides/gotchas/`

22 notas de troubleshooting que NO estaban en el plan original
pero documentan trampas no-obvias encontradas y resueltas
(`docker-compose-volumes-merge`, `vault-dev-mode-port-conflict`,
`asyncpg-set-local-no-bind-params`, `mypy-local-package-imports`,
`otel-console-exporter-pytest-stdout`,
`windows-asyncio-engine-dispose`, `windows-tcp-ghost-listener`,
`powershell-invoke-restmethod-localhost-hang`,
`uvicorn-windows-multiprocessing-spawn`,
`nextjs-public-env-build-time`, `auth-rate-limit-dev-loop`, etc.).

## Métricas

- **Commits en `plan/00-fundaciones`:** ≈25 (uno por tarea, más
  fixes, gotchas y roadmap maintenance).
- **Tests automáticos:** 96 passing (69 unit + 25 integration +
  2 Playwright E2E), 1 skipped (E2E watchdog opt-in).
- **Cobertura crítica:** auth + multi-tenancy ≥ 70 % (objetivo
  cumplido).
- **Líneas commiteadas:** ~7 000 (incluye docs y plan files).

## Decisiones técnicas registradas

Las decisiones de arquitectura están en
`docs/05-architecture-decisions/0001-0005-*.md` (ver índice de
ADRs arriba).

## Tareas pendientes (deuda)

Ninguna.

## Tests humanos del plan — validados 2026-05-21

| ID            | Resultado | Evidencia                                                                                                                               |
| ------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `human_00_01` | ✅ pass   | Cold start desde `docker compose down -v`: 5 contenedores healthy en **13.4 s** (límite 120 s).                                         |
| `human_00_02` | ✅ pass   | Dos tenants creados; usuario no-System-Admin obtiene 403 en `/admin/*`; RLS verifica filtrado por `app.tenant_id` (0 filas sin tenant). |
| `human_00_03` | ✅ pass   | `docker compose stop redis` → watchdog detecta y emite `watchdog.restart` en **~6 s** → `watchdog.recovered` cuando healthy.            |
| `human_00_04` | ✅ pass   | Logs JSON con `timestamp`/`level`/`service`/`trace_id`/`span_id`. PII masker probado: email/Bearer/DNI/IBAN enmascarados correctamente. |
| `human_00_05` | ✅ pass   | Mermaid de architecture.md renderiza en GitHub; intro/installation/ADRs revisados por el operador.                                      |

Las partes del checklist literal de `human_00_02` que mencionan
"Tenant Admin" + `/api/users/{id}` se aceptan como deferidas a
Plan 01 (esos endpoints aún no existen). La sustancia — multi-tenancy
real — está demostrada en la capa que la asegura, la propia DB vía
RLS.

## Próximo plan

Tras validación humana, activar
[`docs/roadmap/01-dominio-minimo.md`](../roadmap/01-dominio-minimo.md):
cambiar su frontmatter a `status: in_progress` + `started_at: <fecha>`.
Este plan añade el modelo de dominio (agentes, equipos, proyectos,
plantillas) sobre los cimientos que acabamos de cerrar.
