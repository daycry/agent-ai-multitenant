---
adr_id: "0047"
title: "Auth providers (SSO OIDC/SAML) platform-global + acceso por membership + administración de usuarios"
status: accepted
date: 2026-06-02
authors: [system_architect]
supersedes: ["0031"]
realigns_with: ["0028"]
plan_referenced: sso-global-user-admin
docs_language: es
---

# ADR 0047 — Auth/SSO platform-global + acceso por membership

> **Estado: `accepted`** (aprobado por el operador 2026-06-02; rutas viejas per-tenant se retiran, sin redirección).
> **Supersede** la parte per-tenant de **ADR 0031**; **re-alinea** con **ADR 0028**.

## Contexto

ADR 0028 (2026-05-28) decidió que los **auth providers son platform-global** (System Admin, configurados una vez,
sirven a todos los tenants), con login global (`users` es tabla global, sin `tenant_id`) y resolución de tenant
**después** del login. Sin embargo, **Plan 08 implementó SSO per-tenant** (`SSOConfiguration` con
`UniqueConstraint(tenant_id, provider)`, rutas `/auth/sso/{tenant_id}/oidc|saml/login`,
`_load_enabled_oidc_config(tenant_id)`) y lo formalizó en **ADR 0031** — divergiendo de 0028. El operador confirma
que el diseño correcto es el de 0028: **un usuario hace login con un provider global y el acceso a tenants lo dan
las memberships que asigna el administrador.**

## Decisión

1. **Auth providers platform-global.** La configuración OIDC/SAML pasa a ser **global** (gestionada por
   `system_admin` en el tenant especial), no por tenant. Un provider habilitado sirve a **todos** los tenants.
   `password` sigue siendo global y **se mantiene al lado** (no se reemplaza).

2. **Login global (agnóstico de tenant).** La entrada de SSO deja de llevar `{tenant_id}`:
   - `GET /auth/sso/{provider_id}/oidc/login` y `…/saml/login` (por **provider**, no por tenant).
   - Callback OIDC compartida `GET /auth/sso/oidc/callback` y ACS SAML **global** `POST /auth/sso/saml/acs`
     (el `state`/`RelayState` —server-side, single-use— lleva el provider que inició el flujo; ya era así para
     OIDC). La sesión emitida **prueba identidad** (usuario global), todavía sin tenant activo.

3. **Acceso por membership (sin claiming automático).** El acceso a un tenant lo determinan **exclusivamente** las
   `UserOrganizationMembership` (usuario↔tenant + rol) que **asigna el administrador**. NO se implementa claiming
   por email-domain ni `default_tenant_id` (descartado por el operador a favor de la asignación explícita).
   Tras un login correcto:
   - **0 memberships activas** → pantalla **"No tienes permisos asignados en la plataforma; contacta con el
     administrador"** (sesión válida pero sin tenant; no se entra a la app).
   - **1 membership** → se entra directo a ese tenant.
   - **>1** → **tenant-picker** (ya existe) para elegir.

4. **Administración de usuarios (System Admin).** Nueva superficie `/admin/users` (no existe hoy) + endpoints para
   **listar usuarios y gestionar sus memberships** (asignar/quitar usuario↔tenant + rol, activar/desactivar).
   Reutiliza el modelo `UserOrganizationMembership` (ya existe) y `GET /admin/users` (ya existe). SCIM (Plan 08)
   se mantiene como vía de aprovisionamiento per-tenant (ortogonal); la administración manual es complementaria.

5. **Página de login muestra los providers habilitados.** Endpoint **público** (sin secretos) que lista los
   providers globales habilitados (`id`, `kind`, `display_name`, `button_label`, URL de inicio). `/login` renderiza
   un botón por cada uno + el formulario de password. **Estilo de marca** según las buenas prácticas oficiales de
   cada provider; **`button_label` configurable** por provider; **icono prefijado por `kind`**
   (azure/microsoft, google, github, OIDC/SAML genérico).

6. **SP SAML global.** El SP entityID + ACS pasan a ser **globales** (una identidad de SP para la plataforma), no
   por tenant. La URL de callback/ACS a registrar en el IdP se construye desde `sso_redirect_base_url` (valor que
   fija el operador según su despliegue; el default `http://localhost:8000` es solo placeholder y no coincide con
   el api-server dev en `:8001`) y se muestra **informativamente en la modal** de config, con aviso si sigue en el
   default.

## Consecuencias

- **Rutas**: `/auth/sso/{tenant_id}/…` → `/auth/sso/{provider_id}/…` (login) + ACS global. Migración de las rutas +
  de los clientes (admin-panel).
- **Esquema**: `sso_configurations` deja de estar tenant-scoped → tabla **platform-global** (sin RLS, acceso solo
  `system_admin`), con identidad por `provider`/`kind` (no por tenant). Migración reversible que **consolida** las
  filas per-tenant existentes en globales (si dos tenants tenían el mismo provider, el operador reconcilia; en la
  práctica dev hay pocas). `button_label` añadido.
- **MFA** (TOTP/WebAuthn): per-usuario, **sin cambios** (es ortogonal al scope del provider).
- **Sesiones**: una sesión sin tenant activo es válida (identidad); el tenant se fija al elegir/resolver membership.
- **RBAC/menú**: la config de auth providers vive en el grupo **Plataforma** (System Admin) — el menú ya se colocó
  ahí (plan admin-menu-reorg). `/admin/users` también es System Admin.
- **Seguridad**: password login intacto; el endpoint público de providers no expone secretos; la asignación de
  acceso es explícita (deny-by-default: sin membership, no hay acceso).

## Alternativas consideradas

- **Mantener per-tenant (ADR 0031)**: rechazada — contradice ADR 0028 y la intención del operador; obliga a conocer
  el tenant antes del login (subdominio/claiming) que 0028 ya descartó.
- **Claiming por email-domain / `default_tenant_id`**: rechazada por el operador a favor de **asignación explícita**
  por el administrador (más control, deny-by-default).

## Migración / compatibilidad

- Reversible. Las configs SSO per-tenant existentes se migran a globales; las rutas viejas pueden mantenerse como
  redirección temporal si hace falta (a confirmar). Password + sesiones existentes intactos.

## Trazabilidad

- Decisión del operador (2026-06-02) sobre la divergencia 0028↔0031. Implementa: plan `sso-global-user-admin`.
- Impacta: routers/sso.py, db/models.py (SSOConfiguration), admin-panel (/login, /admin/settings/sso, /admin/users),
  RBAC matriz, y la doc de SSO (se actualizará al cerrar el plan).
