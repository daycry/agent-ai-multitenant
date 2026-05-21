---
adr: "0001"
title: PostgreSQL Row-Level Security desde el día uno
status: accepted
date: 2026-05-20
deciders: System Architect
phase: 00-fundaciones
---

# ADR 0001 — PostgreSQL Row-Level Security desde el día uno

## Contexto

El sistema es **multi-tenant a nivel departamentos / equipos**. Una
fuga cross-tenant (que un user del tenant A vea recursos del tenant
B) es **inaceptable** incluso en un entorno controlado: arruina la
confianza del operador y obliga a auditoría completa.

El riesgo no viene de los atacantes externos —el stack vive detrás
de un reverse proxy en red interna— sino de **bugs en el código de
la aplicación**: un `WHERE tenant_id = ?` olvidado, un JOIN con
ámbito mal calculado, una migración que filtra registros.

## Decisión

Activar **PostgreSQL Row-Level Security (RLS)** sobre todas las
tablas tenant-scoped desde la primera migración del proyecto.

- Cada tabla tenant-scoped lleva `tenant_id UUID NOT NULL`.
- Policy `FOR ALL USING (tenant_id = current_setting('app.tenant_id')::uuid)`.
- `FORCE ROW LEVEL SECURITY` activado para que ni siquiera el owner
  pueda saltarse las policies sin el flag `BYPASSRLS`.
- Dos roles de base de datos:
  - `migrations_user` con `BYPASSRLS` — corre Alembic, semilla de
    datos, queries cross-tenant del System Admin.
  - `app_user` con `NOBYPASSRLS` — el rol con el que el `api-server`
    se conecta para servir requests normales.

## Alternativas descartadas

1. **Filtrar tenant_id solo en código (SQLAlchemy events).**
   Rechazado: un único bug deja el filtro sin aplicar. RLS es
   defensa en profundidad.
2. **Una BD por tenant.** Rechazado: en mono-máquina supone
   sobrecarga operacional (conexiones, migraciones N veces,
   backups) y para el tamaño esperado (decenas de tenants
   internos) no justifica la complejidad.
3. **Schema por tenant.** Mismo problema de operación que (2) y
   menos seguro: un `SET search_path` mal puesto sigue filtrando.

## Consecuencias

Positivas:

- Aislamiento garantizado por el motor, no por la aplicación.
- Tests de integración pueden demostrar end-to-end que `tenant A`
  literalmente **no ve** filas de `tenant B` (RLS bloquea la lectura).

Negativas / cuidados:

- Hay que setear `app.tenant_id` en CADA request mediante un
  middleware FastAPI (lo hace `auth/deps.py:get_tenant_session`).
- `set_config('app.tenant_id', $1, true)` —**no** `SET LOCAL`— por
  el bug de asyncpg
  ([gotcha](../03-guides/gotchas/asyncpg-set-local-no-bind-params.md)).
- Endpoints del System Admin necesitan una sesión con `migrations_user`
  (BYPASSRLS); por eso existe `get_admin_session` separado de
  `get_tenant_session`.
- audit_log también está RLS-filtrado por tenant_id, pero el rol
  admin lo bypassa.

## Referencias

- Documento maestro, sección 5 (multi-tenancy) y 17 (seguridad).
- Migración: `apps/api-server/migrations/versions/20260520_0001_initial_schema_and_rls.py`.
- Tests de aislamiento: `tests/integration/test_isolation.py`.
- Gotcha: `docs/03-guides/gotchas/postgres-roles-bypassrls.md`.
