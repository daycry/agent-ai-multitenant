---
plan_id: sso-global-user-admin
title: SSO/auth global (supersede ADR 0031) + administración de usuarios + login con providers
status: in_progress
blocking_plan: []
started_at: 2026-06-02
completed_at: null
estimated_duration_calendar: 6-8 días
estimated_effort_person_days: 7
estimated_cost_human_eur: 3.000 € – 5.000 €
estimated_cost_ai_eur: 80 € – 160 €
created_by: system_architect
spec_sections_referenced: [3, 17, 28]
docs_language: es
---

# Plan sso-global-user-admin — Auth/SSO platform-global + administración de usuarios

> Re-arquitectura crítica de auth. Diseño en **ADR 0047** (supersede 0031, re-alinea con 0028). **No se toca código
> de auth hasta el OK del operador al ADR.** Password login intacto al lado (SSO/MFA alongside, no replacing).

## Cabecera

| Campo           | Valor                        |
| --------------- | ---------------------------- |
| **ID del Plan** | `sso-global-user-admin`      |
| **Rama git**    | `plan/sso-global-user-admin` |
| **ADR**         | 0047 (proposed)              |

## Resumen

Plan 08 implementó SSO **per-tenant** (ADR 0031), divergiendo de **ADR 0028** (auth platform-global). El operador
confirma el modelo de 0028: **login global con providers configurados en el tenant especial (System Admin), acceso
a tenants por memberships que asigna el administrador, y pantalla de "sin acceso" si el usuario no tiene ninguna.**
Este plan re-arquitectura SSO a global, añade la administración de usuarios, y muestra los providers en el login.

## Alcance

Ver **ADR 0047** para el diseño completo. Resumen de lo que entra:

- Auth providers (OIDC/SAML) **platform-global** (System Admin); `sso_configurations` deja de ser tenant-scoped.
- **Login global** por provider (`/auth/sso/{provider_id}/...`), callback/ACS globales; sesión = identidad.
- **Acceso por membership** (sin claiming): 0 → pantalla "sin permisos, contacta al admin"; 1 → entra; >1 → picker.
- **Administración de usuarios** (System Admin): `/admin/users` + endpoints de memberships (asignar/quitar tenant+rol).
- **Página de login** lista los providers habilitados (endpoint público sin secretos) con botones de **marca**
  (label configurable por provider, icono por `kind`).
- **Modal de config SSO** muestra callback/ACS/SP-entity/metadata informativo (subsume el task de #33).
- Password login intacto. Migración reversible. Tests. Supersede 0031 + actualiza docs SSO.

## Tareas

### Fase A — Modelo global + migración

#### `task_sso_01` — `sso_configurations` platform-global + button_label + migración

- [x] **Título**: Re-scopear `SSOConfiguration` a platform-global (sin RLS tenant; identidad por provider/kind, no
      por tenant), añadir `button_label`; migración reversible que consolida las filas per-tenant existentes en
      globales. Repos/queries (`_load_enabled_oidc_config`/`_load_enabled_saml_config`) pasan a globales.
- **Tests**: `pytest tests/integration/test_sso_global_config.py + tests/integration/test_migrations.py -v`

### Fase B — Login global + providers públicos

#### `task_sso_02` — Rutas de login por provider + callback/ACS global + endpoint público de providers

- [x] **Título**: `/auth/sso/{provider_id}/oidc|saml/login` (por provider, no tenant); callback OIDC + ACS SAML
      globales (state/RelayState lleva el provider); `GET /auth/sso/providers` PÚBLICO (id/kind/display_name/
      button_label/login_url, SIN secretos). Sesión emitida sin tenant activo.
- **Tests**: `pytest tests/integration/test_sso_global_login.py -v` (login por provider; providers públicos sin secretos)

### Fase C — Resolución de tenant por membership + pantalla "sin acceso"

#### `task_sso_03` — Resolución post-login + pantalla "sin permisos"

- [x] **Título**: Tras login (SSO o password): 0 memberships activas → respuesta/pantalla "sin permisos, contacta
      al admin" (sesión válida, sin tenant); 1 → tenant activo; >1 → tenant-picker. Backend + UI de la pantalla.
- **Tests**: `pytest tests/integration/test_post_login_membership_resolution.py -v` (@pytest.mark.cross_tenant)

### Fase D — Administración de usuarios

#### `task_sso_04` — `/admin/users` (UI + endpoints de memberships)

- [x] **Título**: Endpoints System Admin para listar usuarios + asignar/quitar membership (usuario↔tenant+rol) +
      activar/desactivar; UI `/admin/users` (lista + asignación de tenants/roles). Reusa `UserOrganizationMembership` + `GET /admin/users`.
- **Tests**: `pytest tests/integration/test_admin_user_memberships.py -v` (asignar/quitar; RBAC system_admin)

### Fase E — Página de login con providers + modal informativa

#### `task_sso_05` — Botones de provider en /login (marca) + callback en modal

- [x] **Título**: `/login` muestra un botón por provider habilitado (estilo de marca oficial por `kind`,
      `button_label` configurable, icono prefijado) + password. Modal de config SSO muestra callback/ACS/SP-entity/
      metadata informativo + copiar + base configurada (aviso si default). _(typecheck/lint/build verde; e2e escritos
      NO ejecutados)._ Usar frontend-design para la calidad de marca.
- **Tests**: admin-panel `typecheck && lint && build` verde + e2e `login-providers.spec.ts` (escrito, no ejecutado)

### Fase F — Docs + cierre del ADR

#### `task_sso_06` — Aceptar ADR 0047 + docs SSO + changelog + RBAC

- [x] **Título**: ADR 0047 → `accepted`; actualizar docs de SSO (guías/runbook/reference) al modelo global;
      `docs/04-reference/rbac.md` (auth-providers global system_admin + /admin/users); changelog
      `docs/07-changelog/sso-global-user-admin.md`; fila en roadmap README.
- **Tests**: `test -f` changelog + docs SSO mencionan el modelo global

### Fase G — Reescritura de los tests heredados per-tenant

#### `task_sso_07` — Adaptar las 5 suites SSO/auth viejas al modelo global

- [x] **Título**: Reescritura de las suites SSO/auth per-tenant que rompían tras la migración 0076. Las 5 nombradas en
      el encargo + **2 adicionales detectadas en la selección completa** (`test_jit_provisioning.py`,
      `test_group_mapping.py`) que también seedean `tenant_id` y asumen membership/grupo-en-login (ambos retirados por
      ADR 0047). Se reescriben `test_oidc_config_crud.py` + `test_saml_config_crud.py` al CRUD **global + system_admin**
      (seed sin `tenant_id`; "tenant_user puede listar / no puede borrar" → "system_admin gestiona / no-system-admin
      403"; "segundo create 409" → unicidad global por provider; "edit conserva/re-cifra secreto" se mantiene; los
      tests de aislamiento per-tenant y de SP-metadata per-tenant se ELIMINAN — SP-metadata es global zero-arg).
      `test_saml.py` pasa a las rutas globales (`/auth/sso/{provider_id}/saml/login` + `POST /auth/sso/saml/acs`, seed
      global); la sesión emitida es de **identidad sin tenant** (assert `active_tenant_id is None` + `memberships ==
    []`); los cross-tenant se sustituyen por **RelayState ligado a su provider** (un RelayState capturado no se puede
      dirigir a otro provider). `test_saml_crypto.py` mantiene los tests de cripto reescritos al config global y
      reemplaza el `test_sp_signing_config_isolated_per_tenant` por un único test de SP-signing global.
      `test_jit_provisioning.py` se reescribe al **aprovisionamiento de identidad global** (usuario creado con hash
      centinela `!sso-no-local-login!` + `is_sso_provisioned`, sin membership; link-by-email sin duplicar; el usuario
      SSO no entra por login local; idempotencia bajo carrera) y ELIMINA las aserciones de membership/tenant.
      `test_group_mapping.py` se reduce a los **unit tests puros** de `resolve_role_from_groups`/`is_grantable_role`
      (siguen válidos: la columna `group_role_mappings` y su validador siguen existiendo) y ELIMINA el e2e
      grupo→rol-en-login (ADR 0047: el login no lee grupos ni crea membership). `test_login_discovery.py` se **ELIMINA**
      (ADR 0047: no hay claiming por email-domain; los providers públicos los cubre `test_sso_global_login.py`).
- **Bug de producción corregido**: el CRUD de `routers/sso.py` seguía con `require_tenant_admin` + `get_tenant_session`
  tras la migración a tabla global (sin RLS). Eso permitía que **cualquier `tenant_admin` de cualquier tenant**
  leyera/modificara/borrara la config SSO **platform-global** — escalada de privilegios contra el límite de
  plataforma, contra ADR 0047 ("acceso solo `system_admin`"). Corregido: todos los endpoints CRUD/helpers de SSO
  pasan a `require_system_admin` + `get_admin_session` (BYPASSRLS), igual que `/admin/*` y `llm_providers`.
- **Tests**: `pytest tests/integration -k "sso or saml or oidc or login or jit or group_map or scim or mfa or auth or membership or session" -q` → 0 failed (full SSO/auth suite verde; password/MFA/SCIM intactos).

## Tests humanos del Plan

```yaml
- id: human_sso_01
  description: "Login global + acceso por membership"
  checklist:
    - "En /login aparecen los providers globales habilitados como botones de marca (label configurable, icono por tipo) + password"
    - "Login con un provider funciona sin indicar tenant; la callback es la misma para todos"
    - "Un usuario sin memberships ve la pantalla 'sin permisos, contacta al administrador' (no entra)"
    - "El System Admin asigna al usuario un tenant + rol en /admin/users; tras re-login el usuario entra a ese tenant"
    - "Con varias memberships, el tenant-picker deja elegir"
    - "El login por password sigue funcionando igual"
    - "La modal de config SSO muestra la callback/ACS a registrar en el IdP (con copiar)"
```

## Criterios de cierre

1. Tareas `[x]`; `pytest tests/unit tests/integration -v` verde; admin-panel `typecheck && lint && build` verde.
2. `pre-commit` (prettier scoped) verde; migración reversible (cabeza única).
3. Password login intacto; 0-membership ⇒ pantalla "sin acceso" verificado; providers públicos sin secretos.
4. ADR 0047 → accepted; docs SSO al día.
5. Tests humanos validados.
6. Changelog + fila README.
7. PR de `plan/sso-global-user-admin` mergeado (lo hace el humano).

## Próximo Plan

Ninguno pendiente tras este (cierra las peticiones del operador).
