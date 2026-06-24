---
adr_id: "0079"
title: "Rol de aprobación de planes (project_approval) y modelo de roles del tenant"
status: accepted
date: 2026-06-23
authors: [claude-opus, workflow-analisis-lifecycle-y-roles]
plan_referenced: mejoras-2026-06-chat-coste-cortex
docs_language: es
related: ["0028", "0047", "0074"]
supersedes: []
---

# ADR 0079 — Rol de aprobación de planes (`project_approval`) y modelo de roles del tenant

> **Estado: `accepted` (2026-06-23)** — el operador eligió la **Opción A** (rol
> `plan_approver` a nivel de tenant) y CONFIRMÓ las preguntas abiertas (2026-06-23):
>
> - **(2) Firma única:** SÍ — un `plan_approver` aprueba en solitario igual que un
>   `tenant_admin` (en planes por encima del umbral aplica la doble firma normal).
> - **(3) Doble firma:** firmantes mixtos permitidos — dos firmantes DISTINTOS cualesquiera
>   de `{tenant_admin, plan_approver}` cierran la doble firma.
> - **(4) Alcance / multi-rol:** roles **exclusivos** por (usuario, tenant) — un usuario
>   tiene UN rol por tenant (`UniqueConstraint(user_id, tenant_id)`), así que NO se es
>   `plan_approver` y `tenant_admin` a la vez en el mismo tenant (ni hace falta: un
>   `tenant_admin` ya aprueba). Un `plan_approver` tiene además acceso de **miembro normal**
>   (lee/escribe recursos como un `tenant_user`) porque pasa `require_tenant_member`. Entre
>   tenants distintos sí puede tener roles distintos. **No se adopta multi-rol N:M** (sería
>   otro ADR si algún día hace falta).
> - **(5) Alcance futuro:** acotado a planes; más acciones sensibles → ADR futuro / Casbin.
> - **(6) SSO/SCIM:** NO por ahora — el rol se asigna solo desde el admin-panel.

## Contexto

El operador propone un rol cuya función sea **aprobar los planes de los proyectos**, para
desacoplar "quién aprueba presupuesto/planes" de "quién administra el tenant" (segregación
de funciones / cuatro ojos). Antes de añadirlo hay que entender el modelo actual.

**Modelo de roles HOY (verificado en código):**

- Los roles de un usuario dentro de un tenant son un **enum cerrado** `UserRole` en
  `user_org_memberships.role` (columna `String(32)`):
  `tenant_admin`, `tenant_user`, `system_operator`. Más el booleano **global**
  `users.is_system_admin` (fuera del enum).
- La autorización es **puramente enum + booleanos** vía dependencias FastAPI:
  `require_tenant_member` (membresía activa) y `require_tenant_admin` (membresía +
  `role == "tenant_admin"`). **No hay Casbin** instanciado ni tabla de policies —
  pese a que `CLAUDE.md` lo menciona como objetivo, hoy no existe.
- Existe una _factory_ `require_tenant_role(...)` pero **no se usa** (la guía dice usar
  las prehechas `require_tenant_admin/member`).
- **Aprobar un plan** (`POST /plans/{id}/approve`) está cableado a `require_tenant_admin`.
- Ya existe **doble firma** (ADR/tarea 03_25): por encima de un umbral de coste
  (`plan_approval_double_signature_threshold`, global) el plan pasa a
  `pending_second_approval` y un **segundo firmante distinto** (también `tenant_admin`)
  lo cierra. El guard de "no mismo firmante" vive en `plan_state_machine.transition_plan_status`.
- RLS de PostgreSQL aísla por tenant (`app.user_id`/`app.tenant_id`); todos los permisos
  son **a nivel tenant**, no hay permisos por-proyecto.

## Problema / drivers

1. Hoy **solo `tenant_admin` aprueba planes**: no se puede delegar la aprobación sin dar
   el control total del tenant.
2. No hay granularidad por-proyecto (un aprobador de un proyecto concreto).
3. El sistema es enum-based: cualquier permiso nuevo es lógica imperativa + (quizá) schema.

## Opciones

### A — Nuevo valor de enum de membership `plan_approver` (tenant-wide)

Añadir `plan_approver` a `UserRole`. La aprobación deja de exigir `tenant_admin` y pasa a
una dependencia nueva `require_can_approve_plan` que acepta `tenant_admin` **o**
`plan_approver`. Granularidad: **todo el tenant** (aprueba planes de cualquier proyecto).

- ✅ Encaja con el modelo actual; cambio pequeño y de bajo riesgo; SSO ya mapea roles de
  enum (default `tenant_user`).
- ✅ No toca los 40+ endpoints que usan `require_tenant_admin` (se crea una dependencia
  nueva, no se modifica la existente).
- ❌ No es por-proyecto. Escala mal si en el futuro se quieren permisos finos
  (`can_approve_tasks_of`, etc.).

### B — Booleano por-membership `can_approve_plans`

Columna booleana en `user_org_memberships`. Mismo alcance tenant-wide que A, pero ortogonal
al rol (un `tenant_user` con el flag puede aprobar).

- ✅ No "infla" el enum; un usuario conserva su rol y gana el permiso.
- ❌ Empieza a inventar un sistema de permisos ad-hoc por columnas booleanas (no escala).

### C — Tabla de grants por-proyecto `project_approvers(user_id, project_id)`

Granularidad **por proyecto**: ser aprobador del proyecto A pero no del B.

- ✅ Es lo que sugiere literalmente "aprobar los planes **de los proyectos**".
- ❌ 3× más trabajo administrativo (asignar por proyecto), nueva tabla + RLS + UI; errores
  opacos (403) si falta el grant.

### D — Introducir Casbin (policies en BD)

El modelo RBAC "de verdad" que `CLAUDE.md` cita.

- ✅ Escala a permisos finos.
- ❌ Coste alto: enforcer, modelo, policies, migración de los gates actuales. Desproporcionado
  para un solo permiso nuevo.

## Recomendación

**Fase 1 (MVP, recomendado): Opción A** — `plan_approver` tenant-wide + dependencia
`require_can_approve_plan` (acepta `tenant_admin` ∪ `plan_approver`). Es el mínimo correcto,
encaja con el modelo enum existente, no rompe los gates actuales y cubre el caso de uso
(delegar la aprobación de planes sin dar admin). Documentar explícitamente que el enum es un
_escape hatch_ y que la granularidad por-proyecto (Opción C) o Casbin (D) se abordarán con su
propio ADR **si** aparece esa necesidad.

**Doble firma:** un `plan_approver` cuenta como firmante igual que un `tenant_admin` (el guard
de "firmante distinto" sigue aplicando). Para planes caros, dos firmantes distintos
cualesquiera de `{tenant_admin, plan_approver}` cierran la doble firma.

**Admin-panel:** exponer el rol en la edición de membresías (igual que tenant_admin/user).

## Preguntas abiertas para el operador (decidir antes de implementar)

1. **Granularidad:** ¿tenant-wide (Opción A, recomendada) o por-proyecto (Opción C)?
2. **Firma única:** ¿un `plan_approver` puede aprobar en **firma única** (igual que un admin),
   o solo cuenta para la **segunda** firma en planes caros?
3. **Firmantes mixtos:** ¿quieres permitir explícitamente 1ª firma `plan_approver` + 2ª
   `tenant_admin` (y viceversa), o exigir cierto rol en alguna de las firmas?
4. **Lectura/escritura:** ¿el `plan_approver` necesita además permisos de lectura/edición
   sobre los planes/proyecto, o **solo aprobar**?
5. **Alcance futuro:** ¿este rol será solo para planes, o anticipas más acciones sensibles
   (aprobar cambios de agentes, etc.) que justifiquen ya Casbin (Opción D)?
6. **SSO/SCIM:** ¿hace falta que el IdP pueda provisionar `plan_approver`, o se asigna solo
   desde el admin-panel?

## Consecuencias

- **Si A:** migración trivial (valor de enum + check), nueva dependencia, 1 cambio en
  `approve_plan`, UI de membresías, tests (cross-tenant, separación de rol, doble firma con
  roles mixtos). Reversible.
- La autorización sigue siendo enum-based; este ADR **no** introduce Casbin (se deja para un
  ADR futuro si el roadmap pide permisos finos).
- Hasta que el operador responda las preguntas abiertas, **no se implementa**.
