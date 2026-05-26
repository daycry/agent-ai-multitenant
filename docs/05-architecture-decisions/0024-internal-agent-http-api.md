---
adr: "0024"
title: HTTP `/internal/agent/*` para la comunicación sandbox → api-server
status: accepted
date: 2026-05-26
deciders: System Admin
phase: 04.5-agent-runtime-integration
---

# ADR 0024 — Sandbox → api-server vía HTTP `/internal/agent/*`

> **Estado: `accepted`.** Cierra Plan 04.5: las tools que el sandbox
> agent-runtime expone al modelo (memory_recall, memory_store,
> rag_search, document_convert, promote_to_kb) hablan con el
> api-server por una familia HTTP dedicada bajo `/internal/agent/*`,
> autenticada con un bearer JWT de vida corta minteado por el worker
> antes de lanzar el contenedor.

## Contexto

Plan 04 terminó con la pila de memoria + RAG + KBs montada **en el
api-server**: tablas, motores, endpoints humanos (`/memories`,
`/knowledge-bases`, etc.). El agent-runtime — el contenedor sandbox
donde corre la loop de LangGraph del agente — sigue llamando a
`memory_recall`, `memory_store` y `document_convert` como
**placeholders 501** desde `task_02_19`. Cerrar Plan 04 sin
cerrar esa brecha dejaría 4 de los 5 tests humanos del plan inejectables
(memoria privada / por equipo, RAG con citas, promoción humana,
ingestión).

Plan 04.5 nace para esa brecha. Llegamos con tres opciones serias
sobre la mesa:

### Opción A — Todo por HTTP `/internal/agent/*`

Cada tool en el sandbox abre una sesión HTTP al api-server contra
endpoints **dedicados** (`/internal/agent/memory-recall`,
`memory-store`, `rag-search`, `document-convert`, `promote-to-kb`).
Autenticación: un JWT _purpose-specific_ con `kind: "agent"` que el
worker mintea justo antes de lanzar el contenedor y lo inyecta como
`AGENTIC_INTERNAL_TOKEN`. La tool decodifica → llama → mapea
respuesta a `ToolResult`.

### Opción B — Pre-inyección de contexto

El worker resuelve la memoria + RAG **antes** de arrancar el
contenedor y se la inyecta como JSON en `AGENT_TASK_SPEC`. El sandbox
no llama a nadie; sólo lee lo que ya recibió. Para writes
(memory_store, promote_to_kb) se vuelve a la opción A o se reusa el
patrón `OrchestrationSink` que ya tenemos para `kanban_update`.

### Opción C — Híbrido sink + HTTP

Reads por HTTP (necesitamos respuesta), writes por
`OrchestrationSink` (fire-and-forget — el worker dragea efectos al
acabar la ejecución). Era nuestra idea de partida porque copia el
patrón ya probado.

## Decisión

**Opción A: todas las tools por HTTP a `/internal/agent/*`.**

Cinco endpoints, una sola dependencia de auth (`get_agent_principal`),
un único modo de transporte para el sandbox.

## Por qué

**1. Read-after-write dentro de un mismo run.**
La opción C (writes vía sink) rompe la semántica natural del agente:
si un step llama `memory_store("X")` y dos steps después llama
`memory_recall(query that matches X)`, el segundo NO ve X — el sink
sólo se drena cuando la `Execution` termina. Eso fuerza al agente a
"recordar lo que acaba de aprender" sin canal alguno para releerlo.
HTTP elimina el problema: write commitea, read posterior lo ve. Es
el mismo modelo mental de un proceso normal contra una BD.

**2. Menos piezas móviles que la opción C.**
Híbrido = mantener un sink + un drain + 5 endpoints + 5 schemas
de efectos + tests de cada camino. HTTP puro = 5 endpoints + 5
schemas + un cliente HTTP. La opción C ahorraba "una llamada de
red por write" y costaba mucho más código.

**3. Compatible con ADR 0012 (aislamiento de contenedores).**
El sandbox **sigue sin tener** credenciales de BD, ni socket Docker,
ni acceso al fileystem del host. Sólo abre una conexión HTTP al
api-server (que ya está alcanzable desde la red `agentic-agents` —
ya lo hacemos para el LLM via egress-proxy). El token bearer es la
única credencial nueva y vive como env var dentro del contenedor.

**4. Defense in depth contra impersonation humana / agente.**
El JWT lleva `kind: "agent"` como discriminador. La dep
`get_agent_principal` rechaza tokens **sin** ese claim; la dep
`get_principal` (humana) rechaza tokens **con** ese claim. Mismo
secreto de firma HS256 para los dos espacios, pero los dominios
quedan disjuntos: una fuga de JWT humano no abre `/internal/agent/*`
y viceversa.

**5. Encaja con la pre-inyección de contexto a futuro.**
Cuando un proyecto quiera "inyectar el top-3 de memorias en
`AGENT_TASK_SPEC`" como optimización (opción B), se podrá montar
encima de A: el worker hace la primera `memory_recall` por HTTP
antes del launch y la pasa por env. Las tools HTTP siguen
disponibles para cualquier recall posterior. A no impide B; A → B
sí (no al revés).

## Consecuencias

- **El worker (no el orchestrator)** es el que mintea el token, en
  `workers/execution.py` justo antes de `runner.run_streamed(...)`.
  TTL 24 h (`_AGENT_TOKEN_DEFAULT_TTL`) — la vida real del sandbox
  son minutos; el margen evita carreras en el handoff.

- **Resolución de scope/owner en el servidor.** El cliente
  (sandbox) **nunca** pasa `team_id` ni `project_id`; el endpoint
  los resuelve del `Agent` row pinned por el token. Un agente no
  puede mirar la KB de otro proyecto pasando IDs alien.

- **Escalación de scope rechazada.** `memory_store` con `scope`
  por encima del `memory_scope` del agente → 403. Defensa en
  profundidad sobre el CHECK del schema.

- **El sandbox sigue siendo sync.** El cliente HTTP es
  `httpx.Client` (sync) porque el `ToolRegistry` es sync. Si el
  futuro requiere streaming (p.ej. RAG con SSE), abrirá un
  segundo cliente async.

- **Tests integración**: cada tool tiene su test de wire-up que
  ejerce el endpoint **y** el adapter (con `httpx.MockTransport`
  para el adapter, `ASGITransport` para el endpoint).

## Alternativas descartadas

- **Opción B (pre-inyección pura)** — no soporta writes en absoluto
  y el contexto a inyectar es ilimitado en peor caso. Se queda como
  optimización futura sobre A.
- **Opción C (sink writes + HTTP reads)** — rompe read-after-write
  y duplica infraestructura sin beneficio.
- **gRPC / MCP-RPC desde el sandbox** — complica el cliente sin
  aportar nada sobre HTTP+JSON en este perímetro.

## Referencias

- ADR 0012 — aislamiento de contenedores agent-runtime.
- ADR 0019 — egress-proxy del sandbox.
- Plan 04.5 (`docs/roadmap/04.5-agent-runtime-integration.md`).
- Changelog Plan 04.5 (`docs/07-changelog/04.5-agent-runtime-integration.md`).
- Código: `apps/api-server/src/api_server/auth/internal_agent.py`,
  `apps/api-server/src/api_server/routers/internal_agent.py`,
  `docker/agent-runtimes/agent-runtime/agent_runtime/internal_api.py`.
