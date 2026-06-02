---
title: Asignar tools a un agente (básicas vs avanzadas)
audience: tenant admin, project owner
phase: 06.15-agent-tools-assignment-ui
updated: 2026-06-01
---

# Asignar tools a un agente

Esta guía explica cómo un `tenant_admin` decide **qué tools puede usar
un agente** desde el editor de agente, qué significan **básicas** y
**avanzadas**, qué reglas de scope se aplican y cómo se comporta el
enforcement en runtime.

> **TL;DR**: en `/admin/agents/{id}` → sección **"Tools del agente"**,
> activa las tools que el agente puede usar (pestañas **Básicas** /
> **Avanzadas**) y guarda. Si **no asignas ninguna**, el agente mantiene
> su comportamiento actual (sin restricción). En cuanto asignas alguna,
> el agente queda restringido a exactamente ese conjunto.

## El modelo en una frase

Un agente puede usar una tool si está en su lista de **tools asignadas**
(la junction `agent_tools`). Esa lista la editas tú; el runtime la hace
cumplir (`ToolRegistry`). El frontend **no es la barrera** — sólo
configura; la restricción real se aplica donde se ejecuta la tool.

## Básicas vs avanzadas — qué es cada una

La dicotomía es **derivada** (no hay un campo "tier"; ver
[ADR 0044](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md)):

| Pestaña       | Qué incluye                                                                                                                                 |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **Básicas**   | Las tools **built-in** del platform (`is_builtin=true`): file, runtime, git, network, orquestación                                          |
| **Avanzadas** | Tools **custom** del tenant (`is_builtin=false`) y/o ejecutores externos (`mcp_tool`, `http_endpoint`, `python_function`, `docker_command`) |

Las **básicas** son las 18 tools seedeadas por la plataforma
(`read_file`, `write_file`, `apply_patch`, `list_files`, `search_code`,
`run_pytest`, `run_lint`, `run_typecheck`, git, network…). Aparecen para
**cualquier** agente de cualquier tenant.

Las **avanzadas** son lo que tu tenant ha creado en `/tools` o lo que
pasa por un ejecutor externo (incluidas las tools MCP descubiertas de un
MCP server del proyecto).

### `security_level` es un eje aparte

Cada tool lleva un badge de **`security_level`**:

- `safe` — sin efectos peligrosos (lectura, listados).
- `sandboxed` — escribe/ejecuta dentro del sandbox del runtime
  (`write_file`, `run_pytest`).
- `privileged` — capacidad elevada; revísala antes de asignarla.

`security_level` es **ortogonal** a básica/avanzada: una básica puede
ser `sandboxed`, una avanzada puede ser `safe`. La UI muestra ambos
badges por separado. No confundas "avanzada" con "peligrosa".

## Cómo asignar (paso a paso)

1. Como `tenant_admin`, abre `/admin/agents/{id}`.
2. Baja a la sección **"Tools del agente"**.
3. Cambia entre las pestañas **Básicas** y **Avanzadas**.
4. Activa (toggle/checkbox) las tools que el agente puede usar. Cada fila
   muestra el nombre, la descripción, y los badges de `security_level` e
   `implementation_type`.
5. **Guarda**. La UI hace un `PUT /agents/{id}/tools` con el conjunto
   completo (es **declarativo**: lo que guardas es exactamente lo que
   queda; lo que no marcas se quita).
6. (Opcional) Pulsa **"Diagnóstico"** — disponible en agentes
   project-scoped — para abrir el panel read-only
   `agent-tools-diagnostic` y verificar el set efectivo.

> La sección es **read-only** para `tenant_user` (la UI esconde los
> controles de guardado) y para agentes `global_builtin` (hay que
> **forkearlos** primero y asignar sobre el fork; igual que con las KBs).

## Reglas de scope (qué puedes asignar a qué)

El backend valida el `PUT`; si algo viola el scope, devuelve **422** (o
**403** para un `global_builtin`):

- **Básicas (built-in)** → asignables a **cualquier** agente.
- **Avanzadas custom** (`is_builtin=false`) → sólo las de **tu propio
  tenant**. Las custom de otro tenant ni siquiera aparecen (RLS las
  oculta); intentar asignar un id ajeno se rechaza con 422.
- **Avanzadas MCP** (`implementation_type=mcp_tool`) → sólo si el
  **proyecto** del agente declara ese MCP server. Un agente template
  (sin proyecto) no tiene MCP servers, así que no puede recibir tools
  MCP. Configura el MCP server en el proyecto primero
  (ver [Configurar un MCP server](./configurar-mcp-server.md)).

## Cómo se comporta el enforcement

### Sin asignaciones ⇒ comportamiento actual

Un agente **sin filas** en `agent_tools` **no tiene restricción por
agente**. Es el comportamiento por defecto y el de todos los agentes que
ya existían antes de esta feature. **Asignar tools es opt-in**: nadie
pierde tools de golpe.

### Con asignaciones ⇒ restringido a ese conjunto

En cuanto el agente tiene al menos una fila, su toolset queda
restringido a **exactamente** las tools asignadas. En runtime, el
`ToolRegistry` rechaza cualquier tool fuera del allowlist **antes** de
ejecutarla.

### Intersección con el chat-mode

Si el agente corre bajo un **chat mode** que también declara un
`allowed_tools`, el conjunto efectivo es la **intersección** de las dos
listas — una tool debe estar permitida en **ambas** capas:

```
tools efectivas = tools asignadas al agente  ∩  allowed_tools del modo
```

- Si el agente no restringe (sin filas) → manda el allowlist del modo.
- Si el modo no restringe (sin allowlist, p.ej. el dispatch de tareas)
  → manda el conjunto del agente.
- Si **ambos** restringen → sólo lo que esté en los dos. Si no comparten
  ninguna tool, el resultado es **vacío** = el agente no puede usar
  ninguna tool (idéntico al modo "discusión").

> ⚠️ Es **intersección**, no unión. Asignar `write_file` al agente NO se
> la habilita si el chat-mode activo no la incluye en su allowlist.

### Quitar todas las asignaciones

Guardar con la lista **vacía** borra todas las filas `agent_tools` del
agente y **restaura el comportamiento por defecto** (sin restricción).
Es la forma de "deshacer" una restricción sin romper nada.

## `config_override` por asignación

Cada asignación admite un `config_override` JSON opcional, que se
superpone a los defaults de la tool del catálogo (mismo campo que
`AgentTool.config_override`). Úsalo para afinar parámetros de una tool
sólo para este agente sin tocar el catálogo global.

## Verificar con el diagnóstico

El panel read-only `agent-tools-diagnostic`
(`/admin/projects/{project_id}/agent-tools-diagnostic`) muestra el set
efectivo de tools de los agentes de un proyecto. Tras asignar, ábrelo
(botón **"Diagnóstico"** en la sección, visible en agentes
project-scoped) y confirma que refleja exactamente lo que esperas. Es la
forma rápida de auditar "¿qué tools verá realmente este agente?".

## Anti-patrones

### ❌ Esperar que la UI sea la barrera

Esconder una tool en la UI no impide su uso. La restricción real vive en
el `ToolRegistry` del runtime, alimentado por el allowlist del task spec.
Si quieres que un agente NO pueda usar una tool, **no se la asignes** (y
recuerda que sin filas = sin restricción, así que asigna el subconjunto
que sí quieres).

### ❌ Asignar una MCP tool sin el MCP server en el proyecto

El backend rechaza con 422. Primero configura el MCP server en el
proyecto del agente; luego sus tools aparecen en la pestaña Avanzadas.

### ❌ Intentar asignar sobre un agente built-in

Los `global_builtin` son platform-managed (403 en el `PUT`). Forkéalo
(`/admin/agents/{id}` → "Hacer copia") y asigna sobre el fork. El fork
mantiene `forked_from_agent_id`.

### ❌ Confundir "avanzada" con "peligrosa"

El tier (básica/avanzada) habla del **origen/ejecución** de la tool, no
de su riesgo. El riesgo lo da `security_level`. Revisa siempre el badge
de `security_level` antes de asignar una `privileged`.

## Reference técnica

- ADR formal: [`docs/05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md`](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md).
- Matriz RBAC (qué rol para qué endpoint):
  [`docs/04-reference/rbac.md`](../04-reference/rbac.md) (sección `agents.py`).
- Plan que lo materializa:
  [`docs/roadmap/06.15-agent-tools-assignment-ui.md`](../roadmap/06.15-agent-tools-assignment-ui.md).
- Tools built-in (catálogo): [ADR 0014](../05-architecture-decisions/0014-tools-builtin.md);
  MCP + ejecutores: [ADR 0025](../05-architecture-decisions/0025-mcp-tools-y-ejecutores.md).
- Patrón espejado (KBs por agente): [ADR 0026](../05-architecture-decisions/0026-agent-scoped-kbs.md)
  - [guía rol vs stack](./knowledge-bases-rol-vs-stack.md).
