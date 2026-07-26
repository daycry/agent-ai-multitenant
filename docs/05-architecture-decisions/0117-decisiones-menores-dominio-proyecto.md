---
title: "ADR 0117: Tres decisiones menores del dominio Proyecto (MCP, validación humana por tarea, web-app)"
status: accepted
date: 2026-07-18
---

# ADR 0117: Decisiones menores del dominio Proyecto

Salen de la auditoría integral del dominio Proyecto (2026-07-17, hallazgos
PROJ-02, PROY2-06 y el `apps/web-app` vacío) vía `task_proy_f4` del plan
`remediacion-proyecto-integral-2026-07-17`. Las tres son decisiones de
producto/alcance que el equipo de plataforma NO debe tomar unilateralmente:
este ADR las deja preparadas con opciones y recomendación para que el operador
elija. Ninguna bloquea el resto de la remediación (ya implementada).

## (a) MCP por proyecto: empaquetar los servers stdio o retirar la superficie (PROJ-02)

### Contexto

La UI de proyecto ofrece configurar servidores MCP (`projects.mcp_servers` +
formulario en el admin-panel), pero es una **fachada**: los ~24 templates
stdio históricos referencian binarios (`npx …`, `uvx …`) que no existen en
ninguna imagen del stack; `test-connection` no puede validar stdio; y el
worker no lanza esos procesos. El único MCP operativo del stack es interno
(docling — y su vía operativa real hoy es docling-serve HTTP). Un operador que
configura un server MCP en su proyecto obtiene silencio.

### Opciones

1. **Empaquetar**: imagen `mcp-runners` con node+uv y los binarios de los
   templates soportados; el worker la lanza como sidecar efímero por-run con
   la misma red restringida que los runtimes. Coste: imagen nueva mantenida,
   superficie de seguridad (procesos arbitrarios stdio), matriz de versiones.
2. **Retirar de la UI** (recomendada a corto): ocultar la sección MCP de la
   página de proyecto (o marcarla «experimental — sin runtime») hasta que
   exista la imagen; conservar `shared-mcp` y la columna (`mcp_servers`)
   intactas. Coste: ninguna función se pierde (hoy no funciona); honestidad
   inmediata.
3. **Híbrida**: retirar stdio y permitir SOLO servers MCP **HTTP/SSE**
   remotos (URL + token), que sí son alcanzables sin binarios locales. Coste
   medio: validación de egress (allowed_domains) + test-connection HTTP real.

### Recomendación

**(2) ahora, con (3) como siguiente paso si hay demanda.** (1) solo con un
caso de uso concreto que lo pague.

### Resolución (a) — 2026-07-23: **Opción 3 (Híbrida), aceptada e implementada**

Elegida la **Opción 3**: el catálogo **ofrece SOLO servers MCP de transporte
HTTP** (`streamable_http`/`sse`); las plantillas `stdio` se **ocultan** del
picker (siguen en `CATALOG` para validación en tiempo de ejecución + auditoría,
no se borran). Motivación reforzada por un caso real (2026-07-23): sin poder
arrancar, un agente daba vueltas y escalaba a humano.

Implementación:

- `api_server.routers.mcp_catalog.offered_catalog()` filtra por transporte
  (`transport != "stdio"`) + deny-list `_UNAVAILABLE_TEMPLATE_IDS`; `GET
/mcp-catalog` solo devuelve HTTP.
- Tres plantillas HTTP nuevas en `shared_mcp.catalog`: `context7` (remoto),
  `atlassian` (sidecar `ghcr.io/sooperset/mcp-atlassian` sobre `streamable-http`)
  y `github-remote` (MCP remoto oficial de GitHub, PAT en cabecera vía Vault).
  Sustituyen a las stdio `jira-mcp`/`confluence-mcp`/`github-mcp` (ocultas).
- Tests: `tests/unit/test_mcp_catalog_availability.py` (solo-HTTP ofrecidas,
  stdio nunca ofrecidas, las 3 presentes) + ajuste de la familia scm en
  `tests/integration/test_github_mcp.py` y del recuento en
  `tests/integration/test_mcp_integrations.py` (24 → 27).
- Egress: los remotos exigen abrir su dominio en `projects.allowed_domains`
  (`mcp.context7.com`, `api.githubcopilot.com`); el sidecar Atlassian es
  hostname interno de `agentic-agents` (sin egress).
- Empaquetar binarios stdio (Opción 1) queda descartado salvo caso de uso que
  lo pague. Las decisiones (b) y (c) de este ADR siguen `proposed`.

Doc de referencia actualizado: `docs/04-reference/mcp-servers.md`
(sección «Qué se OFRECE en el picker (ADR 0117)»).

## (b) `task.human_validation_required`: implementar el flag o corregir CLAUDE.md (PROY2-06)

### Contexto

El principio 7 de CLAUDE.md promete: «Tests humanos a nivel de plan.
Excepción: `task.human_validation_required=true` para tareas individuales
críticas». Ese flag **no existe** (ni columna, ni schema, ni código): la
validación humana es solo por plan (review session al pasar a
`pending_human_validation`) y por categorías de acción sensible
(approval policies). La promesa lleva desde el día uno sin implementación.

### Opciones

1. **Implementar**: columna `tasks.human_validation_required BOOL` +
   `on_task_done` la respeta (la tarea queda `in_review` esperando un
   veredicto humano por-tarea en vez de `done`). Coste: nueva máquina de
   revisión por-tarea (hoy la review humana es por plan), UI, notificaciones.
2. **Corregir CLAUDE.md** (recomendada): borrar la excepción del principio 7
   y documentar las DOS vías reales de control humano fino que ya existen:
   políticas de aprobación por categoría de acción (13 categorías, 4
   plantillas) y `ask_human` (ADR 0114). Cubren el caso de uso («esta acción
   crítica necesita un humano») con granularidad mayor que un flag por tarea.
3. **Aplazar** con el flag documentado como «previsto»: perpetúa la promesa
   falsa — descartada.

### Recomendación

**(2)**: las approval policies + `ask_human` ya dan el control fino real; el
flag por-tarea duplicaría maquinaria. Si el operador prefiere (1), es un plan
propio (~2-3 d) — no un remiendo.

### Resolución (b) — 2026-07-26: **Opción 2 (corregir CLAUDE.md), aceptada e implementada**

El operador elige la **(2)**. El principio 7 ya no promete
`task.human_validation_required`: describe las dos vías que SÍ existen —
políticas de aprobación por categoría de acción sensible (13 categorías, 4
plantillas) y la tool `ask_human` (ADR 0114)— y dice explícitamente que el flag
no existe y por qué. Ambas se verificaron en código antes de escribirlo
(`shared_domain.approval_categories.APPROVAL_CATEGORIES`, `ask_human` cableada
en dispatch/run-contract/schemas).

Nada de código: el flag nunca tuvo columna. Lo que se retira es una promesa que
llevaba desde el día uno sin implementación — el tipo de mentira documental que
sobrevive porque nadie va a comprobarla.

Si algún día se prefiere la (1), es un plan propio (~2-3 d): máquina de revisión
por-tarea, UI y notificaciones. No un remiendo.

## (c) `apps/web-app` vacío: consolidar en admin-panel o plan de separación

### Contexto

CLAUDE.md declara `apps/web-app` («Frontend Next.js de tenants») separado de
`apps/admin-panel` («Frontend del System Admin»), pero `apps/web-app` está
vacío desde el día uno: TODO el frontend (tenants + System Admin) vive en
`admin-panel`, con RBAC por rol dentro de la misma app. Ningún compose lo
construye.

### Opciones

1. **Consolidar** (recomendada): declarar `admin-panel` como el frontend
   único (tenants + admin, separación por RBAC/rutas), borrar `apps/web-app`
   y actualizar CLAUDE.md + architecture-overview. Coste: cero código; el
   nombre `admin-panel` queda algo impreciso (renombrarlo sería un churn de
   imágenes/compose que no paga nada hoy).
2. **Separar**: plan para extraer las vistas de tenant a `web-app` (build,
   imagen, compose, auth compartida). Coste alto; beneficio real solo si los
   ciclos de release de tenant y admin deben divergir o si el aislamiento de
   superficie (bundle del admin no descargable por tenants) se vuelve un
   requisito.

### Recomendación

**(1)**: consolidar y documentar. (2) solo si aparece el requisito de
aislamiento de superficie.

### Resolución (c) — 2026-07-26: **Opción 1 (consolidar), aceptada e implementada**

El operador elige la **(1)**. `apps/admin-panel` queda declarado el frontend
ÚNICO (tenants + System Admin, separados por RBAC y rutas) y `apps/web-app` se
borra. Se actualizan CLAUDE.md, `docs/01-overview/02-architecture.md` y
`docs/context/architecture-overview.md` (nodos del Mermaid y sus aristas
incluidos, que si no quedaban huérfanos).

**El coste NO era «cero código», como decía la opción.** Tirando del hilo
apareció un fallo real de recuperación ante desastres:
`Settings.restore_app_services` incluía `web-app` entre los servicios que la
restauración completa para. Ese servicio no está en NINGÚN compose —ni el
versionado ni el que genera el instalador (`CORE_SERVICES`)— porque nunca tuvo
código. Y `_stop_app_stack` hace `docker compose stop <servicios>` y **eleva si
el código de salida no es 0**, que es justo lo que devuelve compose ante un
servicio desconocido: **la restauración abortaba en el paso 3, antes de
restaurar nada**. Solo se manifiesta ejecutándola de verdad, así que llevaba ahí
desde que se escribió la lista.

Corregido, con test que compara `restore_app_services` contra los servicios que
el generador del instalador declara (`tests/unit/test_restore_services_exist.py`).
Retirado también de `apps/installer/lib/preview.ts`, donde reservaba 256 MiB y
el puerto 3001 para un fantasma.

**Fuera del alcance de este ADR, anotado**: `apps/memorizer`,
`apps/personal-assistant` y `apps/webhook-dispatcher` **también contienen solo
`.gitkeep`**. No se borran —su lógica existe y vive embebida
(`api_server/memorizer/`, `api_server/assistant/`, los workers), así que la
carpeta puede ser un destino de extracción legítimo— pero CLAUDE.md ya las
marca como RESERVADAS en vez de listarlas como apps desplegables.

## Consecuencias

- **Las tres decisiones están resueltas** (a: 2026-07-23; b y c: 2026-07-26).
  Ninguna requirió el trabajo grande de sus opciones alternativas.
- La UI MCP ofrece solo transportes que funcionan; el principio 7 describe los
  mecanismos reales; el frontend documentado es el que existe.
- Coste real descubierto al implementar (c): el simulacro de recuperación estaba
  roto por un servicio fantasma. La lección: una divergencia «solo documental»
  puede tener aristas ejecutables — la lista de servicios de un runbook es
  código, aunque parezca prosa.
