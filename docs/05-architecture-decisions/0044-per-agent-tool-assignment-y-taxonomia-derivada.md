---
adr_id: "0044"
title: "Asignación de tools por agente + taxonomía básica/avanzada derivada (sin nuevo campo)"
status: accepted
date: 2026-06-01
authors: [system_architect]
plan_referenced: 06.15-agent-tools-assignment-ui
docs_language: es
---

# ADR 0044 — Asignación de tools por agente y taxonomía básica/avanzada derivada

## Contexto

El catálogo de tools (`/tools` CRUD, ADR 0014 + 0025) y la junction
`agent_tools(agent_id, tool_id, config_override)` ya existían en el
modelo desde Plan 02/05. Lo que faltaba era:

1. **No había forma de asignar tools a un agente desde la UI.** La
   junction `agent_tools` sólo se rellenaba por seeds/SQL; no existía
   ningún endpoint para que un `tenant_admin` declarara "qué tools
   puede usar este agente", ni sección en el editor de agente, ni
   distinción visual entre tools **básicas** y **avanzadas**.
2. **El runtime no filtraba el toolset por `agent_tools`.** La única
   restricción real vivía a nivel de chat-mode (`ChatModeConfig.
allowed_tools`); un agente veía siempre el toolset completo aunque
   tuviera filas en `agent_tools`.
3. **No estaba fijado qué significa "básica vs avanzada".** El panel
   de diagnóstico (`agent-tools-diagnostic`) y la futura UI necesitaban
   una taxonomía estable, pero el modelo `Tool` no tiene un campo
   `tier` / `level` que la exprese.

La tentación inmediata era añadir una columna `Tool.tier ∈ {básica,
avanzada}` (otra migración, otra fuente de verdad que mantener
sincronizada con `is_builtin`/`implementation_type`). Este ADR la
descarta.

## Decisión

### 1. "Básica vs avanzada" es una **derivación**, no un campo nuevo

La taxonomía se calcula a partir de columnas que **ya existen** en
`Tool`:

```
básica   = is_builtin == true     (CUALQUIER implementation_type:
                                    builtin, docker_command, http_endpoint, …)
avanzada = is_builtin == false    (tool custom del tenant + tools MCP)
```

El único criterio es `is_builtin`. El `implementation_type` NO entra en la
dicotomía (sería un bug: `run_pytest` es `is_builtin=true` con
`implementation_type=docker_command` y es **básica**).

- Las **básicas** son las 18 tools `builtin` seedeadas en
  `PLATFORM_TENANT_ID` (`builtin_tools.py`): file (`read_file`,
  `write_file`, `apply_patch`, `list_files`, `search_code`), runtime
  (`run_pytest`, `run_lint`, `run_typecheck`…), git, network y
  orquestación. Son `is_builtin=true` y asignables a **cualquier**
  agente de cualquier tenant.
- Las **avanzadas** son todo lo que NO es de plataforma: tools custom
  creadas por un tenant (`is_builtin=false`) y las tools MCP descubiertas
  de los MCP servers del proyecto. El `implementation_type` es ortogonal:
  una básica de plataforma también puede usar un ejecutor externo (p. ej.
  `run_pytest` es `docker_command` y **sigue siendo básica**).

`security_level` (`safe` / `sandboxed` / `privileged`) es un eje
**ortogonal**: una básica puede ser `sandboxed` (`write_file`) y una
avanzada puede ser `safe`. La UI muestra ambos como badges
independientes; no se mezclan en la dicotomía básica/avanzada.

**Por qué derivar y no persistir**: `is_builtin` + `implementation_type`
ya determinan unívocamente el tier. Una columna `tier` sería
información redundante que habría que mantener consistente a mano (y se
desincronizaría en cuanto alguien creara una tool sin rellenarla).
Derivar mantiene **una sola fuente de verdad** y evita una migración.
Si en el futuro la dicotomía deja de ser función de estas dos columnas
(p.ej. una básica que se quiera marcar como avanzada por política),
**ese** sería el momento de un campo de primera clase — con su ADR.

### 2. Asignación por agente vía `agent_tools` — endpoints tenant-scoped

Dos endpoints sobre `/agents/{id}/tools` (router `agents.py`),
calcados del patrón de los grants de KB del Plan 06.9
(`/agents/{id}/knowledge-bases`, ADR 0026):

- `GET /agents/{id}/tools` (`tenant_user`) — lista las tools asignadas
  al agente. La _read shape_ espeja el panel read-only
  `agent-tools-diagnostic` más `is_builtin` (la taxonomía) y el
  `config_override` por asignación.
- `PUT /agents/{id}/tools` (`tenant_admin`) — **declarativo**: reemplaza
  el conjunto entero de `agent_tools` del agente en una transacción
  (borra filas viejas + inserta el nuevo set). Una lista vacía limpia
  todas las asignaciones.

**Reglas de scope** (validadas en el `PUT`):

- **Built-in** (`is_builtin`, `PLATFORM_TENANT_ID`) → asignable a
  cualquier agente.
- **Custom** (`is_builtin=false`) → sólo del propio tenant. RLS ya
  oculta las custom de otros tenants, así que un `tool_id` que no
  resuelve se devuelve como **422** (el body trae un id no asignable,
  no es un 404 de path).
- **MCP** (`implementation_type=mcp_tool`) → sólo si el **proyecto** del
  agente declara ese MCP server (match por el prefijo de server-name de
  `implementation_ref`). Un agente template (sin proyecto) no tiene MCP
  servers ⇒ toda tool MCP falla el scope (422).

Un agente `global_builtin` rechaza la escritura con **403** (igual que
los grants de KB: es platform-managed, hay que forkearlo primero y
asignar sobre el fork).

Tenant-scoped por **RLS vía la tabla `agents`**: la sesión de tenant
sólo ve agentes y tools del tenant + built-ins de plataforma.

### 3. Enforcement en runtime — **backward-compatible**

La restricción real se aplica en el runtime, no en el frontend (la UI
sólo configura). El módulo `api_server/agent_tools_enforcement.py`
expone dos piezas puras (sin acoplar a HTTP), reutilizadas por el
orquestador y por los tests:

- `resolve_agent_tool_names(session, agent_id) -> frozenset[str] | None`
  — el set de `Tool.name` asignados al agente, o **`None`** cuando el
  agente **no tiene filas**. El sentinel `None` es load-bearing: **sin
  filas ⇒ sin restricción por agente** (comportamiento actual; no se le
  quita ninguna tool a un agente existente). Con filas ⇒ el agente
  queda restringido a exactamente esos nombres.
- `combine_tool_allowlists(agent_tool_names, mode_allowed_tools)` —
  combina el set por-agente con el allowlist del chat-mode activo
  (`ChatModeConfig.allowed_tools`). Son dos restricciones independientes,
  así que el set efectivo es su **intersección**; un `None` en cualquiera
  significa "esa capa no restringe". Una lista **vacía** es un resultado
  válido: "las dos capas no comparten tool" ⇒ el runtime bloquea todo
  (idéntico al modo discusión con allowlist vacío).

El cableado (orquestador `dispatch.py`): se resuelve el set por-agente,
se intersecta con el allowlist del modo (en el task-dispatch path no hay
modo, así que el set por-agente queda solo), y **sólo si el resultado no
es `None`** se emite la clave `allowed_tools` en el task spec
(`ExecutionRequest.allowed_tools → _agent_spec → AGENT_TASK_SPEC →
ToolRegistry.set_allowed_tools`). El `ToolRegistry` del runtime ya
rechazaba en `call()` cualquier tool fuera de su allowlist; este plan
sólo decide **cuál** es ese allowlist por agente. No es el motor de
guardrails por capas (Plan 11): es el enforcement mínimo de call-time
que hace real una asignación por agente.

Se forwardean **nombres** de tool (`Tool.name`, p.ej. `read_file`), no
ids: el registry del runtime y los allowlists de chat-mode se expresan
sobre el mismo namespace de nombres, así la intersección es sobre un
único espacio.

## Consecuencias

### Lo que mejora

- Un `tenant_admin` puede limitar qué tools usa cada agente desde el
  editor (`/admin/agents/[id]` → "Tools del agente"), con pestañas
  **Básicas** / **Avanzadas** y badges de `security_level` +
  `implementation_type`.
- Cero migración, cero columna nueva. La taxonomía vive como función
  pura de `is_builtin`/`implementation_type`.
- Backward-compatible por diseño: los agentes existentes (sin filas
  `agent_tools`) no cambian de comportamiento. El enforcement sólo se
  "enciende" cuando alguien asigna explícitamente.

### Lo que añade de complejidad

- El orquestador tiene un paso extra de resolución (`resolve_agent_tool_
names` + `combine_tool_allowlists`) en el dispatch. Es una query de
  join `agent_tools × tools` + una intersección de sets; coste
  despreciable.
- La dicotomía básica/avanzada está **codificada en dos sitios**: el
  backend (la `read shape` expone `is_builtin` + `implementation_type` y
  deja derivar al cliente) y el frontend (que la deriva para repartir en
  pestañas). Se documenta aquí + en la guía para que ambos coincidan. No
  hay una constante compartida, pero la regla es trivial y estable.

### Trade-offs explícitos

- **No persistimos el tier**: si mañana hiciera falta que una básica
  cuente como avanzada (o un override por tenant), habría que introducir
  el campo entonces — no ahora. La derivación cubre el 100 % de los
  casos actuales.
- **El allowlist efectivo es una intersección estricta**: si el chat-mode
  ya restringe y además el agente restringe, el agente sólo puede usar lo
  que esté en **ambos**. Es lo correcto (cada capa es un límite), pero
  puede sorprender a quien espere "unión". La guía lo explica.

## Alternativas consideradas

### Alt-1: Columna `Tool.tier ∈ {básica, avanzada}`

Persistir la dicotomía como campo de primera clase.

- ✅ Lectura trivial, sin lógica derivada.
- ❌ Redundante con `is_builtin`/`implementation_type` — dos fuentes de
  verdad que se desincronizan. Una tool creada sin rellenar `tier`
  quedaría en un estado indefinido.
- ❌ Migración (reversible) + backfill de las 18 built-ins + todas las
  custom existentes. Coste sin beneficio mientras la derivación sea
  exacta.

Rechazada. Anotada como follow-up **sólo si** la dicotomía deja de ser
función de las dos columnas existentes.

### Alt-2: Enforcement "deny-by-default" (sin filas ⇒ ninguna tool)

Que un agente sin asignaciones no pudiera usar **ninguna** tool.

- ✅ Modelo más estricto y explícito.
- ❌ **Rompe todos los agentes existentes** de golpe (ninguno tiene
  filas `agent_tools` hoy). Viola el requisito de backward-compat del
  plan. Sería un cambio de comportamiento masivo y silencioso.

Rechazada. El sentinel `None` (= sin restricción) preserva el
comportamiento actual; el modo estricto sería opt-in en un plan futuro
(p.ej. un flag `enforce_empty_as_deny` por tenant) con su ADR.

### Alt-3: Filtrar en el frontend / en el api-server al construir el spec

Aplicar la restricción al construir la respuesta de la UI o en el
api-server, sin tocar el runtime.

- ❌ El frontend NO es fuente de verdad: un agente podría seguir
  invocando tools no asignadas vía la API directa o vía el chat. La
  restricción real **tiene que** vivir donde se ejecuta la tool
  (`ToolRegistry` en el runtime). El api-server sólo decide el allowlist
  y lo forwardea.

Rechazada como mecanismo de enforcement; la UI sí filtra para mostrar,
pero no es la barrera.

## Esquema (sin cambios)

```sql
-- Ya existía (Plan 02/05). NO se modifica en este plan:
CREATE TABLE agent_tools (
    agent_id        UUID NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool_id         UUID NOT NULL REFERENCES tools(id)  ON DELETE CASCADE,
    config_override JSONB,
    PRIMARY KEY (agent_id, tool_id)
);
-- RLS por tenant_id vía la tabla agents (tenant-scoped).
```

**Sin migración** en Plan 06.15. La taxonomía y el enforcement se montan
sobre las columnas y la junction existentes. (El head único antes de
este plan es `0071_model_prices_provider_id`; este plan no lo mueve.)

## Riesgos

| Riesgo                                                                   | Probabilidad | Impacto | Mitigación                                                                                              |
| ------------------------------------------------------------------------ | ------------ | ------- | ------------------------------------------------------------------------------------------------------- |
| Frontend y backend derivan la dicotomía con reglas distintas             | Baja         | Bajo    | La regla está fijada en este ADR + en la guía; el backend expone los campos crudos y deja derivar.      |
| Un admin asigna tools y "rompe" un agente al quitarle una que necesitaba | Media        | Bajo    | El diagnóstico (read-only) muestra el set efectivo; quitar todas las filas restaura el comportamiento.  |
| MCP tool asignada a un agente cuyo proyecto pierde el MCP server después | Baja         | Bajo    | La tool deja de resolver en runtime; el `ToolRegistry` la rechaza. El re-PUT vuelve a validar el scope. |
| Intersección estricta sorprende a quien espera unión con el chat-mode    | Media        | Bajo    | Documentado en la guía con un ejemplo concreto.                                                         |

## Trazabilidad

- Roadmap: `docs/roadmap/06.15-agent-tools-assignment-ui.md` (5 tareas, 4 fases).
- Endpoints: `apps/api-server/src/api_server/routers/agents.py`
  (`list_agent_tools` / `set_agent_tools` + helpers de scope).
- Schemas: `apps/api-server/src/api_server/schemas/agents.py`
  (`AgentToolAssignment`, `SetAgentToolsRequest`, `AgentToolResponse`).
- Enforcement: `apps/api-server/src/api_server/agent_tools_enforcement.py`
  (`resolve_agent_tool_names`, `combine_tool_allowlists`); cableado en
  `apps/orchestrator/src/orchestrator/dispatch.py`.
- Runtime: `docker/agent-runtimes/agent-runtime/agent_runtime/`
  (`tool_wiring.py`, `tools.py` → `ToolRegistry.set_allowed_tools` /
  `call` / `is_allowed`).
- Frontend: sección "Tools del agente" en
  `apps/admin-panel/app/admin/agents/[id]/page.tsx`.
- Tests: `tests/integration/test_agent_tools_assignment.py`,
  `test_agent_tools_enforcement.py`; e2e
  `apps/admin-panel/e2e/agent-tools-assign.spec.ts` (escrito, no
  ejecutado).
- Guía: `docs/03-guides/asignar-tools-a-agentes.md`.
- RBAC: `docs/04-reference/rbac.md` (sección `agents.py`).
- ADRs relacionados: 0014 (tools builtin), 0025 (MCP + ejecutores),
  0026 (agent-scoped KBs — patrón espejado).
