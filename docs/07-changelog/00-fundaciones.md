---
plan_id: 00-fundaciones
title: Fundaciones del Sistema
started_at: 2026-05-20
completed_at: null
status: pending_human_validation
tasks_done: 19
tasks_total: 20
tasks_pending_local: [task_00_13]
tests_automated_passing: 94
human_validations_passing: TBD
docs_language: es
---

> **Estado:** el plan aún no está formalmente cerrado. Queda
> `task_00_13` (Playwright) pendiente de verificación local más los
> cinco tests humanos (`human_00_01..05`). `task_00_03` ya cerrada
> tras el primer run verde de GitHub Actions sobre la rama
> `plan/00-fundaciones`. Cuando el operador humano valide, se pasa
> el frontmatter del roadmap a `status: completed` y se rellena
> `completed_at`.

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
- `task_00_13` ⬜ implementación verde (login + dashboard +
  TanStack Query con refresh 30 s + Playwright spec). E2E pendiente
  de instalar browsers (`npm run e2e:install`) y arrancar el stack
  con un user system admin seeded.

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

17 notas de troubleshooting que NO estaban en el plan original
pero documentan trampas no-obvias encontradas y resueltas
(`docker-compose-volumes-merge`, `vault-dev-mode-port-conflict`,
`asyncpg-set-local-no-bind-params`, `mypy-local-package-imports`,
`otel-console-exporter-pytest-stdout`,
`windows-asyncio-engine-dispose`, `windows-git-crlf-vs-hooks`, etc.).

## Métricas

- **Commits en `plan/00-fundaciones`:** ≈25 (uno por tarea, más
  fixes, gotchas y roadmap maintenance).
- **Tests automáticos:** 94 passing (69 unit + 25 integration),
  1 skipped (E2E watchdog opt-in).
- **Cobertura crítica:** auth + multi-tenancy ≥ 70 % (objetivo
  cumplido).
- **Líneas commiteadas:** ~7 000 (incluye docs y plan files).

## Decisiones técnicas registradas

Las decisiones de arquitectura están en
`docs/05-architecture-decisions/0001-0005-*.md` (ver índice de
ADRs arriba).

## Tareas pendientes (deuda)

| Tarea        | Razón                                                                        | Cómo cerrarla                                                                                |
| ------------ | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| `task_00_13` | E2E Playwright necesita browsers (~300 MB) + un user system admin pre-seeded | `cd apps/admin-panel && npm run e2e:install && npm run e2e` con el stack y un admin en la BD |

Ambas son **verificación**, no implementación. El código está
escrito y los tests intermedios (`npm run build`, YAML parseable)
pasan localmente.

## Tests humanos del plan

Pendientes de ejecutarse en el sistema en runtime con un operador
humano (ver sección "Tests Humanos del Plan" en
[`docs/roadmap/00-fundaciones.md`](../roadmap/00-fundaciones.md)):

- `human_00_01` — el sistema arranca con `docker compose up -d` en
  máquina nueva.
- `human_00_02` — multi-tenancy es real (token de tenant_A no ve
  recursos de tenant_B).
- `human_00_03` — self-healing del watchdog en escenario realista.
- `human_00_04` — observabilidad útil (logs con campos, PII
  enmascarado, trace_id propagado).
- `human_00_05` — documentación inicial es navegable y suficiente.

## Próximo plan

Tras validación humana, activar
[`docs/roadmap/01-dominio-minimo.md`](../roadmap/01-dominio-minimo.md):
cambiar su frontmatter a `status: in_progress` + `started_at: <fecha>`.
Este plan añade el modelo de dominio (agentes, equipos, proyectos,
plantillas) sobre los cimientos que acabamos de cerrar.
