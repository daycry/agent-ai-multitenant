---
plan_id: remediacion-marketplace-mcp-2026-09-02
title: Remediación del marketplace y de los MCP — la cadena abre de punta a punta
completed_at: null
docs_language: es
---

# Plan remediacion-marketplace-mcp-2026-09-02 — Remediación del marketplace y de los MCP

## Resumen

Antes de este plan, «instalar del marketplace» y «declarar un MCP en el
proyecto» eran dos gestos que **no llegaban al run**: el egress bloqueaba todo
MCP remoto, las tools había que importarlas a mano una a una, una instalación
podía quedar `enabled` sin producir nada, y el agente que «tenía» una tool no
la invocaba. El plan cerró la cadena completa —egress → declarar → importar →
repartir por rol → desplegar → **invocar en el run**— y la dejó medida con un
test que despacha una tarea real y ve el paso `act` llamar a la tool desplegada.

Después dio al proyecto sus **anclas de integración** (epic padre de Jira,
página raíz de Confluence) y las llevó al preámbulo del run para que las skills
de Atlassian dejen de adivinarlas en el texto del plan. Y limpió el panel:
nombres en vez de UUIDs, procedencia del marketplace en la ficha del agente,
cola de revisión a un clic, i18n donde faltaba.

## Cambios por tarea

### Ola 0 — la cadena abre de punta a punta

- **`task_mk_0a` / `task_mk_0b`** — ADR 0165 (allowlist de hosts MCP remotos en el
  egress, ajuste de plataforma `egress.mcp_allowed_hosts`, renderizado del
  filtro del proxy, botón «Probar») y ADR 0166 (las tools MCP llegan al catálogo
  sin paso manual; enmienda de los ADR 0052 y 0100). Ambos `accepted` antes de
  tocar código, por la cadena de precedencia de `CLAUDE.md`.
- **`task_mk_00`** — el destino tras instalar deja de ser una pantalla vacía;
  revisión de dos lentes con tres correcciones.
- **`task_mk_02`** — egress: `shared_mcp.egress.proxied_httpx_client_factory`,
  descubrimiento y sonda **a través del proxy**, aviso tipado `EGRESS_BLOCKED`
  en la card del servidor (fail-open al guardar, fail-closed en el formulario),
  runbook y pantalla del System Admin. Verificado en vivo con
  `mcp.context7.com`.
- **`task_mk_01`** — `mcp/import_tools.py::import_server_tools` como **único**
  punto de import (R1-R5, límites L1-L3), tarea Celery `workers.mcp_import_server_tools`
  en la lane `marketplace`, disparo al guardar el servidor, al desplegar desde el
  marketplace y al completar el OAuth; retirada de tools al quitar el servidor.
- **`task_mk_10`** — ADR 0081 reabierto: opción (b) aplicada (`capability`
  derivado `catalog_row|on_deploy|deferred` visible en la API y el panel) y
  opción (a) propuesta por escrito para el operador.
- **`task_mk_11`** — el resultado del despliegue enseña avisos, OAuth pendiente
  y qué filas creó (`DeployResultNotes`).
- **`task_mk_14`** — sin permisos que consentir no hay consentimiento que
  esperar (`consent.needs_consent`).

### Ola 1 — la UI dice la verdad

- **`task_mk_12`** — `test_deployed_tool_is_invoked_in_the_run`: instalar →
  desplegar → despachar con el orquestador real → `agent_runtime.run_task` con
  modelo scripted → paso `act` que llama a `status_checker` por el executor
  `http_endpoint`. Playwright: `mcp-import-tools`, `agent-skills-assign`.
- **`task_mk_13`** — procedencia del marketplace (`listing` + versión) en las
  filas de tools y skills del agente; enlaces a la cola de revisión en el sidebar
  (Plataforma) y en la cabecera del marketplace; el fork **no** se lleva las
  tools MCP (son del proyecto, ADR 0128) y lo dice en `mcp_tools_not_copied`.

### Ola 2 — anclas de integración por proyecto

- **`task_mk_20`** — `projects.integrations` (migración `0149`, JSONB cerrado por
  proveedor: `jira {project_key, parent_issue_key}`, `confluence {space_key,
  root_page_id}`; 422 ante claves desconocidas o formatos malos) y sección
  «Integraciones» en la ficha del proyecto. Sin secretos: viven en el MCP.
- **`task_mk_21`** — las anclas viajan en el `ExecutionRequest` y el runtime las
  pliega como bloque «PROJECT INTEGRATION ANCHORS» tras la persona; las cuatro
  skills `atlassian-*` las leen primero y sólo caen al plan si faltan; guía
  «Ejemplo 3 — Atlassian completo».
- **`task_mk_22`** — el catálogo oficial siembra `atlassian-remote` y
  `github-remote` como listings `mcp_server` (derivados de las plantillas del
  catálogo MCP; las `stdio` fuera por transporte); la guía deja de pedir la
  asignación por agente y documenta la allowlist de egress.
- **`task_mk_23`** — nombres en vez de UUIDs en «Instaladas» y en los shares,
  buscador de tenant al compartir (`GET /marketplace/shares/tenant-directory`),
  i18n de la sección de skills; la pestaña de compartir partida a `shares-tab.tsx`.

### Ola 3 — condicionada a ADR

- **`task_mk_30`** — [ADR 0167](../05-architecture-decisions/0167-tools-de-plataforma-para-el-arbol-jira-confluence.md)
  (`proposed`): `jira_children_of_parent` y `confluence_page_under_root` como
  tools de plataforma que envuelven al MCP con las anclas puestas, más la
  ingesta opcional del subárbol de Confluence. **Sólo se implementa si el
  operador lo acepta.**

## Decisiones que esperan al operador

- ADR 0081 §«Reapertura de la Fase B/C»: opción (a) —materializar
  `python_function`/`docker_command` en el sandbox— propuesta; (b) aplicada.
- ADR 0167: aceptar o rechazar las tools de plataforma de la ola 3.
- Tests humanos `human_mk_01..03` (plan §Tests humanos).

## Cómo verificarlo

- `pytest tests/integration/test_marketplace_v2_chain.py` — la cadena entera,
  incluido el run que invoca la tool desplegada.
- `pytest tests/integration/test_project_integrations.py tests/unit/test_agent_spec_integrations.py`
  y `docker/agent-runtimes/agent-runtime/tests/test_integrations_preamble.py` —
  las anclas de punta a punta.
- `pytest tests/integration/test_marketplace_seed.py tests/integration/test_cross_tenant_sharing.py`.
- Panel: `npm run test`, `npm run check:i18n`, `npm run check:size`; e2e
  mockeados `mcp-import-tools`, `agent-skills-assign`, `marketplace-review`,
  `marketplace-admin`.

## Documentación tocada

- ADR 0165, 0166 (nuevos, `accepted`), 0081 (reabierto), 0167 (nuevo, `proposed`).
- `docs/03-guides/configurar-mcp-server.md` (capas, egress, ejemplo Atlassian).
- `docs/06-runbooks/` — runbook de la allowlist de egress (`task_mk_02`).
- `docs/03-guides/gotchas/redis-con-contrasena-rompe-la-integracion.md` (scripts de dev).
