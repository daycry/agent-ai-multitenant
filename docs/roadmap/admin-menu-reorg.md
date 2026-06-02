---
plan_id: admin-menu-reorg
title: Reorganización del menú admin (grupos/submenús + ámbito + header moderno)
status: in_progress
blocking_plan: []
started_at: 2026-06-02
completed_at: null
estimated_duration_calendar: 2-3 días
estimated_effort_person_days: 2
estimated_cost_human_eur: 800 € – 1.500 €
estimated_cost_ai_eur: 30 € – 70 €
created_by: frontend_lead
spec_sections_referenced: [33]
docs_language: es
---

# Plan admin-menu-reorg — Menú en grupos/submenús + ámbito + header moderno

> Plan de frontend (IA + visibilidad). `plan_id` descriptivo. UI-only, behavior-preserving. Estructura aprobada por el operador.

## Cabecera

| Campo           | Valor                   |
| --------------- | ----------------------- |
| **ID del Plan** | `admin-menu-reorg`      |
| **Rama git**    | `plan/admin-menu-reorg` |

## Resumen

El sidebar es una lista plana de ~26 ítems (admin-shell.tsx) con flags `adminOnly`/`systemAdminOnly`. El operador
pide: (1) agrupar en **submenús colapsables** (compacto); (2) **visibilidad por ámbito** clara — qué es del tenant
especial / System Admin (platform-global, ADR 0028) vs qué ve un tenant_admin en cualquier tenant; (3) **scrollbar
moderna** en el sidebar; (4) **header moderno** que muestre el tenant actual + el usuario logueado.

## Alcance

**Entra** (UI-only, behavior-preserving — preservar rutas + TODOS los `data-testid`):

- **Menú en grupos con submenús colapsables** (recordar abierto/cerrado en localStorage; el ítem activo expande su
  grupo). Grupos APROBADOS: **Trabajo** (Dashboard, Mis tareas, Tablero, Aprobaciones, Bandeja) · **Recursos**
  (Agentes, Agentes humanos, Equipos, Proyectos, Knowledge Bases, Memorias, Documentos) · **Configuración del
  tenant** (Guardrails, Validación humana, Notificaciones, Calidad/Evals, Estadísticas, Marketplace, Ajustes) ·
  **Plataforma** (System Admin: Proveedores LLM, Modelos & Precios, **Auth/SSO**, Backups +destinos/restaurar) ·
  **Ayuda** (Documentación).
- **Ámbito**: `Plataforma` solo visible al System Admin (systemAdminOnly); `Configuración del tenant` al
  tenant*admin (adminOnly). CORRECCIÓN: la entrada **SSO** pasa de \_Ajustes del tenant* al grupo **Plataforma**
  (systemAdminOnly) — coherente con ADR 0028 (auth providers platform-global). Revisar que cada ítem tenga el flag
  correcto. (Si el backend de SSO resultara per-tenant y no global, NO cambiarlo aquí — solo colocar el menú y
  dejar nota; el re-scope de auth sería otro plan.)
- **Scrollbar moderna** del sidebar cuando hay overflow: fina, con tokens del tema (scrollbar-width/color +
  `::-webkit-scrollbar`), como utilidad reutilizable.
- **Header moderno** (admin-header.tsx): mostrar el **tenant actual** (nombre, pill/badge) + el **usuario**
  (avatar inicial/nombre + menú con perfil/logout); integrar el TenantPicker existente (para System Admin). Datos:
  tenant del tenant-context, usuario de `useCurrentUser`. Siempre visible.
- Changelog + nota en la guía ui-conventions.

**Queda fuera**: cambiar rutas/endpoints; re-scope del backend de auth/SSO; rediseño visual (ya hecho en ui-refresh).

## Decisiones clave

- **Behavior-preserving**: preservar rutas + `data-testid` (los e2e dependen). Submenús + header son presentación/IA.
- **Ámbito guiado por RBAC + ADR 0028**: `systemAdminOnly` = platform-global (tenant especial); `adminOnly` = tenant.
- **SSO al grupo Plataforma** (System Admin); si el backend SSO es per-tenant, solo se recoloca el menú + nota.

## Tareas

### Fase A — Menú agrupado + ámbito + scrollbar

#### `task_menu_01` — Nav en grupos con submenús colapsables + scope + scrollbar moderna

- [ ] **Título**: Reestructurar el NAV de admin-shell.tsx en los 5 grupos con submenús **colapsables** (estado en
      localStorage; ítem activo abre su grupo); gating por grupo/ítem (systemAdminOnly/adminOnly) coherente con ADR
      0028 — mover la entrada SSO a **Plataforma** (systemAdminOnly). Scrollbar moderna del sidebar (utilidad
      reutilizable). Preservar todas las rutas + `data-testid` (añadir testids de grupo, no quitar los existentes).
- **Tests**: `npm run typecheck && lint && build` verde + nav-\* `data-testid` existentes preservados

### Fase B — Header moderno

#### `task_menu_02` — Header con tenant actual + usuario

- [x] **Título**: admin-header.tsx muestra el **tenant actual** (nombre/pill) + el **usuario** (avatar inicial +
      nombre + menú perfil/logout), integrando el TenantPicker (System Admin). Moderno, accesible; preservar el
      logout + `data-testid` existentes.
- **Tests**: `npm run typecheck && lint && build` verde

### Fase C — Docs

#### `task_menu_03` — Changelog + ui-conventions + verificación

- [x] **Título**: Actualizar `docs/03-guides/ui-conventions.md` (estructura del menú por grupos + ámbito + header);
      changelog `docs/07-changelog/admin-menu-reorg.md`; fila en roadmap README; verificación final
      (typecheck/lint/build + 0 `data-testid` de e2e perdidos). Nota si el backend SSO no es global.
- **Tests**: build verde; `test -f` changelog; 0 `data-testid` de e2e perdidos

## Tests humanos del Plan

```yaml
- id: human_menu_01
  description: "Menú agrupado + ámbito + header"
  checklist:
    - "El sidebar muestra grupos colapsables (Trabajo/Recursos/Config tenant/Plataforma/Ayuda); compacto"
    - "Como tenant_admin (no system): NO se ve el grupo Plataforma (Proveedores LLM, Precios, SSO, Backups)"
    - "Como System Admin: se ve Plataforma; SSO está ahí (no en Ajustes del tenant)"
    - "Si el sidebar desborda, la scrollbar es fina/moderna, no la nativa"
    - "El header muestra el tenant actual + el usuario logueado; el System Admin puede cambiar de tenant"
    - "Ningún enlace cambió de ruta; los e2e existentes siguen pasando"
```

## Criterios de cierre

1. Tareas `[x]`; `npm run typecheck && lint && build` verde; `pre-commit` (scoped prettier) verde.
2. 0 `data-testid` de e2e eliminados; rutas sin cambios.
3. Ámbito correcto (Plataforma solo System Admin; SSO en Plataforma).
4. Test humano validado.
5. Changelog + fila en README.
6. PR de `plan/admin-menu-reorg` mergeado (lo hace el humano).

## Próximo Plan

Tras este: actualización integral de la documentación (#24, el último).
