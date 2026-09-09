---
title: Mapa de pantallas del panel — qué ruta, en qué área, para qué rol
audience: frontend-dev, tenant-admin, system-admin, architect
phase: ui-reestructuracion-2026-09-09
updated: 2026-09-09
---

# Mapa de pantallas del panel — Referencia

Las **81 rutas** que sirve `apps/admin-panel/app/**/page.tsx`, con el **área** a la
que pertenecen tras la reestructuración de la UI
([plan `ui-reestructuracion-2026-09-09`](../roadmap/ui-reestructuracion-2026-09-09.md))
y el **rol** que las ve en el menú.

Dos cosas que conviene leer antes de usar esta tabla:

- **El área es dónde se enseña; la ruta no cambia.** El plan reorganiza la
  arquitectura de información —tres áreas, el proyecto como hub— y dice por
  escrito que las URL se conservan. La guarda
  [`tests/unit/test_admin_panel_routes_preserved.py`](../../tests/unit/test_admin_panel_routes_preserved.py)
  lo comprueba en cada suite con la foto tomada el 2026-09-09, antes de mover la
  primera pantalla.
- **El rol de esta tabla es el del MENÚ, no el permiso.** La barrera real es el
  backend (RBAC + RLS); el gating del NAV es UX y el más restrictivo manda
  (`adminOnly` → tenant_admin, `systemAdminOnly` → System Admin, `systemOwnerOnly`
  → System Owner). Desde `task_ui_01` los grupos viven en
  [`sidebar-work.tsx`](../../apps/admin-panel/components/layout/sidebar-work.tsx) y
  [`sidebar-system.tsx`](../../apps/admin-panel/components/layout/sidebar-system.tsx),
  las pestañas del proyecto en
  [`nav-model.ts`](../../apps/admin-panel/components/layout/nav-model.ts) —que es
  también donde se decide el área de cada ruta— y `admin-shell.tsx` sólo los
  re-exporta. Una pantalla **sin entrada de menú** se alcanza navegando o por
  enlace directo, y su columna dice de dónde se llega.

## Áreas

| Área         | Qué es                                                                                                                    |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- |
| **Trabajo**  | El tenant: Inicio (dashboard, bandeja humana), Proyectos, Recursos del tenant y Ajustes del tenant                        |
| **Proyecto** | Barra contextual dentro de un proyecto: Resumen · Tablero · Runs · Equipo · Capacidades · Conocimiento · Costes · Ajustes |
| **Sistema**  | Sólo System Admin, separada del menú del tenant (ADR 0117 c)                                                              |
| **Cabecera** | Fuera de las barras: selectores, usuario, salud degradada y el asistente como botón flotante                              |
| **Sin área** | Pantallas de sesión y portal público: no cuelgan de ninguna barra lateral                                                 |

## Trabajo — Inicio

| Ruta                         | Sitio en el menú               | Rol        | Notas                                                                       |
| ---------------------------- | ------------------------------ | ---------- | --------------------------------------------------------------------------- |
| `/admin/dashboard`           | Inicio · Dashboard             | Cualquiera | Reescrito por `task_ui_02` con las seis secciones y `GET /tenant/dashboard` |
| `/admin/inbox`               | Inicio · Bandeja humana        | Cualquiera | `task_ui_03` le pone las cuatro pestañas con contador                       |
| `/admin/approvals`           | (pestaña Aprobaciones)         | Cualquiera | Sigue respondiendo; redirige a su pestaña de la bandeja                     |
| `/admin/human-queue`         | (pestaña Preguntas)            | Cualquiera | `ask_human` (ADR 0123)                                                      |
| `/admin/notifications/inbox` | (pestaña Notificaciones)       | Cualquiera | Ojo: `/admin/notifications` es la CONFIGURACIÓN, no la bandeja              |
| `/admin/review/active`       | (pestaña Revisiones)           | Cualquiera | Sesiones de revisión humana en curso                                        |
| `/admin/review/[id]`         | Sin entrada — desde Revisiones | Cualquiera | Una sesión concreta                                                         |

## Trabajo — Proyectos

| Ruta                  | Sitio en el menú                | Rol          | Notas                                                       |
| --------------------- | ------------------------------- | ------------ | ----------------------------------------------------------- |
| `/admin/projects`     | Proyectos (portfolio)           | tenant_admin | Al entrar en uno, la barra cambia a la del proyecto         |
| `/admin/projects/new` | Sin entrada — desde arriba      | tenant_admin | Alta de proyecto                                            |
| `/admin/board`        | Proyectos · Portfolio de planes | Cualquiera   | `task_ui_11` lo convierte en portfolio con columna Proyecto |

## Proyecto (barra contextual)

Todas cuelgan de `/admin/projects/[id]`; conservan su ruta y pasan a ser pestañas.

| Ruta                                          | Pestaña                         | Notas                                            |
| --------------------------------------------- | ------------------------------- | ------------------------------------------------ |
| `/admin/projects/[id]`                        | Resumen                         | `GET /projects/{id}/overview` (`task_ui_10`)     |
| `/admin/projects/[id]/plans`                  | Tablero (planes)                | Doble Kanban del principio 6                     |
| `/admin/projects/[id]/plans/[planId]`         | Tablero (tareas del plan)       |                                                  |
| `/admin/projects/[id]/tasks`                  | Tablero (tareas)                |                                                  |
| `/admin/plans/[id]/escalated`                 | Tablero · escalaciones          | **Área por confirmar** (ver §Preguntas abiertas) |
| `/admin/executions/[id]`                      | Runs · detalle                  | Se llega desde Runs y desde el dashboard         |
| `/admin/projects/[id]/mcp-servers`            | Capacidades · MCP               | `task_ui_20` la funde con tools y skills         |
| `/admin/projects/[id]/agent-tools-diagnostic` | Capacidades · diagnóstico       | Sólo lectura                                     |
| `/admin/projects/[id]/commands`               | Capacidades · comandos          |                                                  |
| `/admin/projects/[id]/knowledge-bases`        | Conocimiento                    | `task_ui_21` la convierte en flujo guiado        |
| `/admin/projects/[id]/memories`               | Ajustes · memorias del proyecto |                                                  |
| `/admin/projects/[id]/incoming-webhooks`      | Ajustes · webhooks entrantes    |                                                  |
| `/admin/projects/[id]/dep-cache`              | Ajustes · caché de dependencias |                                                  |
| `/admin/projects/[id]/chat`                   | Sin pestaña — acción            | Chat del proyecto                                |

## Trabajo — Recursos del tenant

| Ruta                                                | Sitio en el menú                     | Rol          | Notas                                             |
| --------------------------------------------------- | ------------------------------------ | ------------ | ------------------------------------------------- |
| `/admin/agents`                                     | Recursos · Agentes                   | tenant_admin | `task_ui_30` le aplica el patrón de recurso       |
| `/admin/agents/[id]`                                | Sin entrada — desde Agentes          | tenant_admin | `task_ui_32` la agrupa en cinco pestañas          |
| `/admin/teams`                                      | Recursos · Equipos                   | tenant_admin |                                                   |
| `/admin/teams/[team_id]`                            | Sin entrada — desde Equipos          | tenant_admin |                                                   |
| `/admin/human-agents`                               | Recursos · Agentes humanos           | tenant_admin |                                                   |
| `/admin/tools`                                      | Recursos · Skills y tools            | tenant_admin | Una pantalla, dos pestañas                        |
| `/admin/knowledge-bases`                            | Recursos · Conocimiento              | tenant_admin |                                                   |
| `/admin/knowledge-bases/categories`                 | Sin entrada — desde arriba           | tenant_admin |                                                   |
| `/admin/documents`                                  | Recursos · Conocimiento (documentos) | tenant_admin |                                                   |
| `/admin/documents/[id]/ingestion`                   | Sin entrada — desde el documento     | tenant_admin | Detalle del indexado                              |
| `/admin/documents/[id]/citations`                   | Sin entrada — desde el documento     | tenant_admin |                                                   |
| `/admin/memories`                                   | Recursos · Memorias                  | tenant_admin | `task_ui_31`: pestañas por ámbito con procedencia |
| `/admin/marketplace`                                | Recursos · Marketplace               | tenant_admin | Catálogo · Instaladas · Publicar                  |
| `/admin/marketplace/private`                        | Sin entrada — desde Marketplace      | tenant_admin | Catálogo privado del tenant                       |
| `/admin/marketplace/installations/[id]`             | Sin entrada — desde Instaladas       | tenant_admin |                                                   |
| `/admin/marketplace/installations/[id]/permissions` | Sin entrada — consentimiento         | tenant_admin | La puerta de permisos de una instalación          |

## Trabajo — Ajustes del tenant

| Ruta                          | Sitio en el menú                            | Rol          | Notas                                      |
| ----------------------------- | ------------------------------------------- | ------------ | ------------------------------------------ |
| `/admin/guardrails`           | Ajustes · Guardrails                        | tenant_admin |                                            |
| `/admin/approval-policy`      | Ajustes · Políticas de aprobación           | tenant_admin | 13 categorías, 4 plantillas                |
| `/admin/notifications`        | Ajustes · Notificaciones                    | tenant_admin | Canales y reglas (la bandeja es otra ruta) |
| `/admin/eval-quality`         | Ajustes · Calidad y evals                   | tenant_admin |                                            |
| `/admin/tenant-stats`         | Ajustes · Estadísticas y costes             | tenant_admin |                                            |
| `/admin/settings`             | Ajustes · Ajustes del tenant                | tenant_admin |                                            |
| `/admin/settings/hourly-rate` | Sin entrada — desde Ajustes                 | tenant_admin | Coste humano facturable                    |
| `/admin/settings/memories`    | Sin entrada — desde Ajustes                 | tenant_admin | Política de memoria del tenant             |
| `/admin/office`               | Ajustes · Extras · Oficina                  | Cualquiera   | El piso 2D en vivo (ADR 0118)              |
| `/admin/leaderboard`          | Ajustes · Extras · Leaderboard              | Cualquiera   | Ranking modelo×agente (ADR 0121)           |
| `/admin/runs`                 | **Sin entrada de menú** (decisión del plan) | Cualquiera   | Se llega desde el proyecto o el dashboard  |

## Sistema (System Admin)

| Ruta                                | Sitio en el menú                 | Rol          | Notas                                                |
| ----------------------------------- | -------------------------------- | ------------ | ---------------------------------------------------- |
| `/admin/users`                      | Sistema · Tenants y usuarios     | System Admin | ADR 0047                                             |
| `/admin/invitations`                | Sistema · Invitaciones           | System Admin | Única vía de alta con el registro cerrado (ADR 0134) |
| `/admin/llm-providers`              | Sistema · Proveedores LLM        | System Admin |                                                      |
| `/admin/ollama`                     | Sistema · Ollama                 | System Admin |                                                      |
| `/admin/model-prices`               | Sistema · Precios de modelos     | System Admin |                                                      |
| `/admin/settings/platform-defaults` | Sistema · Ajustes de plataforma  | System Admin | Incluye la allowlist de egress (ADR 0165)            |
| `/admin/settings/sso`               | Sistema · SSO                    | System Admin | El backend de SSO sigue siendo per-tenant (ADR 0031) |
| `/admin/settings/sso/saml`          | Sin entrada — desde SSO          | System Admin |                                                      |
| `/admin/backup`                     | Sistema · Backup y restauración  | System Admin |                                                      |
| `/admin/backup/destinations`        | Sistema · Backup · destinos      | System Admin |                                                      |
| `/admin/backup/restore`             | Sistema · Backup · restaurar     | System Admin |                                                      |
| `/admin/marketplace/review`         | Sistema · Marketplace (revisión) | System Admin | Cola de aprobación de listings (ADR 0142 D6)         |
| `/admin/docs`                       | Sistema · Documentación          | Cualquiera   | Hoy vive en el grupo «Ayuda»                         |
| `/admin/cortex`                     | Sistema · Córtex                 | System Owner | ADR 0074                                             |
| `/admin/cortex/mind`                | Sistema · Córtex · Mente         | System Owner | ADR 0075                                             |
| `/admin/cortex/identity`            | Sistema · Córtex · Identidad     | System Owner | ADR 0074/0077                                        |

## Cabecera

| Ruta                        | Sitio                                    | Rol          | Notas                                                                                                                                                                                                                            |
| --------------------------- | ---------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/admin/assistant`          | Asistente (botón flotante)               | tenant_admin | Hoy es entrada del grupo «Trabajo»                                                                                                                                                                                               |
| `/admin/assistant/settings` | Sin entrada — desde el asistente         | tenant_admin |                                                                                                                                                                                                                                  |
| `/admin/settings/security`  | Menú de usuario · Seguridad de mi cuenta | Cualquiera   | Ajuste **personal** (MFA), no del tenant. **Decidido el 2026-09-09** por el operador e implementado en `task_ui_01`: sale del menú lateral y entra en el menú de usuario (`data-testid="user-menu-security"`). La ruta no cambió |

## Sin área (sesión y portal público)

| Ruta                        | Qué es                                          |
| --------------------------- | ----------------------------------------------- |
| `/`                         | Entrada: redirige según sesión                  |
| `/login`                    | Login (con MFA y handoff SSO)                   |
| `/select-tenant`            | Elección de tenant cuando hay varias membresías |
| `/accept-invite`            | Aceptar una invitación                          |
| `/auth/callback`            | Callback de OAuth/OIDC                          |
| `/no-access`                | Sin membresía activa                            |
| `/developers`               | Portal de desarrollador (público)               |
| `/developers/api-reference` | Referencia de la API pública v1                 |
| `/developers/sdks`          | SDKs generados del OpenAPI                      |
| `/developers/tutorials`     | Tutoriales                                      |
| `/developers/webhooks`      | Webhooks entrantes y salientes                  |

## Preguntas abiertas

Rutas cuya área **el plan no fija**, y que este documento no inventa (criterio de
cierre 6 del plan: una decisión de producto nueva va a ADR antes de inventarla).
Se anotan aquí para que las cierre la casilla que las toque:

1. ~~**`/admin/settings/security`**~~ — **decidida el 2026-09-09 por el
   operador**: al menú de usuario de la cabecera, porque es la verificación en
   dos pasos de la propia cuenta y no un ajuste del tenant. Implementada en
   `task_ui_01`; la ruta no cambió. Se deja tachada en vez de borrada para que
   quien vuelva a hacerse la pregunta encuentre la respuesta y su fecha.
2. **`/admin/plans/[id]/escalated`** — vista de escalaciones de un plan. Encaja en
   el Tablero del proyecto (contexto del plan) o en la pestaña Revisiones de la
   bandeja humana, según de dónde llegue el usuario. La deciden `task_ui_03` y
   `task_ui_11`. **Sigue abierta.**

## Referencias

- [Plan de reestructuración](../roadmap/ui-reestructuracion-2026-09-09.md) — olas, casillas y decisiones del brainstorming.
- [Convenciones de UI](../03-guides/ui-conventions.md) — navegación, tamaños de pantalla y `data-testid`.
- [RBAC](./rbac.md) — los permisos de verdad, que son los del backend.
