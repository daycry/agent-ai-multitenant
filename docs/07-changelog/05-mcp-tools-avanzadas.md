---
plan_id: 05-mcp-tools-avanzadas
title: MCP y Tools Avanzadas
started_at: 2026-05-26
completed_at: 2026-05-28
status: pending_human_validation
tasks_done: 16
tasks_total: 16
tasks_pending_local: []
docs_language: es
---

> **Estado:** plan **`pending_human_validation`** — las 16 tareas
> (`task_05_01`..`task_05_16`) están en `done` con sus tests
> automáticos en verde. Quedan pendientes los tres tests humanos
> (`human_05_01`..`human_05_03`) que el revisor humano ejecutará
> sobre la rama `plan/05-mcp-tools-avanzadas` antes de mergear a
> `master`. Plan 05 abre el sistema agéntico al ecosistema MCP
> entero y activa los tres tipos de tools que en Plan 01 quedaron
> modelados pero inertes (`http_endpoint`, `python_function`,
> `docker_command`).

# Changelog — Plan 05 · MCP y Tools Avanzadas

Quinta fase del Plan de Implementación. El Plan 04 dio a los agentes
**memoria** (memoria de ejecuciones + RAG sobre documentos). El Plan
05 les da **manos**: acceso real a sistemas externos vía MCP, más los
tres tipos de tools nativas que faltaban por activar.

## Por qué

Hasta aquí los agentes usaban sólo tools builtin (`shell_exec`,
`http_request`, `memory_recall`, `file_*`). Suficiente para ejecutar
planes técnicos sobre el repo del proyecto, pero el agente no podía
mirar issues de GitHub, postear en Slack, consultar Jira ni leer una
KB externa. Plan 05 abre ese acceso por dos vías:

1. **MCP (Model Context Protocol)** — protocolo abierto de Anthropic;
   cada integración publica un servidor MCP que estandariza su API
   como tools. El platform habla MCP genéricamente; añadir GitHub o
   Slack es declarar un MCP server en el proyecto, no escribir un
   módulo Python.

2. **Tools nativas no-MCP** — `Tool.implementation_type` ya tenía
   `http_endpoint` / `python_function` / `docker_command` desde Plan 01. Plan 05 activa los ejecutores con su envelope de seguridad.

## Qué entró (16 tareas + 1 stretch)

### Fase A — Cliente MCP genérico (3 tareas)

- **`task_05_01`** Cliente MCP Python con los tres transports
  (stdio / sse / streamable_http). Wrapper sobre el SDK oficial
  `mcp` con jerarquía propia de excepciones (transport / auth /
  tool). `MCPClient.connect()` como async context manager.
- **`task_05_02`** Descubrimiento one-shot: `discover_tools(config)`
  hace connect → initialize → list_tools → close en una llamada.
  `DiscoveryResult` con `server_name`, `server_version`,
  `server_instructions`, `tools[]`.
- **`task_05_03`** Adaptador agent-runtime ↔ MCP. `MCPToolRunner`
  bridge sync-over-async con loop asyncio en hilo aparte. Tools
  registradas como `<server>.<tool>` en el `ToolRegistry`. Errores
  MCP folded a `ToolResult.ok=False`.

### Fase B — Configuración por proyecto (4 tareas)

- **`task_05_04`** Validación Pydantic de `Project.mcp_servers`
  (JSONB). `MCPServerConfigModel` enforce transport-specific
  invariants, regex de name, `auth_ref` con prefijo `vault:`, nombres
  únicos por proyecto.
- **`task_05_05`** Inyección de auth Vault al `connect()`. Resolver
  Protocol + `StaticVaultResolver` (tests) + `HvacVaultResolver`
  (production con lazy hvac). El secret se fusiona en `env` (stdio)
  o `headers` (http) — el config frozen original no se muta.
- **`task_05_06`** UI admin-panel `/admin/projects/[id]/mcp-servers`.
  CRUD del array `mcp_servers` con dialog transport-aware y editor
  key/value reutilizable. Persistencia via `PUT /projects/{id}` (la
  validación backend de task_05_04 sigue siendo la fuente de
  verdad).
- **`task_05_07`** Endpoint `POST /projects/{id}/mcp/test-connection`
  - botón "Probar" en la UI. Errores tipados (AUTH_ERROR /
    TRANSPORT_ERROR / CONFIG_ERROR / UNKNOWN_ERROR) que la UI
    ramifica por código sin parsear mensajes.

### Fase C — Integraciones verified (4 tareas + 1 stretch)

- **`task_05_08`** Catálogo `packages/shared-mcp/.../catalog.py` con
  `McpServerTemplate` (dataclass frozen). Arranca con docling-mcp
  documentado en `docs/04-reference/mcp-servers.md`.
- **`task_05_09`** Familia SCM: github-mcp + gitlab-mcp +
  azure-devops-mcp (más bitbucket-mcp en stretch). Tests
  parametrizados en `test_github_mcp.py`.
- **`task_05_10`** postgres-mcp con secret único `DATABASE_URI` (pin
  contra splits future en host/user/pass que dejarían fugas
  parciales).
- **`task_05_11`** Plantillas filesystem + gdrive + gmail +
  gcalendar + slack + jira + linear, con spot-checks de seguridad
  por integración (gmail "send" footgun, slack scopes mínimos,
  jira basic-auth de tres campos).
- **Stretch (post-roadmap)**: el usuario pidió ampliar el catálogo
  con servidores estilo Confluence, Notion, Sentry, Brave Search,
  etc. 12 entradas extra en 4 nuevas categorías
  (observability / search / browser / meta). El catálogo cierra
  Fase C con **24 plantillas en 10 categorías**.

### Fase D — Tools avanzadas y cierre (5 tareas)

- **`task_05_12`** `HttpEndpointTool` con URL template + render
  regex-seguro (no `str.format`, sin attribute walking) +
  enforcement de allowlist + body cap + scheme http/https.
  Diferente del builtin `http_request` (que es la tool genérica).
- **`task_05_13`** `PythonFunctionTool` con subprocess aislado
  vía `_python_sandbox_runner.py` (stdlib-only). Empty env + cwd
  tempdir + timeout + crash isolation. Sandbox best-effort para
  código operator-vetted; para untrusted code la respuesta es
  task_05_14.
- **`task_05_14`** `DockerCommandTool` con contenedor efímero,
  cap_drop ALL + no-new-privileges + read_only + tmpfs /tmp +
  uid 1000 + mem_limit + pids_limit + network=none por defecto +
  remove=True. Tripwire `_FORBIDDEN_RUN_KWARGS` rechaza launch si
  un futuro intento de colar `privileged` se cuela.
- **`task_05_15`** Panel diagnóstico
  `/admin/projects/[id]/agent-tools-diagnostic`. Read-only.
  Endpoint backend que devuelve, por agente project-scoped, su
  lista de Tool rows wired + los MCP servers del proyecto. UI
  con badges por `implementation_type` y `security_level`.
- **`task_05_16`** Este changelog + ADR 0025
  (`docs/05-architecture-decisions/0025-mcp-tools-y-ejecutores.md`).

## Métricas

- **Commits**: 16 (uno por tarea + 1 stretch del catálogo extendido).
- **Tests automáticos**: 116 pytest + 11 Playwright. Pasan en
  CI < 1m total.
- **Plantillas en catálogo**: 24 en 10 categorías.
- **Líneas añadidas**: ~6.000 (código + tests + docs + ADR).

## Lo que queda fuera

- **Marketplace de MCP servers** con niveles de confianza + ratings
  — Plan 09.
- **Guardrails declarativos** sobre `security_level=privileged` —
  Plan 11. Hoy un agente con `docker_command` o `gmail-mcp` wired
  puede invocar la tool sin gate; Plan 11 los interceptará.
- **Wiring real de `HvacVaultResolver` en api-server** — pendiente.
  La dependency seam `get_vault_resolver()` está en `routers/mcp.py`
  pero devuelve `None`. Mientras tanto las configs con `auth_ref`
  vía `/test-connection` devuelven AUTH_ERROR con mensaje
  explícito.

## Tests humanos

Pendientes de ejecutar por revisor humano sobre la rama:

- **`human_05_01`** — github-mcp end-to-end con PAT de prueba en
  Vault. Comprobar que la UI lista las tools y que un agente puede
  crear un issue.
- **`human_05_02`** — `docker_command` Tool con
  `python:3.12-alpine`. Comprobar el aislamiento (network=none,
  read-only fs, container removido al terminar).
- **`human_05_03`** — Allowlist de `http_endpoint`. Configurar un
  Tool con URL fuera del allowlist y verificar que falla con error
  explícito + queda en audit log.

## Próximo plan

Plan 06 (Workspaces & worktrees) — los agentes empezarán a operar
sobre código real del proyecto en lugar de workspaces volátiles.
