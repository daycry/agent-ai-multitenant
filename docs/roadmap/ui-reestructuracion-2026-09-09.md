---
plan_id: ui-reestructuracion-2026-09-09
title: Reestructuración de la UI del panel — el proyecto como hub, el Sistema aparte, y recursos con procedencia
status: approved
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 5 semanas
estimated_effort_person_days: 24
created_by: claude-fable-5-1-brainstorming-2026-09-09
docs_language: es
priority: P1
source_audit: sesión de brainstorming con el operador el 2026-09-09 (decisiones en §Decisiones ya tomadas) sobre el panel tal como está en la rama plan/marketplace-mcp-2026-09-02
---

# Reestructuración de la UI del panel (2026-09-09)

## Cabecera

| Campo             | Valor                                                                                                                                |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| **ID del Plan**   | `ui-reestructuracion-2026-09-09`                                                                                                     |
| **Prioridad**     | P1 (olas 1-4) · P3 (ola 5, piel, opcional)                                                                                           |
| **Bloqueado por** | Ninguno formal. Empieza cuando `remediacion-marketplace-mcp-2026-09-02` deje `in_progress` (el protocolo admite un solo plan activo) |
| **Rama sugerida** | `plan/ui-reestructuracion-2026-09-09`, sobre `master` una vez mergeado el PR #183 (la ola 3 reutiliza lo que ese PR entrega)         |
| **Método**        | Una ola = un PR desplegable. Vitest por pantalla, Playwright (subset mockeado) por flujo, `next build` antes de dar la ola por buena |
| **Origen**        | Petición del operador: «la UI no está bien estructurada, parece un pegote; que sea más friendly, mejor organizada y profesional»     |

## Resumen

El panel tiene 34 rutas planas repartidas en seis grupos de menú y un dashboard que
enseña la salud de los servicios y tres contadores globales. Todo cuelga del
**tenant** y del **menú**; nada cuelga del **proyecto**, que es donde ocurre el
trabajo (planes, tareas, equipo, MCP, conocimiento, memorias, runs, costes). Los
síntomas que el operador describe salen de ahí: un tablero de planes que no dice
de qué proyecto es cada plan; un dashboard que responde «¿está vivo el stack?» en
vez de «¿qué me espera hoy?»; memorias en una lista plana sin ámbito ni
procedencia; dos caminos para el mismo servidor MCP (declararlo en el proyecto o
instalarlo del marketplace y desplegarlo) que parecen cosas distintas; y un
Conocimiento que los usuarios no saben manejar.

Este plan reorganiza el panel en **tres niveles** —tenant, proyecto, sistema—
con el **proyecto como hub**, saca el área de Sistema del menú del tenant (ADR
0117 c), da un patrón común a los recursos del tenant y convierte Conocimiento en
un flujo guiado. **Las rutas actuales se conservan**: cambia dónde se enseñan y
qué barra las envuelve, no las URL, para no romper enlaces ni los e2e.

## Decisiones ya tomadas (brainstorming del 2026-09-09)

1. **Centro de gravedad: el proyecto**, con el área de Sistema separada para el
   System Admin (opción «A con la separación de C»).
2. **Enfoque 2** de los tres propuestos: reestructurar la arquitectura de
   información; la **piel** (tipografía, paleta, densidad) es la **ola 5,
   posterior y opcional**.
3. **Dashboard del tenant**, en este orden: (1) lo que espera a un humano;
   (2) trabajo en curso por proyecto; (3) dinero contra presupuesto; (4) salud
   **sólo cuando duele**; más (5) actividad reciente de los agentes y (6) estado
   de los MCP. Sale de **un endpoint agregado** con caché corta.
4. **Bandeja humana unificada**: aprobaciones, `ask_human`, revisiones humanas de
   plan y notificaciones en una pantalla con pestañas y contador.
5. **Tablero dentro del proyecto** (doble Kanban del principio 6) y un
   **portfolio** de planes a nivel tenant con la columna Proyecto siempre visible.
6. **Capacidades del proyecto en una sola pantalla** (MCP · Tools · Skills) con
   procedencia (declarado a mano / desplegado desde el marketplace), conexión,
   tools importadas y estado de egress. Los despliegues del marketplace
   desembocan aquí: deja de haber dos caminos que parecen distintos.
7. **Memorias por ámbito** (privada, equipo, proyecto, global) **con procedencia**
   (quién la creó —persona o agente, con su run—, cuándo, en qué proyecto o
   equipo, categoría, veces recuperada). Las privadas sólo las ve su dueño.
8. **Conocimiento como flujo**: «¿Qué sabe este proyecto?» y un asistente de tres
   pasos (elegir o crear base → subir documentos → ver el indexado en vivo), con
   la concesión al proyecto implícita.
9. **Patrón común de pantalla de recurso** para agentes, skills y tools,
   marketplace y conocimiento; nombres en vez de UUID.

## Arquitectura de información

### Cabecera (siempre visible)

Logo · selector de tenant (el actual) · selector de **área**: «Trabajo» y, sólo
para System Admin, «Sistema» · a la derecha idioma, usuario, un **indicador de
salud que sólo aparece si algo está degradado** (enlaza al detalle en Sistema) y
el **asistente** como botón flotante.

### Área Trabajo (tenant) — barra lateral en cuatro grupos

- **Inicio**: Dashboard · Bandeja humana.
- **Proyectos**: portfolio de proyectos. Al entrar en uno, la barra lateral
  **cambia** a la navegación del proyecto con un enlace «← Proyectos».
- **Recursos del tenant**: Agentes · Equipos · Agentes humanos · Skills y tools
  (una pantalla, dos pestañas) · Conocimiento (bases y documentos) · Memorias ·
  Marketplace.
- **Ajustes del tenant**: Guardrails · Políticas de aprobación · Notificaciones ·
  Calidad y evals · Estadísticas y costes · Ajustes · Extras (Oficina,
  Leaderboard).

Runs globales dejan de ser entrada de menú: se consultan desde el proyecto o el
dashboard (la ruta `/admin/runs` sigue existiendo).

### Área Proyecto — barra lateral contextual

Resumen · Tablero · Runs · Equipo · Capacidades · Conocimiento · Costes ·
Ajustes. Las fichas actuales (`/mcp-servers`, `/commands`, `/kb`, `/plans`…)
pasan a ser esas pestañas conservando su ruta.

### Área Sistema (System Admin) — barra lateral propia

Salud de servicios · Tenants y usuarios · Invitaciones · Proveedores LLM · Ollama
· Precios de modelos · Ajustes de plataforma (incluye Egress) · SSO · Backup y
restauración · Marketplace (revisión y catálogo global) · Córtex (sólo Owner) ·
Documentación. Un enlace a una ruta de Sistema desde Trabajo abre el área Sistema.

## Endpoints nuevos (api-server)

| Endpoint                                    | Para qué                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `GET /tenant/dashboard`                     | Las seis secciones del dashboard en una respuesta: pendientes humanos (contadores por cola), trabajo por proyecto (planes activos con progreso, runs en curso, último fallo), gasto vs presupuesto por proyecto, salud degradada (sólo si la hay), actividad reciente de agentes, estado MCP por proyecto. Mismas consultas que las pantallas de origen, bajo RLS; caché Redis 30 s por tenant |
| `GET /projects/{id}/overview`               | El Resumen del proyecto: lo mismo acotado a un proyecto, más sus avisos (MCP sin conectar o sin tools, egress bloqueado, repo sin sincronizar, rol sin agente)                                                                                                                                                                                                                                 |
| `GET /projects/{id}/capabilities`           | MCP (con procedencia, conexión, tools importadas, egress), tools y skills desplegadas en el proyecto, unificando `mcp_servers`, `mcp_server_warnings`, despliegues del marketplace y el catálogo                                                                                                                                                                                               |
| `GET /memories` (ampliado)                  | Filtros por ámbito, proyecto, agente, categoría y fecha; cada fila con `created_by` (persona o agente), `execution_id`, `project_id`/`team_id`, `recall_count`. Lo que el dominio no tenga registrado sale `null` y la UI dice «desconocida»                                                                                                                                                   |
| `GET /marketplace/installations` (ampliado) | `deployments: [{project_id, project_name}]` y el nombre del listing, para «dónde está desplegado» sin UUID                                                                                                                                                                                                                                                                                     |

Todo endpoint nuevo lleva test de integración con caso cross-tenant.

## Criterios de cierre del plan

1. Todas las casillas `[x]` con su test en verde (vitest, Playwright del subset, integración de los endpoints nuevos).
2. `check-i18n`, `check-component-size`, `tsc`, eslint y `next build` en verde en cada ola.
3. Ninguna ruta anterior devuelve 404: `tests/unit` gana una guarda que compara las rutas de `app/admin/**/page.tsx` antes y después.
4. Tests humanos `human_ui_01..04` validados.
5. Entrada en `docs/07-changelog/ui-reestructuracion-2026-09-09.md` y `docs/04-reference/` actualizado (mapa de pantallas).
6. Toda decisión de producto nueva que aparezca al implementar (un dato que el dominio no tiene, un permiso que no existe) se lleva a un ADR antes de inventarla.

---

## Ola 1 — Cimientos: áreas, dashboard y bandeja (P1 · ~6 d)

### `task_ui_01` — Selector de área y las dos barras laterales

- [ ] **Título**: `components/layout/admin-shell.tsx` (el fichero más acoplado del panel) se parte en
      `area-switcher.tsx`, `sidebar-work.tsx`, `sidebar-system.tsx` y `sidebar-project.tsx`; el shell elige
      la barra por ruta y por rol (`navGroupVisible` se conserva). El área Sistema sólo aparece para System
      Admin; entrar en una ruta de Sistema desde Trabajo cambia el área. El indicador de salud degradada en
      cabecera consulta `/admin/system-health` sólo para System Admin y sólo pinta si hay algo en rojo.
      **Test**: vitest de `visibleNavGroups` por rol y área; vitest del shell (tres barras, cambio por ruta);
      e2e `sidebar-complete.spec.ts` actualizado por `data-testid`; guarda de rutas conservadas.
      **Coste**: 2 d.

### `task_ui_02` — `GET /tenant/dashboard` y el dashboard tenant-céntrico

- [ ] **Título**: endpoint agregado (`routers/dashboard.py`, `require_tenant_member`, caché Redis 30 s por
      tenant, mismas consultas que aprobaciones, inbox, planes, runs, costes, agentes y `mcp_server_warnings`);
      `app/admin/dashboard/page.tsx` se reescribe con las seis secciones en el orden acordado, cada una con
      enlace a su destino, y la salud sólo cuando duele. Los tres contadores globales desaparecen.
      **Test**: integración del endpoint (cifras coherentes con las pantallas de origen; cross-tenant); vitest
      del dashboard con las seis secciones y con el caso «nada pendiente»; e2e del dashboard.
      **Coste**: 2 d.

### `task_ui_03` — Bandeja humana unificada

- [ ] **Título**: `/admin/inbox` pasa a tener pestañas Aprobaciones · Preguntas (`ask_human`) · Revisiones ·
      Notificaciones con contador por pestaña, reutilizando los endpoints existentes; `/admin/approvals`,
      `/admin/human-queue` y `/admin/notifications/inbox` siguen respondiendo (redirigen a la pestaña).
      **Test**: vitest de las cuatro pestañas y contadores; e2e de aprobar desde la bandeja.
      **Coste**: 1,5 d.

### `task_ui_04` — Guarda de rutas y mapa de pantallas

- [ ] **Título**: `tests/unit/test_admin_panel_routes_preserved.py` fija el conjunto de rutas de
      `app/admin/**/page.tsx` al arrancar el plan y falla si alguna desaparece; `docs/04-reference/` gana el
      mapa de pantallas por área (qué ruta, en qué área, para qué rol).
      **Test**: la propia guarda; docs guard.
      **Coste**: 0,5 d.

## Ola 2 — El proyecto como hub (P1 · ~7 d)

### `task_ui_10` — Barra contextual del proyecto y Resumen

- [ ] **Título**: `sidebar-project.tsx` con las ocho entradas; `GET /projects/{id}/overview` y la página de
      Resumen con planes activos, runs, gasto, avisos (MCP, egress, repo, roles sin agente) y actividad de
      agentes, cada bloque enlazando a su pestaña.
      **Test**: integración del overview (cross-tenant; avisos calculados); vitest del Resumen; e2e de
      navegación entre pestañas del proyecto.
      **Coste**: 2 d.

### `task_ui_11` — Tablero dentro del proyecto y portfolio del tenant

- [ ] **Título**: `/admin/projects/{id}/board` con el doble Kanban (planes → tareas) acotado al proyecto;
      `/admin/board` pasa a ser el **portfolio**: lista de planes de todos los proyectos con la columna
      Proyecto siempre visible, filtros por estado, proyecto y responsable, y enlace al tablero del proyecto.
      **Test**: vitest del tablero de proyecto y del portfolio; los e2e del tablero global se rehacen como
      portfolio (sin `test.skip`).
      **Coste**: 2,5 d.

### `task_ui_12` — Runs, Equipo y Costes del proyecto

- [ ] **Título**: pestaña Runs (filtros por plan, agente y estado, reutilizando `/executions`); pestaña Equipo
      (equipo asignado, agentes por rol, agentes humanos, huecos de rol con enlace a la ficha del agente);
      pestaña Costes (gasto por plan, agente y modelo contra presupuesto; pausa por presupuesto).
      **Test**: vitest de las tres pestañas; e2e de filtrar runs por plan.
      **Coste**: 2,5 d.

## Ola 3 — Capacidades y Conocimiento (P1 · ~5 d)

### `task_ui_20` — Capacidades del proyecto en una pantalla

- [ ] **Título**: `GET /projects/{id}/capabilities` y la pestaña Capacidades con MCP · Tools · Skills. En MCP,
      cada servidor lleva procedencia (declarado / desplegado desde el listing X vY), conexión (probado,
      OAuth conectado), «N tools importadas» / «sin importar» con Probar e Importar directos (lo que
      `task_mk_01` ya entrega en la tarjeta) y estado de egress (aviso D11 de `task_mk_02`). La sección
      «Capacidades disponibles» del marketplace (ADR 0142) se funde aquí.
      **Test**: integración del endpoint; vitest de las tres pestañas y de la procedencia; e2e de desplegar
      desde el marketplace y verlo aparecer en Capacidades.
      **Coste**: 2,5 d.

### `task_ui_21` — Conocimiento como flujo guiado

- [ ] **Título**: pestaña Conocimiento del proyecto con las bases concedidas como tarjetas (nombre, categoría,
      documentos, estado de indexado, última actualización) y el asistente «Añadir conocimiento» de tres pasos
      (elegir o crear base → subir documentos o pegar texto/URL → indexado en vivo con reintento), con la
      concesión al proyecto implícita; a nivel tenant, la misma pantalla con «concedida a» por base y conceder
      o retirar desde la tarjeta; los documentos viven dentro de su base. Vocabulario: «base de conocimiento» y
      «documento»; los ids y los términos de ingesta sólo en el detalle de un fallo.
      **Test**: vitest del asistente (tres pasos, fallo con reintento); e2e de añadir conocimiento a un proyecto.
      **Coste**: 2,5 d.

## Ola 4 — Recursos del tenant con patrón común (P1 · ~5 d)

### `task_ui_30` — Patrón de pantalla de recurso

- [ ] **Título**: `components/resource/` con cabecera (título, contador, acción principal), barra de filtros y
      búsqueda, lista/tarjetas con campos en el mismo sitio (nombre, procedencia, ámbito, uso, última
      actividad) y panel lateral de detalle; se aplica a Agentes, Equipos, Agentes humanos y Skills y tools.
      **Test**: vitest del patrón y de cada pantalla migrada.
      **Coste**: 1,5 d.

### `task_ui_31` — Memorias por ámbito con procedencia

- [ ] **Título**: `GET /memories` ampliado (ámbito, proyecto, agente, categoría, fecha, `created_by`,
      `execution_id`, `recall_count`); `/admin/memories` con pestañas por ámbito y contador, cada memoria con
      quién, cuándo, dónde y cuántas veces se recuperó; buscador semántico. Las privadas sólo para su dueño
      (principio de ámbitos). Lo que una memoria antigua no tenga registrado se enseña como «desconocida»; si
      el dominio no guarda la procedencia, esta casilla propone la migración en un ADR antes de inventarla.
      **Test**: integración del endpoint (cross-tenant y ámbito privado); vitest de las pestañas y la ficha.
      **Coste**: 2 d.

### `task_ui_32` — Agentes por pestañas y marketplace con «dónde está desplegado»

- [ ] **Título**: la ficha del agente agrupa en pestañas Identidad y modelo · Skills y tools · Conocimiento ·
      Memorias · Actividad; el marketplace queda en Catálogo · Instaladas · Publicar, con nombres en vez de
      UUID, la capacidad de cada instalación (`task_mk_10`), sus despliegues por proyecto (endpoint ampliado)
      y «Desplegar en un proyecto» que lleva a Capacidades.
      **Test**: vitest de la ficha por pestañas y de Instaladas; e2e de la cola de revisión enlazada.
      **Coste**: 1,5 d.

## Ola 5 — Piel (P3 · opcional · ~3 d)

### `task_ui_40` — Sistema visual

- [ ] **Título**: tipografía, densidad, paleta semántica, estados vacíos y microcopys, con la skill de diseño
      del proyecto y sin tocar la estructura de las olas 1-4. Sólo si el operador la aprueba tras ver las
      cuatro anteriores.
      **Test**: vitest de regresión de los `data-testid`; `next build`.
      **Coste**: 3 d.

---

## Tests humanos

| ID            | Qué valida                                                                                                                        |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `human_ui_01` | Un tenant admin entra, ve en el dashboard lo que le espera y llega en un clic a la aprobación pendiente y al plan que falla       |
| `human_ui_02` | Desde un proyecto recorre Resumen → Tablero → tarea → run sin salir del proyecto, y el portfolio dice a qué proyecto es cada plan |
| `human_ui_03` | Añade conocimiento a un proyecto con el asistente en menos de dos minutos sin leer documentación                                  |
| `human_ui_04` | Un System Admin ve el área Sistema separada y un tenant admin no ve nada de ella                                                  |

## Riesgos

- **`admin-shell.tsx`** es el fichero más acoplado del panel; se parte en la ola 1 con su guarda de rutas.
- **Los e2e del tablero global** se rehacen como portfolio; ninguno se salta con `test.skip`.
- **Memorias antiguas sin procedencia**: se enseña «desconocida», no se inventa; si falta la columna, ADR.
- **Dos planes con alcance vecino**: `task_mk_11/13/22/23` del marketplace tocan pantallas que la ola 3 y 4
  reorganizan. Regla: el plan del marketplace entrega el **dato** (avisos, procedencia, nombres); éste
  entrega el **sitio** donde se ve. Si una casilla del marketplace se implementa después de la ola
  correspondiente, se implementa ya en la pantalla nueva.
- **Memoria de esta máquina de desarrollo**: el stack completo no cabe (16 GB); las pruebas en vivo de la UI
  se hacen con el stack de dev acotado (`docs/context/memoria-del-asistente.md` y la memoria de sesión).
