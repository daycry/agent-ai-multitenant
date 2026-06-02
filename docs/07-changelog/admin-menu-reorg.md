---
plan_id: admin-menu-reorg
title: Reorganización del menú admin (grupos/submenús + ámbito + header moderno)
completed_at: null
docs_language: es
---

# Plan admin-menu-reorg — Menú en grupos/submenús + ámbito + header moderno

## Resumen

Plan **de frontend** (no de plataforma), **behavior-preserving**. El
sidebar del `apps/admin-panel` era una lista plana de ~26 ítems con flags
`adminOnly`/`systemAdminOnly`. Este plan lo reorganiza en **5 grupos con
submenús colapsables**, aclara la **visibilidad por ámbito** (qué es
platform-global del System Admin vs qué ve un `tenant_admin`), añade una
**scrollbar moderna** al sidebar, y moderniza el **header** para mostrar el
**tenant actual** + el **usuario logueado**.

> **Behavior-preserving (GUARDRAIL DURO).** Cambios solo de presentación +
> arquitectura de información. **No** se tocaron rutas, llamadas API
> (`apiFetch`/`lib/api`), claves/mutaciones de TanStack Query, props
> públicas ni la lógica de datos. **Ningún `data-testid` eliminado o
> renombrado** (los e2e seleccionan por ellos); los nuevos son **aditivos**.
> **Backend intacto** (no se re-scopea auth/SSO).

## Estructura aprobada del menú

5 grupos, orden y ámbito fijos:

| Grupo                        | Ámbito (flag)                    | Ítems (rutas sin cambios)                                                                          |
| ---------------------------- | -------------------------------- | -------------------------------------------------------------------------------------------------- |
| **Trabajo**                  | por rol (todos)                  | Dashboard, Mis tareas, Tablero, Aprobaciones, Bandeja                                              |
| **Recursos**                 | `tenant_admin` (`adminOnly`)     | Agentes, Agentes humanos, Equipos, Proyectos, Knowledge Bases, Memorias, Documentos                |
| **Configuración del tenant** | `tenant_admin` (`adminOnly`)     | Guardrails, Validación humana, Notificaciones, Calidad (Evals), Estadísticas, Marketplace, Ajustes |
| **Plataforma**               | System Admin (`systemAdminOnly`) | Proveedores LLM, Modelos & Precios, **Auth/SSO**, Backups (+ destinos + restaurar)                 |
| **Ayuda**                    | por rol (todos)                  | Documentación                                                                                      |

- **`Plataforma` = platform-global (ADR 0028):** solo el System Admin del
  tenant especial la ve (`systemAdminOnly`). `Configuración del tenant` y
  `Recursos` son del `tenant_admin` (`adminOnly`).
- **`Auth/SSO` se reubica de _Ajustes del tenant_ al grupo `Plataforma`**
  por coherencia con ADR 0028 (proveedores de auth platform-global).

## Cambios por tarea

### Fase A — Menú agrupado + ámbito + scrollbar

- **`task_menu_01`** — **Nav en grupos con submenús colapsables + scope +
  scrollbar moderna.** Reestructura el `NAV` de `admin-shell.tsx` en los 5
  grupos con submenús **colapsables** (estado abierto/cerrado por grupo en
  `localStorage`; el grupo del ítem activo se auto-expande), gating por
  grupo/ítem (`systemAdminOnly`/`adminOnly`) coherente con ADR 0028 —
  incluida la reubicación de **Auth/SSO** a `Plataforma`. Scrollbar
  moderna del sidebar como **utilidad reutilizable** en `app/globals.css`
  (`scrollbar-width`/`scrollbar-color` + `::-webkit-scrollbar`, cableada a
  los tokens `--sidebar*`). Se preservan **todas** las rutas y los
  `data-testid` existentes (`nav-*`, `sidebar-nav`, `mobile-nav`); los
  testids de grupo son nuevos/aditivos.

### Fase B — Header moderno

- ✅ **`task_menu_02`** — **Header con tenant actual + usuario.**
  `admin-header.tsx` muestra el **tenant actual** (el `TenantPicker`
  existente para el System Admin; un pill estático
  `current-tenant`/`current-tenant-name` para `tenant_admin`/`tenant_user`,
  con el nombre resuelto desde las memberships de `/me`) + el **usuario**
  (avatar con la inicial + nombre/email y menú `user-menu` →
  `user-menu-popover` con **Perfil** [`user-menu-profile`] / **Cerrar
  sesión** [`logout`]). Sticky, accesible (`Escape` cierra, foco al primer
  ítem, `role="menu"`/`menuitem`). Se conservan el logout y todos los
  `data-testid` existentes (`admin-header`, `open-mobile-nav`,
  `lang-switcher`/`lang-es`/`lang-en`, `role-badge`); se **añaden**
  `current-tenant`, `current-tenant-name` y `user-menu-profile`.

### Fase C — Docs + verificación

- ✅ **`task_menu_03`** — **Changelog + ui-conventions + verificación**
  (esta entrada). Sección **"Navegación del panel — sidebar + header"**
  añadida a
  [`docs/03-guides/ui-conventions.md`](../03-guides/ui-conventions.md)
  (los 5 grupos + reglas de ámbito + comportamiento colapsable + utilidad
  de scrollbar moderna + patrón del header tenant/usuario). Fila del plan
  añadida a `docs/roadmap/README.md` (planes de frontend). Verificación
  final en verde (typecheck/lint/build) y **cross-check de `data-testid`**.

## Nota de ámbito — SSO es per-tenant (re-scope diferido)

El menú coloca **Auth/SSO** bajo `Plataforma` (`systemAdminOnly`) por
coherencia con ADR 0028. **Pero el backend de SSO es per-tenant**, no
platform-global: el ADR 0031 define el ACS SAML como
`/auth/sso/{tenant_id}/saml/acs` (un proveedor SSO por tenant), y las
pantallas de configuración viven en `/admin/settings/sso` y
`/admin/settings/sso/saml`.

Por tanto, conforme al guardrail del plan, aquí **solo se recoloca el
menú** y se conserva la ruta; **no se re-scopea el backend de auth/SSO**.
Si en el futuro se quisiera promover SSO a configuración platform-global
(un proveedor de identidad para toda la plataforma), eso requiere **otro
plan** con su propio ADR (toca rutas, modelo de datos y RLS) y queda
**diferido**.

## Preservación de `data-testid` (NON-NEGOTIABLE)

Cross-check de los productores estáticos de `data-testid` del
`admin-panel` (`components/`, `app/`, `lib/`) entre el punto de rama de
este plan y `HEAD`:

- **Presentes en la base del plan y ausentes en HEAD (pérdida neta): 0.**
- **Nuevos en HEAD (aditivos, del header `task_menu_02`):**
  `current-tenant`, `current-tenant-name`, `user-menu-profile`.

Los `data-testid` que usan los e2e del shell/header siguen intactos:
`nav-*` (`nav-agents`, `nav-teams`, `nav-projects`, `nav-board`,
`nav-approval-policy`, …), `sidebar-nav`, `mobile-nav`, `admin-header`,
`open-mobile-nav`, `lang-switcher`/`lang-es`/`lang-en`, `role-badge`,
`user-menu`/`user-menu-popover`/`logout`, y los del `TenantPicker`.

## Migraciones

**Ninguna.** Plan puramente frontend; no toca esquema ni backend.

## Verificación

- `npm run typecheck` ✅ (tsc `--noEmit`).
- `npm run lint` ✅ (sin errores; solo warnings preexistentes
  `react-hooks/exhaustive-deps` ajenos al plan).
- `npm run build` ✅ (Next.js production build; rutas `/admin/*` intactas,
  incluidas `/admin/settings/sso` y `/admin/settings/sso/saml`).
- `pre-commit` (prettier, **scoped a los ficheros cambiados** por la
  incidencia conocida de libuv en Windows con `--all-files`) ✅. Sin
  `--no-verify`.
- **Cross-check `data-testid`:** 0 pérdidas netas respecto al punto de
  rama del plan; 3 testids nuevos (aditivos).

## Pendiente

- **Tests humanos del plan** — pendientes de ejecutar por un humano
  (sidebar agrupado/colapsable + ámbito correcto + scrollbar fina +
  header tenant/usuario; ningún enlace cambió de ruta; los e2e existentes
  siguen pasando).
- **Merge del PR de `plan/admin-menu-reorg` a `main`** — lo gestiona el
  humano tras los tests humanos. El plan no se marca `completed` aquí.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar
los tests humanos del plan).
