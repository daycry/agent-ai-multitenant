---
plan_id: sso-global-user-admin
title: Auth/SSO platform-global (supersede ADR 0031) + administración de usuarios + login por provider
completed_at: null
docs_language: es
---

# Plan sso-global-user-admin — Auth/SSO platform-global + administración de usuarios

## Resumen

Re-arquitectura **crítica** de autenticación. El Plan 08 había implementado
SSO **per-tenant** (ADR 0031), divergiendo de **ADR 0028** (auth
platform-global). El operador confirma el modelo de 0028 y se formaliza en
**[ADR 0047](../05-architecture-decisions/0047-sso-auth-global-platform-membership-access.md)**
(`accepted`, supersede la parte per-tenant de 0031, re-alinea con 0028):

- **Auth providers platform-global.** Los providers OIDC/SAML se configuran
  **una vez** (System Admin) y sirven a **todos** los tenants.
  `sso_configurations` deja de ser tenant-scoped (sin `tenant_id` ni RLS,
  igual que `llm_providers`); hay a lo sumo **una** config por `provider`.
- **Login global por provider.** La entrada de SSO deja de llevar tenant:
  `/auth/sso/{provider_id}/oidc|saml/login`, callback OIDC compartida y
  **ACS SAML global** (`/auth/sso/saml/acs`). El `state`/`RelayState`
  (server-side, single-use) lleva el provider que inició el flujo. La sesión
  emitida prueba **identidad** (usuario global, sin tenant activo).
- **Acceso por membership (deny-by-default).** El acceso a un tenant lo
  concede **exclusivamente** una `UserOrganizationMembership` que asigna el
  System Admin. Tras login: **0 memberships** → pantalla "sin permisos,
  contacta al administrador"; **1** → entra directo; **>1** → tenant-picker.
  Sin claiming por email-domain ni `default_tenant_id`.
- **Administración de usuarios.** Nueva superficie `/admin/users` + endpoints
  System Admin para asignar/quitar memberships (usuario↔tenant+rol,
  activar/desactivar).
- **Login con providers.** `/login` lista los providers habilitados (endpoint
  **público sin secretos**) con un botón de **marca** por provider (icono por
  `kind`, `button_label` configurable) + el formulario de contraseña.

> **Guardrails respetados.** El **login por contraseña + las sesiones
> existentes + MFA (TOTP/WebAuthn) + SCIM siguen funcionando** sin cambios
> (auth global se añade al lado, no reemplaza). Los secretos (`client_secret`
> OIDC, clave privada SP) **siguen cifrados en reposo** (Fernet
> `sso_encryption_key` / Vault), **nunca** se devuelven ni se registran; el
> endpoint público de providers no expone secretos. Migración **reversible,
> cabeza única**. Las rutas viejas per-tenant **se retiran** (sin redirección,
> decisión del operador).
>
> El frontmatter del plan lo cierra el orquestador; esta entrada documenta lo
> implementado.

## Cambios por tarea

### Fase A — Modelo global + migración

- ✅ **`task_sso_01`** — **`sso_configurations` platform-global +
  `button_label` + migración.** `SSOConfiguration` pasa a platform-global
  (sin RLS por tenant; identidad por `provider`/`kind`, no por tenant), se
  añade `button_label`. Migración **`0076_sso_global`** (head único, revisa
  `0075_memory_source_human_ws`): añade `button_label`, **consolida** las
  filas per-tenant en una global por provider (gana la última actualizada;
  un `NOTICE` registra cuántas se eliminan), elimina el unique per-tenant +
  FK + índices + RLS/`tenant_isolation` + la columna `tenant_id`. El manejo
  de secretos no cambia. **Reversible**: el `downgrade` restaura el _shape_
  per-tenant (las filas consolidadas no se resucitan). Las queries
  (`_load_enabled_oidc_config` / `_load_enabled_saml_config`) pasan a
  globales sobre el engine BYPASSRLS.

### Fase B — Login global + providers públicos

- ✅ **`task_sso_02`** — **Login por provider + callback/ACS global +
  providers públicos.** `/auth/sso/{provider_id}/oidc|saml/login` (por
  provider, no tenant); callback OIDC + **ACS SAML global** (el
  `state`/`RelayState` lleva el `provider_id`); `GET /auth/sso/providers`
  **público** (`id` / `kind` / `display_name` / `button_label` / `login_url`,
  **sin secretos**). La sesión emitida es de identidad (sin tenant). El
  `_provision_identity` linkea por email verificado y **no** crea membership
  ni lee grupos del IdP.

### Fase C — Resolución por membership + pantalla "sin acceso"

- ✅ **`task_sso_03`** — **Resolución post-login + pantalla "sin permisos".**
  `GET /auth/session/resolve` traduce las memberships activas en
  `no_access` / `single` / `multiple`; `POST /auth/session/select-tenant`
  re-valida la membership antes de acuñar el token tenant-scoped. Backend +
  UI de la pantalla "sin permisos, contacta al administrador"
  (`/no-access`). Aplica tanto al login SSO como al de contraseña (ambos
  convergen en la sesión de identidad). Tests `@pytest.mark.cross_tenant`.

### Fase D — Administración de usuarios

- ✅ **`task_sso_04`** — **`/admin/users` (UI + endpoints de memberships).**
  Endpoints System Admin (`require_system_admin`, engine BYPASSRLS):
  `GET/POST /admin/users/{user_id}/memberships`,
  `PATCH/DELETE …/{membership_id}`. `POST` revive una membership revocada en
  vez de chocar con `UNIQUE(user_id, tenant_id)`; `PATCH` cambia rol/activo;
  `DELETE` revoca (soft-delete). El `role` se limita a roles per-tenant
  (nunca `system_admin`); cada mutación deja `audit_log` con el `tenant_id`
  afectado. UI `/admin/users` (lista + asignación de tenants/roles).

### Fase E — Página de login + modal informativa

- ✅ **`task_sso_05`** — **Botones de provider en `/login` (marca) + callback
  en modal.** `/login` pinta un botón por provider habilitado (estilo de
  marca por `kind`, `button_label` configurable) + password. La modal de
  config SSO muestra callback/ACS/SP-entity/metadata informativos (con
  copiar) + la base configurada (aviso si sigue en el default). _(typecheck/
  lint/build verde; e2e `login-providers.spec.ts` escrito, no ejecutado.)_

### Fase F — Docs + cierre del ADR

- ✅ **`task_sso_06`** — **ADR 0047 `accepted` + docs SSO + changelog +
  RBAC** (esta entrada). Actualizadas
  [`docs/04-reference/rbac.md`](../04-reference/rbac.md) (auth providers
  global, `/admin/users/{id}/memberships`, `/auth/sso/providers` público,
  rutas por provider + resolución por membership) y
  [`docs/04-reference/auth-sso.md`](../04-reference/auth-sso.md) (modelo
  global, login por provider, ACS global, resolución post-login, admin de
  usuarios). Nuevo runbook
  [`docs/06-runbooks/sso-global-auth.md`](../06-runbooks/sso-global-auth.md).
  Nota de superseded en la guía de tests humanos del Plan 08. Fila del plan
  en [`docs/roadmap/README.md`](../roadmap/README.md).

## Migraciones

- **`0076_sso_global`** — `sso_configurations` platform-global +
  `button_label`; consolida las filas per-tenant. Head único
  (revisa `0075_memory_source_human_ws`); reversible (down restaura el shape
  per-tenant; las filas consolidadas no se resucitan). El manejo de secretos
  no cambia (`client_secret_*` / SP-key + CHECKs intactos).

## Verificación

```bash
TEST_PG_PORT=15432 TEST_REDIS_URL=redis://localhost:6379/15 pytest \
  tests/integration/test_sso_global_config.py \
  tests/integration/test_sso_global_login.py \
  tests/integration/test_post_login_membership_resolution.py \
  tests/integration/test_admin_user_memberships.py \
  tests/integration/test_migrations.py -q
```

- `apps/admin-panel`: `npm run typecheck && npm run lint && npm run build`
  verde (rutas `/login`, `/no-access`, `/admin/users`, `/admin/settings/sso*`).
- `pre-commit` (prettier **scoped** a los ficheros cambiados por la
  incidencia conocida de libuv en Windows con `--all-files`). Sin
  `--no-verify`.

## Pendiente

- **Tests humanos del plan** — pendientes de ejecutar por un humano (login
  global + providers en `/login` + acceso por membership + pantalla "sin
  acceso" + tenant-picker + password intacto + modal con callback/ACS). Ver
  el bloque `human_sso_01` en
  [`docs/roadmap/sso-global-user-admin.md`](../roadmap/sso-global-user-admin.md).
- Los e2e de UI (`login-providers.spec.ts`) están escritos pero requieren
  navegador y la verificación con IdPs reales no se automatiza.
- **Merge del PR de `plan/sso-global-user-admin` a `main`** — lo gestiona el
  humano tras los tests humanos. El plan no se marca `completed` aquí.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
