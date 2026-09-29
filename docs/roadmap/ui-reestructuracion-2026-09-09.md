---
plan_id: ui-reestructuracion-2026-09-09
title: Reestructuración de la UI del panel — el proyecto como hub, el Sistema aparte, y recursos con procedencia
status: in_progress
blocking_plan: []
started_at: 2026-09-09
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
| `GET /projects/{id}/team`                   | Equipo del proyecto: miembros por rol, agentes humanos y huecos de rol (`role_gaps`, la misma regla que el aviso del Resumen); cualquier miembro (`task_ui_12`)                                                                                                                                                                                                                                |
| `GET /projects/{id}/costs`                  | Gasto de IA del proyecto por plan, agente y modelo contra su presupuesto, con la pausa; sólo `tenant_admin` (`task_ui_12`)                                                                                                                                                                                                                                                                     |
| `GET /runs?project_id=` (filtro nuevo)      | El listado de runs acotado a un proyecto; también en `tenant_stats` (`task_ui_12`)                                                                                                                                                                                                                                                                                                             |
| `GET /memories/counts` y `/memories/search` | Contador por ámbito y buscador híbrido con la misma regla de privadas que el listado (`task_ui_31`)                                                                                                                                                                                                                                                                                            |

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

- [x] **Título**: `components/layout/admin-shell.tsx` (el fichero más acoplado del panel) se parte en
      `area-switcher.tsx`, `sidebar-work.tsx`, `sidebar-system.tsx` y `sidebar-project.tsx`; el shell elige
      la barra por ruta y por rol (`navGroupVisible` se conserva). El área Sistema sólo aparece para System
      Admin; entrar en una ruta de Sistema desde Trabajo cambia el área. El indicador de salud degradada en
      cabecera consulta `/admin/system-health` sólo para System Admin y sólo pinta si hay algo en rojo.
      **Test**: vitest de `visibleNavGroups` por rol y área; vitest del shell (tres barras, cambio por ruta);
      e2e `sidebar-complete.spec.ts` actualizado por `data-testid`; guarda de rutas conservadas.
      **Coste**: 2 d.
      **Entregada el 2026-09-09.** `admin-shell.tsx` pasa de **522 a 190 líneas** y reparte lo que hacía:
      `nav-model.ts` (tipos, gating por rol y las tres áreas — puro, sin React, para poder probar la
      decisión en entorno `node`), `sidebar-work.tsx` (cuatro grupos), `sidebar-system.tsx` (Plataforma,
      Córtex y la documentación), `sidebar-project.tsx` (once pestañas + «← Proyectos»),
      `sidebar-frame.tsx` (marca y el `<nav data-testid="sidebar-nav">` común, ahora con `data-area`),
      `nav-group-block.tsx` (grupo colapsable y enlace), `area-switcher.tsx` y
      `system-health-indicator.tsx`. El shell **re-exporta** el modelo: cuatro ficheros de test importaban
      de él y mover su ruta de import no era el objetivo.
      **Cinco decisiones que el enunciado no fijaba**, tomadas y escritas donde se buscan:
      (1) `/admin/settings/security` al menú de usuario —decisión del operador del 2026-09-09; es la MFA de
      la propia cuenta, no del tenant—; (2) `/admin/docs` se queda en Trabajo **y** en Sistema, porque el
      área Sistema es sólo del System Admin y moverla del todo retiraría la documentación a todos los demás
      (`SHARED_ROUTES`: no fuerza área, así que entrar en ella no te echa de donde estabas);
      (3) `/admin/runs` **sigue** en el menú hasta `task_ui_02` —el plan lo retira, pero la pestaña Runs del
      proyecto es de `task_ui_12` y quitarlo ya dejaría la ruta viva y sin camino media ola—;
      (4) la barra del proyecto sólo ofrece rutas que existen hoy (Tablero, Equipo y Costes los abren
      `task_ui_11` y `task_ui_12`: un enlace a una ruta que nadie sirve es un 404 con aspecto de producto);
      (5) `areaForPath` devuelve `null` para las rutas compartidas, y el emparejamiento es por SEGMENTO
      —`/admin/settings` es del tenant y `/admin/settings/sso` de plataforma; un `startsWith` se llevaría
      por delante los ajustes del tenant enteros—.
      **Un defecto que encontró un test propio**: quien pega `/admin/users` sin ser System Admin recibirá un
      403 del backend, pero de camino se quedaba con la barra lateral **en blanco**; ahora cae a Trabajo.
      **Medido**: vitest **1646 en 184 ficheros** (36 nuevos: `nav-model` 15, `admin-shell-areas` 6,
      `system-health-indicator` 7, `admin-shell-nav-groups` +2, más el ajuste del header), `tsc --noEmit`
      limpio, `check-i18n` y `check-component-size` sin avisos, **`next build` completo (65 páginas)**, y las
      dos guardas de `task_ui_04` en verde: ninguna ruta se perdió. E2E: `sidebar-complete.spec.ts` gana
      cinco casos (áreas, proyecto, portfolio y la seguridad en el menú de usuario) — pendientes de correr
      con navegador, como el resto del subset.
      **Excepción declarada**: `nav.projectChat` entra en la lista de «igual en los dos idiomas» de
      `i18n.test.ts` («Chat»).

### `task_ui_02` — `GET /tenant/dashboard` y el dashboard tenant-céntrico

- [x] **Título**: endpoint agregado (`routers/dashboard.py`, `require_tenant_member`, caché Redis 30 s por
      tenant, mismas consultas que aprobaciones, inbox, planes, runs, costes, agentes y `mcp_server_warnings`);
      `app/admin/dashboard/page.tsx` se reescribe con las seis secciones en el orden acordado, cada una con
      enlace a su destino, y la salud sólo cuando duele. Los tres contadores globales desaparecen.
      **Test**: integración del endpoint (cifras coherentes con las pantallas de origen; cross-tenant); vitest
      del dashboard con las seis secciones y con el caso «nada pendiente»; e2e del dashboard.
      **Coste**: 2 d.
  - ✅ **Cerrada el 2026-09-29 con su e2e.** `e2e/tenant-dashboard.spec.ts` (subset mockeado, como 103 de los
    115 specs del panel): la ruta real `/admin/dashboard` monta las secciones, la salud sin permiso no rompe la
    página y los pendientes enlazan a su pantalla. `1 passed` con el panel en el puerto 3100, porque el 3000 lo
    ocupaba otro proyecto. `e2e/admin-login.spec.ts` pasa a exigir `dashboard-grid`, pero necesita backend real y
    no se ha ejecutado.
  - ⏳ **Implementada el 2026-09-29, casilla abierta.** La revisión adversarial (lente A) recordó que
    su e2e declarado no se ha ejecutado, y CLAUDE.md prohíbe marcar `[x]` una tarea cuyo test automático no
    ha pasado. Se marca cuando el e2e corra contra el stack con esta versión. Lo hecho: backend: la lógica
    vive en `api_server/dashboard/tenant.py` y el router
    es fino. Los pendientes salen de `collect_human_queue`, extraída de `/human-queue` para que las dos cifras
    no puedan divergir; el gasto, de `tenant_budget_summary`; los avisos de MCP, de
    `egress_warnings_for_servers`. Panel: `lib/dashboard/tenant-dashboard.ts` (tipos y cálculos puros) y cinco
    piezas en `components/dashboard/`; la salud del stack sólo se pinta si algo no está `ok` y si el usuario
    puede leer `/admin/system-health`. Tests: `tests/integration/test_tenant_dashboard.py` 8 passed, con
    cross-tenant, y `test_human_queue.py` sigue verde; vitest 7 passed. **No vistos en rojo antes del
    código**: la BD de tests estaba ocupada por otra tanda. Se comprobó que muerden con mutaciones: quitar el
    filtro de «proyectos en curso» pone rojo su test, y pintar la salud siempre pone rojo el caso «nada
    pendiente». El e2e (`e2e/admin-login.spec.ts`, ahora contra `dashboard-grid`) necesita el stack completo
    y no se ejecutó.
  - **Revisión adversarial, intento 1 (2026-09-29)**, skill `adversarial-review` del plugin, lentes A+B+C+D
    con el agente `reviewer`; correcciones con el agente `implementer`. Gaps de esta tarea y su estado:

    | #   | Grado     | Lente | Gap                                                                                                      | Estado                                                         |
    | --- | --------- | ----- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
    | 1   | Important | D     | `_projects` recorría todas las tareas del tenant por proyecto (subconsulta por el `project_id` del plan) | corregido: agregados con GROUP BY por `t.project_id`           |
    | 2   | Important | D     | `_activity` ordenaba por `coalesce(...)`: sin índice ni poda de particiones                              | corregido: `created_at DESC`                                   |
    | 3   | Important | D     | el gasto (3 consultas por proyecto con presupuesto) en la ruta de polling de 30 s                        | corregido: caché propia de 300 s                               |
    | 4   | Important | A     | un System Admin sin tenant activo veía el bloque de error                                                | corregido: aviso con enlace a `/select-tenant`                 |
    | 5   | Important | A     | casilla `[x]` con el e2e sin ejecutar                                                                    | casilla reabierta                                              |
    | 6   | Minor     | C     | gasto y pausas visibles a un `tenant_member` (CWE-863)                                                   | corregido: recorte por rol tras la caché                       |
    | 7   | Minor     | A     | faltaba «último fallo» por proyecto                                                                      | corregido: `last_failure` con enlace                           |
    | 8   | Minor     | A     | la tarjeta de gasto no enlazaba                                                                          | corregido: enlace a `/admin/tenant-stats`                      |
    | 9   | Minor     | D     | la caché no tiene single-flight: N reconstrucciones a la vez al caducar                                  | deuda aceptada: con 1-3 corregidos la reconstrucción es barata |

    Verificado por el orquestador: integración 16 passed (con cross-tenant y los dos roles), vitest 11 passed.

  - **Revisión adversarial, intento 2 (2026-09-29).** Las nueve correcciones cierran. Dos gaps nuevos quedan
    como **deuda aceptada**, con su porqué: (N3) la actividad ordena por creación, así que un run antiguo que
    falla ahora no sube al top 10 — es el precio de usar el índice `(tenant_id, created_at)` y la poda de
    particiones en la tabla más grande del sistema; (N4) el gasto se cachea 300 s y el resto 30 s, así que
    durante hasta cinco minutos una pausa por presupuesto puede verse antes que el gasto que la causó. Sustituye
    a la «caché Redis 30 s» del enunciado para esa sección.

### `task_ui_03` — Bandeja humana unificada

- [x] **Título**: `/admin/inbox` pasa a tener pestañas Aprobaciones · Preguntas (`ask_human`) · Revisiones ·
      Notificaciones con contador por pestaña, reutilizando los endpoints existentes; `/admin/approvals`,
      `/admin/human-queue` y `/admin/notifications/inbox` siguen respondiendo (redirigen a la pestaña).
      **Test**: vitest de las cuatro pestañas y contadores; e2e de aprobar desde la bandeja.
      **Coste**: 1,5 d.
  - ✅ **Cerrada el 2026-09-29**, con el agente `implementer` del plugin. `/admin/inbox` es un hub con cinco
    pestañas: Aprobaciones · Preguntas · Revisiones · Notificaciones y «Asignadas a mí». La última es la bandeja
    de tareas humanas de antes, movida a `assignments-panel.tsx`: ya existía y retirarla dejaba sin camino a los
    agentes humanos. Las piezas viven en `components/inbox/`. `/admin/approvals` y `/admin/human-queue`
    redirigen a `?tab=approvals` y `/admin/notifications/inbox` a `?tab=notifications`; la guarda de rutas
    conservadas sigue verde. Verificado por el orquestador: vitest completo **1698 en 190 ficheros**, `tsc`,
    eslint, `check-i18n` y `check-component-size` en verde. E2E contra `next dev` con el subset mockeado:
    `inbox-approve.spec.ts` (aprobar hace POST y vacía la pestaña; la redirección de `/admin/approvals`) y
    los tres `human-*.spec.ts` rehechos, **en verde**. Excepción i18n declarada: `inboxHub.age*` (min, h y d
    son símbolos).

### `task_ui_04` — Guarda de rutas y mapa de pantallas

- [x] **Título**: `tests/unit/test_admin_panel_routes_preserved.py` fija el conjunto de rutas de
      `app/admin/**/page.tsx` al arrancar el plan y falla si alguna desaparece; `docs/04-reference/` gana el
      mapa de pantallas por área (qué ruta, en qué área, para qué rol).
      **Test**: la propia guarda; docs guard.
      **Coste**: 0,5 d.
      **Entregada el 2026-09-09, y PRIMERA de la ola a propósito**: el plan la lista al final, pero una
      guarda de conservación capturada después de mover pantallas fija el resultado —incluido lo que se
      perdiera por el camino—, así que su valor depende de tomarse antes de `task_ui_01`. Entregado:
      `tests/unit/test_admin_panel_routes_preserved.py` con la foto de las **81 rutas** del 2026-09-09
      (generada con su propio descubrimiento, no a mano) y el autotest que demuestra que la comparación
      muerde; y `docs/04-reference/mapa-de-pantallas-del-panel.md` con las 81 filas (área + rol sacados de
      `NAV_GROUPS`, no inventados) más su docs guard
      `tests/docs/test_panel_screen_map.py`, que comprueba las **dos** direcciones: ruta sin fila y fila sin
      ruta. Verificado quitando `/admin/office` del árbol (rojo con esa ruta), quitando su fila del mapa
      (rojo) y añadiendo una fila inventada (rojo). El mapa deja **dos preguntas abiertas** que el plan no
      fija y que no se inventan aquí (criterio de cierre 6): el área de `/admin/settings/security` —ajuste
      personal, no del tenant— la decide `task_ui_01`, y la de `/admin/plans/[id]/escalated`, `task_ui_03`
      con `task_ui_11`.

## Ola 2 — El proyecto como hub (P1 · ~7 d)

### `task_ui_10` — Barra contextual del proyecto y Resumen

- [ ] **Título**: `sidebar-project.tsx` con las ocho entradas; `GET /projects/{id}/overview` y la página de
      Resumen con planes activos, runs, gasto, avisos (MCP, egress, repo, roles sin agente) y actividad de
      agentes, cada bloque enlazando a su pestaña.
      **Test**: integración del overview (cross-tenant; avisos calculados); vitest del Resumen; e2e de
      navegación entre pestañas del proyecto.
      **Coste**: 2 d.
  - ⏳ **Implementada el 2026-09-29, casilla abierta por un aviso.** `GET /projects/{id}/overview`
    (`dashboard/project.py` más un router fino) con planes activos, runs, gasto y actividad, y cinco avisos
    calculados: MCP sin conectar, MCP sin tools, egress bloqueado, repo sin sincronizar y pausa por
    presupuesto. La barra del proyecto tiene las ocho entradas; Runs, Equipo y Costes salen deshabilitadas
    hasta `task_ui_12`. Integración **8 passed**, con cross-tenant y los dos roles (verificado por el
    orquestador); e2e `project-hub-tabs.spec.ts` **4 passed**. Al correrlo se encontraron dos defectos del
    propio spec: el comodín de rutas registrado el último tapaba `/overview`, y un `[]` para
    `/capabilities` rompía la ficha. **Falta el aviso «rol sin agente»**. El implementador lo omitió porque
    creyó que el dominio no guarda roles requeridos, pero sí los guarda: cada tarea del `specification` de
    un plan lleva su `role` (ADR 0091), y `team_role_agents` dice qué roles cubre el equipo. Lo añade
    `task_ui_12`, que necesita el mismo cálculo para los huecos de rol de la pestaña Equipo.
  - **Actualizado el 2026-09-29.** El aviso `role_without_agent` ya existe (entregado con `task_ui_12`, una
    sola definición en `dashboard/project_team.role_gaps`), y Runs, Equipo y Costes ya tienen pestaña. La casilla
    sigue abierta por la revisión adversarial de las olas 2 y 3 (ver §«Revisión adversarial de las olas 2 y 3»).

### `task_ui_11` — Tablero dentro del proyecto y portfolio del tenant

- [x] **Título**: `/admin/projects/{id}/board` con el doble Kanban (planes → tareas) acotado al proyecto;
      `/admin/board` pasa a ser el **portfolio**: lista de planes de todos los proyectos con la columna
      Proyecto siempre visible, filtros por estado, proyecto y responsable, y enlace al tablero del proyecto.
      **Test**: vitest del tablero de proyecto y del portfolio; los e2e del tablero global se rehacen como
      portfolio (sin `test.skip`).
      **Coste**: 2,5 d.
  - ✅ **Cerrada el 2026-09-29**, con el agente `implementer`. `components/board/plan-kanban.tsx` (movido con
    `git mv`) monta el doble Kanban en `/admin/projects/[id]/board`, acotado al proyecto. `/admin/board` es el
    portfolio: planes de todos los proyectos con la columna Proyecto, filtros por estado, proyecto y
    responsable, y enlace al tablero de cada proyecto. `docs/04-reference/mapa-de-pantallas-del-panel.md` gana
    la fila de la ruta nueva. E2E `dual-kanban`, `kanban-live` y `kanban-realtime` rehechos como portfolio y
    tablero de proyecto, sin `test.skip`, **en verde** junto al resto del bloque (30 de 30). Excepción i18n
    declarada: `portfolio.colPlan` y `portfolio.countOne` («Plan» se escribe igual en los dos idiomas).

### `task_ui_12` — Runs, Equipo y Costes del proyecto

- [ ] **Título**: pestaña Runs (filtros por plan, agente y estado, reutilizando `/executions`); pestaña Equipo
      (equipo asignado, agentes por rol, agentes humanos, huecos de rol con enlace a la ficha del agente);
      pestaña Costes (gasto por plan, agente y modelo contra presupuesto; pausa por presupuesto).
      **Test**: vitest de las tres pestañas; e2e de filtrar runs por plan.
      **Coste**: 2,5 d.
  - ⏳ **Implementada el 2026-09-29, casilla abierta hasta cerrar la revisión adversarial.** `GET /projects/{id}/team`
    (miembro) y `/costs` (sólo `tenant_admin`), filtro `project_id` en `/runs` y en `tenant_stats`, y las tres
    pestañas. El hueco de rol tiene una sola definición (`dashboard/project_team.role_gaps`), compartida con el
    aviso del Resumen. **Queda una incoherencia registrada** (revisión, intento 2): Equipo enseña a los miembros
    built-in, pero `team_role_agents` (el dispatch del ADR 0091) no los cuenta, así que un built-in miembro no tapa
    su hueco. Cambiarlo cambia a quién asigna el dispatch, así que **decide el operador**. Gasto humano: no se
    desglosa por proyecto (`compute_human_cost_usd` va por tenant o plan), y Costes enseña sólo el de IA.

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
  - ⏳ **Implementada el 2026-09-29, casilla abierta hasta cerrar la revisión adversarial.** `GET /projects/{id}/capabilities`
    ampliado de forma aditiva (procedencia, OAuth con tres estados, tools importadas y egress) y la pestaña
    `/capabilities` con MCP · Tools · Skills. «Capacidades disponibles» se funde en ella. Lo no persistido está
    en §«Revisión adversarial de las olas 2 y 3» (gap 17).

### `task_ui_21` — Conocimiento como flujo guiado

- [ ] **Título**: pestaña Conocimiento del proyecto con las bases concedidas como tarjetas (nombre, categoría,
      documentos, estado de indexado, última actualización) y el asistente «Añadir conocimiento» de tres pasos
      (elegir o crear base → subir documentos o pegar texto/URL → indexado en vivo con reintento), con la
      concesión al proyecto implícita; a nivel tenant, la misma pantalla con «concedida a» por base y conceder
      o retirar desde la tarjeta; los documentos viven dentro de su base. Vocabulario: «base de conocimiento» y
      «documento»; los ids y los términos de ingesta sólo en el detalle de un fallo.
      **Test**: vitest del asistente (tres pasos, fallo con reintento); e2e de añadir conocimiento a un proyecto.
      **Coste**: 2,5 d.
  - ⏳ **Implementada el 2026-09-29, casilla abierta en su mitad URL.** Asistente de tres pasos (elegir o crear
    base → subir ficheros o pegar texto → indexado en vivo con reintento), concesión implícita, «activar una base
    existente» sin subir (incluidas las built-in), y tarjetas con cifras exactas calculadas en SQL. **La URL no
    existe en el dominio** y añadirla abre SSRF: la decide el operador en el
    [ADR 0170](../05-architecture-decisions/0170-ingesta-de-conocimiento-por-url.md) (`proposed`). Si elige A, este
    ADR la retira con `rejects:`; si elige B, se abre casilla nueva.

## Ola 4 — Recursos del tenant con patrón común (P1 · ~5 d)

### `task_ui_30` — Patrón de pantalla de recurso

- [ ] **Título**: `components/resource/` con cabecera (título, contador, acción principal), barra de filtros y
      búsqueda, lista/tarjetas con campos en el mismo sitio (nombre, procedencia, ámbito, uso, última
      actividad) y panel lateral de detalle; se aplica a Agentes, Equipos, Agentes humanos y Skills y tools.
      **Test**: vitest del patrón y de cada pantalla migrada.
      **Coste**: 1,5 d.
  - ⏳ **Implementada el 2026-09-29, casilla abierta hasta cerrar la revisión adversarial.** `components/resource/`
    (cabecera, barra, tarjeta con campos fijos y panel lateral), aplicado a Agentes, Equipos, Agentes humanos y a
    Skills y tools, que tiene dos pestañas. Los campos del patrón salen sólo donde el dominio los tiene: «última
    actividad» sólo en Agentes, calculada de `executions`. En Equipos, Agentes humanos y Tools no existe ese dato
    y no se inventa. «Uso» es el número de equipos del agente y de miembros del equipo; el resto no lo guarda.

### `task_ui_31` — Memorias por ámbito con procedencia

- [ ] **Título**: `GET /memories` ampliado (ámbito, proyecto, agente, categoría, fecha, `created_by`,
      `execution_id`, `recall_count`); `/admin/memories` con pestañas por ámbito y contador, cada memoria con
      quién, cuándo, dónde y cuántas veces se recuperó; buscador semántico. Las privadas sólo para su dueño
      (principio de ámbitos). Lo que una memoria antigua no tenga registrado se enseña como «desconocida»; si
      el dominio no guarda la procedencia, esta casilla propone la migración en un ADR antes de inventarla.
      **Test**: integración del endpoint (cross-tenant y ámbito privado); vitest de las pestañas y la ficha.
      **Coste**: 2 d.
  - ⏳ **Implementada el 2026-09-29, casilla abierta hasta cerrar la revisión adversarial.** `GET /memories` con
    procedencia resuelta en lote y filtros nuevos, más `/memories/counts` y `/memories/search` (híbrido), todos con
    la regla de privadas del listado. **«Cuántas veces se recuperó» sale «desconocido»** porque la columna
    `recall_count` la crea `task_mem_02` del plan
    [`memoria-agentes-2026-09-09`](memoria-agentes-2026-09-09.md), por la regla de reparto de ese plan («este
    entrega el dato y la API; el de UI, el sitio donde se ve»). La API ya expone el campo a `null`. «Categoría»
    es `type`: el `kind` tipado es de `task_mem_04`.

### `task_ui_32` — Agentes por pestañas y marketplace con «dónde está desplegado»

- [ ] **Título**: la ficha del agente agrupa en pestañas Identidad y modelo · Skills y tools · Conocimiento ·
      Memorias · Actividad; el marketplace queda en Catálogo · Instaladas · Publicar, con nombres en vez de
      UUID, la capacidad de cada instalación (`task_mk_10`), sus despliegues por proyecto (endpoint ampliado)
      y «Desplegar en un proyecto» que lleva a Capacidades.
      **Test**: vitest de la ficha por pestañas y de Instaladas; e2e de la cola de revisión enlazada.
      **Coste**: 1,5 d.
  - ⏳ **Implementada el 2026-09-29, casilla abierta hasta cerrar la revisión adversarial.** Ficha del agente en
    cinco pestañas; marketplace en Catálogo · Instaladas · Publicar; `GET /marketplace/installations` con
    `deployments` en una consulta. «Desplegar en un proyecto» navega a Capacidades del proyecto, porque el
    despliegue vive allí: no hay acción de despliegue global.

## Ola 5 — Piel (P3 · opcional · ~3 d)

### `task_ui_40` — Sistema visual

- [ ] **Título**: tipografía, densidad, paleta semántica, estados vacíos y microcopys, con la skill de diseño
      del proyecto y sin tocar la estructura de las olas 1-4. Sólo si el operador la aprueba tras ver las
      cuatro anteriores.
      **Test**: vitest de regresión de los `data-testid`; `next build`.
      **Coste**: 3 d.

---

## Revisión adversarial de las olas 2 y 3

**Intento 1 (2026-09-29)**, skill `adversarial-review` del plugin sobre `828ddb4f..aa1cc4e8`, lentes A+B+C+D
en paralelo con el agente `reviewer`. La C y la D las activó el selector: SQL con `format` en
`project_costs.py`, la ruta OAuth y `await` dentro de bucles. Correcciones con el agente `implementer`.

| #   | Grado     | Lente | Gap                                                                                                                       | Tarea        | Estado                                                                                                                                 |
| --- | --------- | ----- | ------------------------------------------------------------------------------------------------------------------------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Important | D, B  | La sonda OAuth lee Vault en síncrono, en serie y sin caché en un endpoint con polling de 30 s, y un token corrupto da 500 | ui_10, ui_20 | corregido                                                                                                                              |
| 2   | Important | B, D  | Los contadores de cada base se calculan sobre la primera página, y se hacen dos peticiones por fila                       | ui_21        | corregido: agregados en SQL en el listado e índice `ix_documents_kb_created` (migración 0150, reversible)                              |
| 3   | Important | D, B  | El sondeo del asistente no acaba con `indexed_empty`, con más de 100 ficheros ni al cerrar el diálogo                     | ui_21        | corregido                                                                                                                              |
| 4   | Important | A     | Una base del catálogo o una ya existente no se puede conceder desde el proyecto sin subir un documento                    | ui_21        | corregido: «activar una base existente», incluidas las built-in                                                                        |
| 5   | Important | B     | La bandeja pinta «Nada pendiente» y contador 0 cuando una consulta falla o está cargando                                  | ui_03        | corregido                                                                                                                              |
| 6   | Important | A, B  | `e2e/approval-request.spec.ts` busca un testid retirado                                                                   | ui_03        | corregido                                                                                                                              |
| 7   | Important | A     | La URL del enunciado se cae sin decisión escrita                                                                          | ui_21        | ADR 0170 `proposed`: decide el operador. La mitad URL queda abierta                                                                    |
| 8   | Minor     | D     | Resumen sin ventana en `executions.created_at` y sin caché                                                                | ui_10        | corregido                                                                                                                              |
| 9   | Minor     | A     | Los bloques del Resumen enlazan a las pantallas antiguas y no a las pestañas                                              | ui_10        | corregido                                                                                                                              |
| 10  | Minor     | A, C  | La consulta de categorías sin predicado de tenant (CWE-639, defensa en profundidad)                                       | ui_21        | corregido                                                                                                                              |
| 11  | Minor     | B     | La pestaña Equipo oculta a los miembros built-in que `/teams/{id}` sí enseña                                              | ui_12        | corregido                                                                                                                              |
| 12  | Minor     | A, B  | El menú de Trabajo mantiene cuatro entradas que llevan a la misma bandeja                                                 | ui_03        | corregido                                                                                                                              |
| 13  | Minor     | A     | Vocabulario de ingesta en la tarjeta, fuera del detalle del fallo                                                         | ui_21        | corregido                                                                                                                              |
| 14  | Minor     | B     | Cerrar el asistente tras un fallo parcial no refresca la lista                                                            | ui_21        | corregido                                                                                                                              |
| 15  | Minor     | A     | La casilla de `task_ui_11` estaba sin marcar y su nota decía «cerrada»                                                    | ui_11        | corregido                                                                                                                              |
| 16  | Minor     | A     | La tabla «Endpoints nuevos» no recogía `/team`, `/costs` ni `/runs?project_id=`                                           | ui_12        | corregido                                                                                                                              |
| 17  | Minor     | A     | «Probado» y «Responsable» se decidieron sólo en comentarios                                                               | ui_20, ui_11 | corregido: anotado abajo                                                                                                               |
| 18  | Minor     | B     | «Conectar» desde Capacidades vuelve a la pantalla de configuración MCP                                                    | ui_20        | deuda aceptada: el callback OAuth es un contrato de ADR 0127; cambiarlo pide tocar su redirección                                      |
| 19  | Minor     | C     | Un miembro puede sumar el coste por proyecto con `/runs?project_id=`, que `/costs` le niega                               | ui_12        | **decisión del operador**: el coste por run es visible a miembros desde `5211c8ac`; si pasa a ser sólo de admin, se recorta en `/runs` |

Dos decisiones que el enunciado no fijaba y que se anotan aquí para que no vivan sólo en el código
(gap 17). **«Probado»** (`task_ui_20`): el dominio no guarda el resultado de «Probar». La tarjeta
enseña el de la última prueba de esta sesión y no persiste nada. **«Responsable»** del portfolio
(`task_ui_11`): es el `created_by` del plan, porque el plan no tiene otro campo de responsable.

Los e2e que clican antes de la hidratación (`sidebar-complete`, `kb-ui`, `marketplace-admin`,
`marketplace-review`, `tools-catalog`) se corrigieron en la misma tanda con una espera de hidratación
común (`e2e/helpers/hydration.ts`) sobre una señal real: `data-hydrated` en `<html>`, que pone
`components/layout/hydration-marker.tsx`.

**Verificación del intento 1**, hecha por el orquestador sobre el árbol con las tres tandas de corrección:
vitest **1777 passed** (205 ficheros); integración de las zonas tocadas **89 passed**, con cross-tenant; `tsc`,
eslint, `check-i18n`, `check-component-size`, `ruff` y `mypy_gate` (802 ficheros) limpios; guardas de docs y rutas
**510 passed**. En e2e, el bloque del Resumen, Runs, Capacidades, Conocimiento, bandeja, menú, memorias y dashboard
da **30 de 31**. El único rojo era el spec de `kb-ui`, que buscaba el enlace de ingesta en la fila cuando la
corrección 13 lo movió al detalle. Ajustado, da **6 de 6**. Dos notas de corrección: el predicado de categorías es
`tenant_id = :tid OR is_builtin` y no `tenant_id IS NULL`, porque desde la migración 0030 las categorías built-in
viven bajo el tenant de plataforma (lo encontró el implementador y es correcto). Y queda una **incoherencia
Minor nueva**: la pestaña Equipo ya enseña a los miembros built-in, pero `team_role_agents` (dispatch del
ADR 0091) sigue sin contarlos, así que un built-in miembro no tapa su hueco de rol. Cambiar eso cambia a quién
asigna el dispatch, así que se lleva al intento 2 en vez de tocarlo aquí.

**Intento 2 (2026-09-29)** sobre `aa1cc4e8..e0aa4feb`: la ola 4 más las correcciones del intento 1, con las
cuatro lentes y la tabla anterior traspasada. Las catorce correcciones del intento 1 se confirman una a una, cada
una con su test. La lente C, sin hallazgos. Gaps nuevos:

| #   | Grado     | Lente | Gap                                                                                                    | Tarea         | Estado                                                                                                                     |
| --- | --------- | ----- | ------------------------------------------------------------------------------------------------------ | ------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 20  | Important | D     | El sondeo del asistente hace una petición por documento cada 1,5 s                                     | ui_21         | en corrección: una petición con `ids`                                                                                      |
| 21  | Important | B     | `["projects","tenant"]` guarda dos formas de dato y la pestaña Instaladas cae al error boundary        | ui_32         | en corrección: una clave por forma                                                                                         |
| 22  | Important | B     | La pestaña por defecto de la bandeja se recalcula con el sondeo y remonta lo que se está escribiendo   | ui_03         | en corrección                                                                                                              |
| 23  | Important | A     | «Skills y tools» se quedó sin la pestaña Skills, y `GET /skills` existe                                | ui_30         | en corrección                                                                                                              |
| 24  | Minor     | A     | «Última actividad» no se pinta, y para Agentes el dato está en `executions`                            | ui_30         | en corrección                                                                                                              |
| 25  | Minor     | B     | La caché de la sonda OAuth no se invalida al conectar, y el Resumen cachea `vault_unavailable`         | ui_20         | en corrección                                                                                                              |
| 26  | Minor     | B     | Memorias: la cabecera cuenta todas y la lista corta en 50 sin aviso                                    | ui_31         | en corrección                                                                                                              |
| 27  | Minor     | B     | Clave React duplicada con un despliegue activo y otro deshabilitado en el mismo proyecto               | ui_32         | en corrección                                                                                                              |
| 28  | Minor     | B     | «Activar una base existente» ofrece candidatos antes de saber cuáles ya están concedidas               | ui_21         | en corrección                                                                                                              |
| 29  | Minor     | B     | La procedencia de una memoria escrita por un agente built-in sale «desconocida»                        | ui_31         | en corrección                                                                                                              |
| 30  | Minor     | D, A  | La migración 0150 no usa `CREATE INDEX CONCURRENTLY`, que pide `conventions.md`                        | ui_21         | en corrección                                                                                                              |
| 31  | Minor     | D     | Memorias precarga `/similar` fila a fila, también en la búsqueda                                       | ui_31         | deuda aceptada: viene de antes del plan y su comentario ya la marcaba; pasarla a lote es trabajo del buscador de similares |
| 32  | Minor     | A     | Las casillas de la ola 4 no tenían nota, y la dependencia `recall_count` sólo constaba en un docstring | ui_30, 31, 32 | corregido: notas en cada casilla                                                                                           |
| 33  | Minor     | A     | El ADR 0170 remitía a una nota de `task_ui_21` que no existía                                          | ui_21         | corregido                                                                                                                  |
| 34  | Minor     | A     | Built-in miembros del equipo que el dispatch no cuenta (el pendiente del intento 1)                    | ui_12         | **decisión del operador**, anotada en la casilla                                                                           |

Descartado con evidencia de la lente B: las bases built-in salen con 0 documentos en el agregado, pero ya
salían así antes del diff, porque la RLS de `documents` no tiene política de lectura built-in. No lo introduce
esta ola.

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
