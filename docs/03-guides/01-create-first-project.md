---
title: Crear tu primer proyecto
audience: usuario tenant
phase: 01-dominio-minimo
updated: 2026-05-21
---

# Crear tu primer proyecto

Esta guía te lleva del estado "tenant recién creado" a un proyecto
funcional con un equipo asignado y una política de validación humana
configurada. No requiere escribir código.

> **Prerrequisitos.** Estás autenticado en el panel
> (`/admin/dashboard`) y tu sesión tiene un tenant activo. La fase de
> selección de tenant llega en Plan 02; mientras tanto el panel
> funciona con la cuenta `root@example.com` creada por el bootstrap.

## 1. Elegir una plantilla en el wizard

1. Click en **Proyectos** (sidebar) → botón **Crear proyecto**, o
   directamente `/admin/projects/new`.
2. El paso 1 del wizard muestra las **8 plantillas seedeadas**
   (Plantilla: API REST, Plantilla: Suite E2E, Plantilla: Análisis de
   datos, …). Cada tarjeta resume qué configuración trae.
3. Pulsa **Usar plantilla** en la tarjeta que más se parezca a tu
   caso. El wizard avanza al paso 2.

## 2. Personalizar el proyecto

En el paso 2 hay tres bloques:

- **Detalles**: el nombre viene prefijado quitando el `Plantilla:` —
  ajústalo si quieres y opcionalmente edita la descripción.
- **Preview**: panel lateral con el equipo asignado, la política
  humana base y el `repository_config` que la plantilla trae.
- **Acciones**: _Cambiar plantilla_ (vuelve al paso 1) o _Crear
  proyecto_.

Al crear, el proyecto se materializa en tu tenant con `status="active"`
y la configuración heredada de la plantilla.

## 3. (Opcional) Revisar el equipo asignado

1. Ve a **Equipos** (sidebar).
2. Si el proyecto ha venido con un team built-in (por ejemplo "API
   Team"), aparecerá listado. Pulsa **Ver detalle**.
3. La pantalla de detalle muestra los agentes con su badge de scope:
   - `Linked (built-in)` — referencia al catálogo plataforma.
   - `Linked (tenant)` — referencia a un agente que tú creaste.
   - `Forked` — copia local del proyecto (editable).
4. Para personalizar un agente sin afectar a otros proyectos, usa
   **Añadir miembro → modo Forked** y elige el proyecto destino.
   Los teams built-in son read-only, ver
   [ADR 0006](../05-architecture-decisions/0006-linked-vs-forked-agents.md).

## 4. Configurar validación humana

1. Sidebar → **Validación humana** (`/admin/approval-policy`).
2. Selecciona uno de los 4 presets seedeados:
   - **Sandbox** — todo automático (solo para playgrounds aislados).
   - **Desarrollo** — código y HTTP GET en auto; el resto humano.
   - **Producción** — todo humano salvo lecturas internas.
   - **Cliente Externo** — todo humano, máxima fricción.
3. La tabla de 13 categorías reacciona al preset. Para invertir una
   categoría concreta, pulsa el badge "Auto"/"Humano" — la fila se
   marca como **Override** y aparece la insignia _Cambios sin
   guardar_.
4. En el panel lateral, elige el proyecto recién creado y pulsa
   **Aplicar política**. El JSON resultante se copia en
   `projects.human_approval_policy` (es un snapshot — modificar el
   preset después no toca proyectos ya guardados).

## 5. Visualizar el plan en el tablero

1. Sidebar → **Tablero** (`/admin/board`).
2. La sección **Planes** muestra tus proyectos como tarjetas; pulsa
   la del proyecto que acabas de crear.
3. La sección **Tareas** se carga con 7 columnas:
   `Backlog → Ready → En curso → Revisión → Bloqueada → Hecho →
Cancelada`. En Plan 01 la lista llega vacía (Plan 02 introduce el
   orquestador que genera tareas).
4. Para probar el drag&drop, crea una tarea dummy con la API:
   ```bash
   curl -X POST http://localhost:8001/projects/<project_id>/tasks \
     -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"title":"Esbozar diseño","status":"backlog","priority":"high"}'
   ```
   La tarea aparece en _Backlog_. Arrástrala a otra columna; al soltar
   se llama `PUT /projects/{pid}/tasks/{tid}` con el nuevo `status` y
   la UI se actualiza optimísticamente.

## Próximos pasos

- Plan 02 introducirá la pantalla **Planes** propia y el flujo de
  aprobación de planes generados por el agente Project Manager.
- Las acciones manuales del Kanban operan sólo sobre el `status`. La
  asignación a agentes, retries, dependencias y orquestación llegan
  con LangGraph en Plan 02.

## Ver también

- [Referencia del modelo de dominio](../04-reference/domain-model.md)
- [Ver tests E2E en directo](watching-e2e-tests.md)
- [ADR 0008 — Doble Kanban](../05-architecture-decisions/0008-dual-kanban-planes-tareas.md)
