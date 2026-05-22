---
adr: "0010"
title: Superadmin cross-tenant via BYPASSRLS + X-Tenant-Id header
status: accepted
date: 2026-05-21
deciders: System Architect
phase: 01-dominio-minimo
---

# ADR 0010 — Superadmin cross-tenant via BYPASSRLS + `X-Tenant-Id`

## Contexto

Plan 00 introdujo `users.is_system_admin: bool` y un router `/admin/*`
que sirve sus consultas a través de `migrations_user` (BYPASSRLS).
Plan 01 da forma al dominio (agents, teams, projects, tasks…) con
sus routers tenant-scoped propios. En la primera iteración del Plan
01 esos routers seguían usando **únicamente** `get_tenant_session`
(app_user, NOBYPASSRLS), de modo que:

- Un superadmin sin claim `tid` en el JWT veía sólo built-ins; las
  filas de cualquier tenant le eran invisibles.
- Cualquier escritura tenant-scoped (POST projects, POST teams,
  POST agents/fork…) rechazaba al superadmin con `400 "active
tenant required (JWT missing 'tid' claim)"`.

El documento maestro dejaba la "selección de tenant tras login"
para una fase posterior, lo que implícitamente diferiría también
el poder cross-tenant del superadmin. Pero esto bloqueaba los
tests humanos del Plan 01 (un operador root sin tenants visibles no
puede ni crear el primer proyecto).

Tres requisitos en tensión:

1. **El primer operador tiene que poder arrancar el sistema** desde
   cero — crear tenants, ver datos, ejecutar los humanos del plan.
2. **Aislamiento multi-tenant real para tenant users**: ni siquiera
   un cliente con conocimiento de UUIDs de otros tenants debe poder
   leerlos. (CLAUDE.md §1, ADR 0001).
3. **Sin selección de tenant en el login todavía** — esa pantalla
   pertenece a Plan 02.

## Decisión

Tres cambios coordinados sobre la auth:

### 1. `get_tenant_session` se vuelve un dispatcher

```python
async def get_tenant_session(principal):
    if principal.is_system_admin and principal.tenant_id is None:
        sessionmaker = get_admin_sessionmaker()  # migrations_user / BYPASSRLS
    else:
        sessionmaker = get_sessionmaker()        # app_user / NOBYPASSRLS
    # ... set app.user_id + (si hay) app.tenant_id ...
```

- **Tenant user con `tid`**: comportamiento previo (`app_user` + RLS).
- **Superadmin sin contexto** (sin `tid`, sin header): BYPASSRLS,
  `app.tenant_id` sin setear → la vista portfolio, ve todos los
  tenants. Writes siguen pidiendo tenant explícito (caen en el 400).
- **Superadmin con contexto** (`tid` o header): vuelve a `app_user`
  con `app.tenant_id` puesto → actúa como ese tenant (reads
  scoped, writes en su bucket). Esto preserva el invariante "no
  hay surfaces sin tenant_id" cuando el superadmin elige uno.

### 2. Header `X-Tenant-Id` para writes del superadmin

`get_principal` ahora lee un header opcional `X-Tenant-Id`. Para
superadmins, su valor **sobrescribe** el `tid` del JWT y se usa
como el tenant activo de la request. Para no-admins el header se
ignora silenciosamente: la JWT firmada sigue siendo la única
fuente de verdad de su scope y nunca pueden saltar a otro tenant.

Valor garbage (no-UUID) en el header → 400.

### 3. Auto-promoción del primer usuario

`POST /auth/register` comprueba `SELECT id FROM users LIMIT 1`
dentro de la misma transacción del INSERT. Si la tabla está vacía,
el nuevo usuario sale con `is_system_admin=true`. La atomicidad de
la transacción asegura que dos registers concurrentes en un DB
fresco no produzcan dos superadmins — sólo el primero que hace
commit gana el flag.

Esto significa que el operador que arranca la instalación tiene
poderes cross-tenant inmediatamente, sin necesidad de SQL manual.

## Alternativas descartadas

1. **Pedir el `tenant_id` en el body de cada POST/PUT.**
   Funcional pero invasiva: cada schema POST/PUT pasa a aceptar
   un `tenant_id` opcional sólo para admins; la lógica del router
   tiene que validar quién puede ponerlo. Header HTTP es una sola
   pieza de plomería en `auth/deps.py` y deja los schemas como
   tenant users los necesitan.
2. **Endpoint `POST /auth/select-tenant/{id}` que mintamos un JWT
   nuevo con `tid` puesto.** Más limpio conceptualmente, pero
   requiere también una UI de selector (que llega en Plan 02) y
   re-emisión de token en cada switch. Header per-request es más
   barato para el operador que cambia de tenant repetidamente.
3. **Crear un "Default Tenant" en bootstrap y meter ahí al
   superadmin.** Resuelve el bloqueo inicial pero ata al
   superadmin a un tenant por defecto y no le da acceso cross.
   Además ensucia las semánticas: el "tenant por defecto" no
   tiene equivalente en producción real.
4. **Dejarlo todo diferido a Plan 02.** Era la opción documentada
   originalmente, pero hace los tests humanos del Plan 01
   inviables como están escritos (`human_01_01..04`).

## Consecuencias

Positivas:

- Un operador con `is_system_admin=true` ve y modifica cualquier
  tenant desde el panel sin SQL manual ni endpoints de admin
  duplicados.
- El selector de tenant del header materializa esto: un dropdown
  con "Todos los tenants" (portfolio) + cada tenant; la
  preferencia persiste en `localStorage` y se inyecta como header
  en cada `apiFetch`.
- Los tenant users mantienen su aislamiento estricto: el header
  es no-op para ellos.
- El primer usuario tras `install` es siempre superadmin, sin
  pasos extra. El bootstrap script ya no necesita un `UPDATE
users SET is_system_admin = true` (sigue allí como defensivo
  porque la idempotencia no hace daño).

Negativas / cuidados:

- **Doble sesión SA**: ahora `get_tenant_session` puede devolver
  `app_user` _o_ `migrations_user`. Cualquier código nuevo que
  asuma uno u otro debe revisar `principal.is_system_admin`. Los
  filtros explícitos por `tenant_id` en routers (p.ej.
  `Project.tenant_id == principal.tenant_id` en `/agents/fork`)
  siguen siendo necesarios — RLS no los reemplaza para superadmins
  que actúan sin tenant context.
- **Audit log**: cuando un superadmin escribe con `X-Tenant-Id`,
  el `audit_log` registra `actor_user_id` + `tenant_id` del
  header. Si actúa sin header (portfolio + write), no llega a
  haber `tenant_id` que loggear (la request 400 antes). Plan 11
  (auditoría completa) verá si esto basta.
- **Tenant plataforma**: `00000000-0000-0000-0000-000000000001` se
  excluye del dropdown del panel. Si alguien arma una petición a
  mano con ese id en el header, BYPASSRLS lo dejaría operar sobre
  filas built-in. Mitigado a nivel UI; Plan 02 puede añadir un
  guard backend explícito si hace falta.
- **Promoción accidental**: en un DB que se haya wipeado y
  re-poblado, el "primer usuario" es nuevo cada vez. En producción
  esto es deseable (un fresh install necesita un operador);
  en CI donde la BD vive entre tests, hay que llamar a
  `_truncate_users` cuando el test verifica la promoción.

## Referencias

- Documento maestro, secciones 5 (multi-tenancy) y 8 (roles).
- Implementación:
  - `apps/api-server/src/api_server/auth/deps.py:get_principal`,
    `get_tenant_session`.
  - `apps/api-server/src/api_server/routers/auth.py:register` —
    promoción del primer usuario.
  - `apps/admin-panel/lib/tenant-context.tsx` +
    `components/layout/tenant-picker.tsx` — selector en el header.
  - `apps/admin-panel/lib/api.ts` — inyección de `X-Tenant-Id`
    en `apiFetch`.
- Tests:
  - `tests/integration/test_superadmin_cross_tenant.py` (6
    casos: portfolio read, scoped read, scoped write, no-admin
    header ignorado, header garbage, write sin contexto).
  - `tests/integration/test_auth.py::test_register_first_user_*` y
    `test_register_subsequent_user_*`.
  - `apps/admin-panel/e2e/tenant-picker.spec.ts` (3 casos UI).
  - `apps/admin-panel/e2e/team-detail.spec.ts` (el caso forked
    quedó desbloqueado gracias a este ADR).
- [ADR 0001 — PostgreSQL RLS desde el día uno](0001-postgres-rls-from-day-one.md).
