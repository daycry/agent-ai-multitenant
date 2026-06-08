---
adr: "0025"
title: MCP como vía principal de integración, ejecutores nativos para http_endpoint / python_function / docker_command
status: accepted
date: 2026-05-28
deciders: System Admin
phase: 05-mcp-tools-avanzadas
---

# ADR 0025 — MCP como vía principal de integración + ejecutores nativos para tools no-MCP

> **Estado: `accepted`.** El plan 05 implementa la integración MCP + un
> catálogo de 24 plantillas verificadas, y activa los ejecutores de los
> tres tipos de Tool que en Plan 01 quedaron modelados pero sin
> implementación (`http_endpoint`, `python_function`, `docker_command`).
> Este ADR fija las decisiones load-bearing que el code review puede
> apuntar — el porqué de cada elección de seguridad, ergonomía y
> separación de responsabilidades — para que no haya que excavar 16
> commits cuando alguien pregunte "y esto por qué así".

---

## Contexto

Al cerrar la Fase 04 (memoria/RAG) los agentes de la plataforma usaban
sólo **tools builtin** — `shell_exec`, `http_request`, `memory_recall`,
`file_*`. Eran suficientes para ejecutar planes técnicos pero la
plataforma no podía hablar con sistemas externos sin que escribiéramos
nosotros, módulo a módulo, cada integración (GitHub, Slack, Jira,
Postgres, GDrive, …).

Plan 05 abre ese acceso con dos vías complementarias:

1. **MCP (Model Context Protocol)**: cada empresa publica un servidor
   MCP que expone su API como tools estandarizadas. Cliente genérico
   en el lado nuestro → ecosistema entero accesible sin escribir
   código por integración.

2. **Tools nativas no-MCP**: el modelo de datos de Plan 01 ya tenía
   `Tool.implementation_type` con cinco valores —`builtin`, `mcp_tool`,
   `http_endpoint`, `python_function`, `docker_command`—. Los tres
   últimos estaban modelados pero no eran ejecutables. Plan 05
   activa esos ejecutores.

Las decisiones de este ADR cubren cómo se separan ambos caminos y
qué envelope de seguridad se aplica a cada uno.

---

## Decisiones

### 1) MCP como vía principal de integración con terceros

Cuando exista un servidor MCP para una integración, **lo usamos**.
No escribimos un módulo Python a medida. Razones:

- Mantenimiento del integrador (GitHub, Atlassian, IBM, …) sale del
  scope del platform: ellos versionan su API, nosotros consumimos.
- El catálogo (`packages/shared-mcp/src/shared_mcp/catalog.py`)
  declara una sola fuente de verdad por servidor: transport, comando
  o URL, env vars, secret keys, ruta Vault. La UI del operador y los
  tests cogen la entrada del mismo sitio.
- El secreto siempre va por Vault: `auth_ref` es un puntero
  `vault:secret/data/mcp/<server>/<project_id>`. CLAUDE.md ya dice que
  Vault es la única vía de credenciales; este ADR lo reafirma sólo
  para el caso MCP — un cleartext token en `mcp_servers` JSONB es
  **rechazado** por el validador Pydantic de task_05_04.

El catálogo arranca con 24 plantillas en 10 categorías (docs / scm /
data / files / comms / issues / observability / search / browser /
meta). Añadir una nueva son ~10 líneas de `McpServerTemplate` + una
sección en `docs/04-reference/mcp-servers.md` + ningún cambio en el
código de tests (parametrizan sobre `CATALOG`).

### 2) Cliente MCP genérico unificado por transport

`shared_mcp.MCPClient.connect()` envuelve el SDK oficial `mcp` y
oculta los tres transports (`stdio`, `sse`, `streamable_http`) detrás
de un único async context manager. El agent-runtime no se entera de
qué transport usa cada servidor — sólo recibe un `MCPSession`
genérico con `list_tools` y `call_tool`.

Bridge sync-over-async: el agent loop es síncrono (`ToolRegistry.call`),
el cliente MCP es asíncrono. `MCPToolRunner` ejecuta un loop asyncio
en un hilo aparte y expone `call_tool` como llamada bloqueante con
`asyncio.run_coroutine_threadsafe`. El loop nunca crashea en una
tool MCP — todas las excepciones MCP (transport / auth / tool error)
se foldea n a `ToolResult.ok=False`.

### 3) Validación + inyección de auth Vault en el momento de connect

`Project.mcp_servers` (JSONB) es validado por
`MCPServerConfigModel` (Pydantic) en cada `POST/PUT /projects`:

- `transport` es un Literal cerrado (stdio/sse/streamable_http).
- Invariantes transport-specific (stdio requiere command; http
  requiere url; sin cruces).
- `auth_ref` debe empezar por `vault:` o ser `None`. Tokens en
  texto plano no entran al sistema.
- Names únicos dentro del proyecto.

Al `MCPClient.connect()`, un `VaultResolver` opcional traduce
`auth_ref` a un dict `{KEY: value}` que se fusiona en `env` (stdio)
o `headers` (http). El config frozen original no se muta — los
punteros vault nunca pisan disco ni JSONB ni logs.

### 4) Tres ejecutores nativos para tools no-MCP, cada uno con su envelope

| Tipo              | Sandbox                                         | Network                           | Filesystem             | Secret env                      |
| ----------------- | ----------------------------------------------- | --------------------------------- | ---------------------- | ------------------------------- |
| `http_endpoint`   | n/a (httpx Client en proceso)                   | allowlist proyecto                | n/a                    | static_headers + Vault opcional |
| `python_function` | subprocess aislado, env={"PATH":…}, cwd=tempdir | heredada del worker               | tempdir limpio         | env scrubbed completo           |
| `docker_command`  | container efímero                               | `network_mode='none'` por defecto | read_only + tmpfs /tmp | sólo static_env                 |

**`http_endpoint`** (task_05_12): pre-cocinado por el operador con
una URL template `{placeholder}`. Diferente del builtin `http_request`
(que es la tool genérica que el agente usa para hitear cualquier URL
del allowlist) — aquí cada Tool row se advertiza con name +
description propias, y el agente no elige la URL. Allowlist
enforcement + body cap + scheme http/https + render con regex
(no `str.format`, sin attribute walking). JSON pre-parseado en el
output.

**`python_function`** (task_05_13): código del operador ejecutado en
un **subprocess** (no eval, no exec). El runner `_python_sandbox_runner.py`
es stdlib-only, importa el código via `importlib.util`, lee args de
stdin como JSON, dumpea `{ok, output}` o `{ok, error}` a stdout.
Pensado para "operator-vetted helper code": el subprocess da
heap fresco + env vacío + cwd tempdir + timeout + crash isolation,
pero NO da deny de red ni filesystem ni memory cap consistente. Para
código **untrusted**, la respuesta es `docker_command`.

**`docker_command`** (task_05_14): código arbitrario ejecutado en un
**contenedor efímero** con el envelope completo de seguridad: cap_drop
ALL + no-new-privileges + read_only + tmpfs /tmp + uid 1000 + mem_limit

- pids_limit + network=none por defecto + remove=True. Mirrors
  ADR 0012 (aislamiento agent-runtime) adaptado a one-shot. Tripwire
  `_FORBIDDEN_RUN_KWARGS` rechaza el launch si una edición futura
  intenta colar `privileged`, `cap_add`, etc.

### 5) Lo que NO entra en Plan 05 (deferred)

- **Marketplace de MCP servers** con niveles de confianza, ratings y
  flagging por la comunidad — Fase 9. El catálogo de Plan 05 es la
  lista de los verificados por nosotros; no hay UI para que un
  tenant añada uno arbitrario aún.
- **Wiring real de `HvacVaultResolver` en api-server** — la dependency
  seam `get_vault_resolver()` está en su sitio (returns None hoy);
  un trabajo posterior la sustituirá por una instancia real de hvac.
  Mientras tanto, configs con `auth_ref` que pasan por el endpoint
  `/mcp/test-connection` devuelven AUTH_ERROR con un mensaje
  explícito.
- **Guardrails declarativos sobre tools privilegiadas** (gmail-send,
  docker_command) — Fase 11. Plan 05 distingue `security_level`
  (safe / sensitive / privileged) en el modelo, pero no actúa sobre
  él. Hoy un agente con la tool wired puede llamarla; cuando llegue
  Fase 11 los guardrails interceptarán `privileged` y pedirán
  validación humana.
- **Ampliación del catálogo** con OneDrive, Datadog, Sentry-extra,
  Stripe, HubSpot, Salesforce — entran cuando un proyecto real las
  pida. Añadir una entrada al catálogo son ~10 líneas.

---

## Consecuencias

- **+** El operador del proyecto declara MCP servers o tools nativas
  desde la UI sin programar — y sin tocar Vault directamente; sólo
  necesita conocer la ruta `vault:...` y meter el secreto allí.
- **+** Un tool MCP es indistinguible de una tool builtin para el
  agente loop (mismo `ToolRegistry`, mismo `ToolResult`).
- **+** El catálogo es código + docs; cada plantilla nueva trae sus
  tests automáticamente (parametrizan sobre `CATALOG`).
- **+** Los tres tipos de tools no-MCP tienen envelopes distintos
  pero consistentes en error-mapping (todo se folda en
  `ToolResult.ok=False`, nunca crash al agent loop).
- **−** Mantener 24 entradas del catálogo en lock-step con sus
  servidores upstream es un coste de mantenimiento real. El test
  `test_extended_catalog_introduces_new_categories` mitiga drift
  silencioso pero un servidor que cambia su shape de auth nos
  obliga a un commit + ADR.
- **−** El sandbox de `python_function` es best-effort en Windows
  (sin RLIMIT, sin namespaces). Para untrusted code real hay que
  usar `docker_command`, lo cual el ADR documenta explícitamente.
- **−** Hasta que se wire el `HvacVaultResolver` real en api-server,
  el botón "Probar conexión" devuelve AUTH_ERROR en configs con
  `auth_ref`. El UX es correcto (mensaje claro), pero el operador
  no puede validar Vault end-to-end en la UI todavía.

---

## Actualización Plan 06.18 — import discovery→catálogo + threading de `mcp_servers`

> **`06.18-tools-overhaul` (`task_06_18_12`, ADR 0052).** Plan 05 dejó dos
> huecos que 06.18 cierra: el **discovery era one-shot** (`test-connection`
> listaba las tools del server pero **no las persistía**, así que no eran
> asignables a un agente), y **`project.mcp_servers` nunca llegaba al
> runtime** (un agente con una tool MCP asignada no tenía sesión MCP que la
> ejecutara).

- **Importación discovery → catálogo.** Tras un `test-connection` exitoso,
  `POST /projects/{id}/mcp/servers/{server}/import-tools`
  (`routers/mcp.py`, `tenant_admin`) hace **upsert** de filas `Tool` con
  `implementation_type='mcp_tool'`, `category='mcp'` y `name` **namespaced
  `<server>.<tool>`** (así `<server>.read_file` no parece un duplicado del
  built-in `read_file` — faceta **Origen=MCP** de la taxonomía de ADR 0049).
  La selección la decide el operador (`tool_names`, **no** se importa todo
  automáticamente — control de supply-chain); `security_level` arranca en
  `sandboxed` (mínimo privilegio) y es editable. El upsert es **idempotente**
  y respeta el `UNIQUE(tenant_id, name)` de `task_06_18_04` (una carrera se
  traduce en 409 limpio). Tenant-safe: un proyecto de otro tenant → 404; el
  server debe estar declarado en `project.mcp_servers`.
- **Threading de `mcp_servers` al runtime.** `project.mcp_servers` viaja por
  la misma ruta de spec que el allowlist de tools (06.15) y de comandos
  (06.16): `dispatch` → `ExecutionRequest` → `__main__`, que arranca un
  `MCPToolRunner`, llama a `register_mcp_server` por cada server y lo cierra
  en `finally`. Así una tool `<server>.<tool>` asignada se ejecuta de verdad
  en lugar de morir como `unknown tool`.

## Referencias

- Plan 05 (`docs/roadmap/05-mcp-tools-avanzadas.md`).
- **Plan 06.18 (`docs/roadmap/06.18-tools-overhaul.md`):** import
  discovery→catálogo + threading de `mcp_servers`; ADR 0052
  (`0052-import-mcp-tools-catalogo.md`); `routers/mcp.py`,
  `orchestrator/dispatch.py`, `agent_runtime/mcp_tools.py`.
- Catálogo de plantillas (`docs/04-reference/mcp-servers.md`).
- ADR 0012 — Aislamiento de contenedores agent-runtime. El envelope
  de `docker_command` Tools deriva de aquí.
- ADR 0021 — Capa shared-llm con catálogo cerrado. Mismo patrón
  (catálogo en código + tests + docs) aplicado a LLM providers.
- `packages/shared-mcp/` — cliente + catálogo + auth + discovery.
- `docker/agent-runtimes/agent-runtime/agent_runtime/` —
  `mcp_tools.py`, `http_endpoint_tool.py`, `python_function_tool.py`,
  `docker_command_tool.py`.
