---
title: Asignar tools (y skills) a un agente
audience: tenant admin, project owner
phase: 06.18-tools-overhaul
updated: 2026-06-04
docs_language: es
---

# Asignar tools a un agente

Esta guía explica cómo un `tenant_admin` decide **qué tools (y skills)
puede usar un agente** desde el editor de agente, cómo leer la
**taxonomía de tres facetas**, qué significa que una tool esté
**cableada en runtime** (`is_runtime_wired`), cómo ver el **set
efectivo** (`effective-tools`), y cómo encajan `shell_exec` + comandos
del proyecto y las tools importadas de un MCP server.

> **TL;DR**: en `/admin/agents/{id}` → sección **"Tools del agente"**,
> activa las tools que el agente puede usar y guarda. Si **no asignas
> ninguna**, el agente mantiene su comportamiento actual (sin
> restricción). En cuanto asignas alguna, el agente queda restringido a
> exactamente ese conjunto. El catálogo navegable está en
> **`/admin/tools`**.

## El modelo en una frase

Un agente puede usar una tool si está en su lista de **tools asignadas**
(la junction `agent_tools`). Esa lista la editas tú; el runtime la hace
cumplir (`ToolRegistry`). El frontend **no es la barrera** — sólo
configura; la restricción real se aplica donde se ejecuta la tool.

## Una sola fuente de nombres (ADR 0048)

Antes de este plan el mismo nombre lógico aparecía **triplicado**: el
catálogo lo llamaba `read_file`, el chat-mode `file_read` y el runtime
`file_read`. La intersección agente∩modo se calculaba sobre cadenas
crudas, así que una tool asignada como `read_file` y permitida por el
modo como `file_read` intersectaba al **conjunto vacío** → `unknown
tool` silencioso.

Ahora hay **una única fuente de verdad**:
`packages/shared-domain/src/shared_domain/tool_names.py`
(`CANONICAL_TOOL_NAMES`). Los nombres **canónicos** son los del catálogo;
una capa de **alias retrocompatible** mapea los nombres legacy
(`file_read → read_file`, `http_request → http_get + http_post`,
`notify_user → send_notification`, `semantic_search → rag_search`). No
hay rename duro: las filas `agent_tools` y los allowlists de chat-mode
existentes siguen funcionando. La intersección se calcula **siempre
sobre el espacio canónico** (`combine_tool_allowlists`), así que ya no
hay intersecciones vacías por desajuste de nombres.

## Taxonomía de tres facetas (ADR 0049)

Cada tool se clasifica por **tres ejes ortogonales** (no se mezclan).
La UI los sirve con etiqueta ES+EN desde un módulo compartido
(`apps/admin-panel/lib/tools/taxonomy.ts`) que usan por igual la
asignación y el diagnóstico, de modo que la misma tool muestra **idéntico
color/etiqueta en ambas superficies**:

| Faceta        | Campo                                 | Valores                                                                       |
| ------------- | ------------------------------------- | ----------------------------------------------------------------------------- |
| **Función**   | `category`                            | `file`, `runtime`, `network`, `knowledge`, `notification`, `command`, `mcp`…  |
| **Seguridad** | `security_level`                      | `safe` · `sandboxed` (Aislada) · `privileged` (Privilegiada)                  |
| **Origen**    | (deriva de `is_builtin` / `mcp_tool`) | **Plataforma** (built-in) · **Tenant** (custom) · **MCP** (`<server>.<tool>`) |

### Básicas vs avanzadas = la faceta **Origen**

"Básica vs avanzada" es la proyección de **Origen** sobre `is_builtin`
(es **derivada**, no hay un campo `tier`):

| Pestaña       | Qué incluye                                                                                                        |
| ------------- | ------------------------------------------------------------------------------------------------------------------ |
| **Básicas**   | Tools **built-in** de plataforma (`is_builtin=true`): file, runtime, red, notificación, conocimiento, `shell_exec` |
| **Avanzadas** | Tools **custom** del tenant (`is_builtin=false`) y las **MCP** importadas (`<server>.<tool>`)                      |

> **`security_level` es ortogonal al origen.** Una básica puede ser
> `privileged` (`shell_exec`) y una avanzada puede ser `safe`. No
> confundas "avanzada" con "peligrosa": el riesgo lo da el badge de
> Seguridad, no la pestaña.

### El catálogo built-in son **15 tools** (la familia `git` se retiró)

El seed `builtin_tools.py` define **15 tools** built-in: file
(`read_file`, `write_file`, `apply_patch`, `list_files`, `search_code`),
runtime (`run_pytest`, `run_lint`, `run_typecheck`, `run_build`), red
(`http_get`, `http_post`), conocimiento (`semantic_search`,
`summarize_text`), notificación (`send_notification`) y `shell_exec`.

La **familia `git`** (`git_status`/`git_diff`/`git_commit`/`git_log`) se
**retiró** del seed (ADR 0049): tenía categoría en la UI pero **ningún
ejecutor de runtime**, así que cualquier asignación moría como `unknown
tool`. Ofrecerla como asignable mentía sobre su disponibilidad.

## `is_runtime_wired` — disponibilidad honesta

No todo lo que está en el catálogo lo sabe ejecutar el runtime **hoy**.
`ToolResponse` expone un flag derivado **`is_runtime_wired`** que la UI
usa para marcar "No disponible aún" en vez de ofrecer la tool como si
funcionara. La fuente de verdad es
`shared_domain.tool_names.RUNTIME_WIRED_TOOL_NAMES`.

- **Cableadas (`is_runtime_wired=true`)**: `read_file`, `write_file`,
  `list_files`, `http_get`, `http_post`, `kanban_update`, `task_comment`,
  `agent_invoke`, `send_notification`, `rag_search` (vía
  `semantic_search`), `document_convert`, `promote_to_kb`,
  `memory_recall`, `memory_store`, los `run_*` (`docker_command`) y
  `shell_exec`.
- **No cableadas (todavía)**: `apply_patch`, `search_code` (la familia
  file registra sólo read/write/list) y `summarize_text` (sin ejecutor).
- Una tool **custom** (`http_endpoint` / `python_function` /
  `docker_command` / `mcp_tool`) cuenta como ejecutable por su
  `implementation_type`, no por este set built-in.

Al guardar la asignación (`PUT /agents/{id}/tools`), el backend
**avisa/`422`** si intentas asignar un nombre no ejecutable como si lo
fuera.

## Cómo asignar (paso a paso)

1. Como `tenant_admin`, abre `/admin/agents/{id}`.
2. Baja a la sección **"Tools del agente"**.
3. Cambia entre las pestañas **Básicas** y **Avanzadas**.
4. **Clica en cualquier parte de la fila** para (des)asignar la tool
   (toda la fila es un único control — el área que se ilumina al pasar
   el ratón es la misma que togglea; el cursor de mano sólo aparece si
   puedes editar). Cada fila muestra los badges de **Seguridad** e
   **Implementación** con tooltip accesible (hover + teclado).
5. Usa el checkbox **tri-state** del header del grupo para
   "Seleccionar/Quitar todas".
6. **Guarda**. La UI hace un `PUT /agents/{id}/tools` con el conjunto
   completo (es **declarativo**: lo que guardas es exactamente lo que
   queda). Aparece confirmación "Guardado".
7. (Opcional) Pulsa **"Diagnóstico"** — en agentes project-scoped — para
   ver el **set efectivo** real (consume `effective-tools`).

> La sección es **read-only** para `tenant_user` (sin cursor de mano ni
> toggle) y para agentes `global_builtin` (hay que **forkearlos** primero
> y asignar sobre el fork; igual que con las KBs).

## El set efectivo: `GET /agents/{id}/effective-tools`

El endpoint `GET /agents/{id}/effective-tools` (contrato de frontera con
el Plan 06.17) es la **única** fuente honesta de "qué tools verá
realmente este agente". Lo consume el panel de diagnóstico. Devuelve:

- `assigned`: cada asignación con `category` / `security_level` /
  `implementation_type` / `is_builtin`, sus `canonical_names` (alias
  resuelto) y `executable_in_runtime` por tool.
- `effective`: el conjunto canónico **realmente** cableado para el `mode`
  pedido = `(asignadas ∩ modo) ∩ runtime-wired`, más `shell_exec` sólo si
  su proyecto autoriza comandos.
- `unrestricted`: `true` cuando el agente no tiene asignaciones (mantiene
  su superficie por defecto; `effective` vacío por diseño).
- `shell_exec_effective`: `true` sólo si `shell_exec` está asignado **y**
  `allowed_commands` del proyecto no está vacío.
- `warnings`: avisos legibles ("set efectivo vacío en modo X", "tool
  asignada pero no ejecutable", "shell_exec sin allowed_commands").

### Intersección con el chat-mode

Si el agente corre bajo un **chat mode** con su propio `allowed_tools`,
el conjunto efectivo es la **intersección** (no la unión) de las dos
capas — y además **∩ runtime-wired**:

```
efectivas = (tools asignadas al agente ∩ allowed_tools del modo) ∩ runtime-wired
```

- Agente sin filas → manda el allowlist del modo.
- Modo sin allowlist (p. ej. el dispatch de tareas) → manda el set del
  agente.
- Ambos restringen → sólo lo que esté en los dos. Vacío = el agente no
  puede usar ninguna tool.

> ⚠️ Es **intersección**, no unión. Asignar `write_file` al agente NO se
> la habilita si el chat-mode activo no la incluye.

### Quitar todas las asignaciones

Guardar con la lista **vacía** borra todas las filas `agent_tools` y
**restaura el comportamiento por defecto** (sin restricción por agente).

## `shell_exec` + comandos del proyecto

`shell_exec` es **básica** (`is_builtin=true`) y **privilegiada**. Para
que sea **efectiva** en un agente hacen falta **dos cosas** (doble
autorización):

1. Asignar `shell_exec` al agente (en Básicas).
2. Autorizar binarios en el proyecto (`/admin/projects/{id}/commands`,
   chips + presets por stack). `allowed_commands` es **deny-by-default**:
   lista vacía ⇒ no ejecuta nada.

El cruce lo calcula `effective-tools` (`shell_exec_effective`). Sólo se
ejecutan binarios cuyo basename esté en `allowed_commands`; siempre argv
(`shlex`) + timeout + `cwd` confinado al workspace, nunca a través de una
shell. Detalle en
[Comandos y runtime por proyecto](./comandos-y-runtime-por-proyecto.md).

## Importar tools de un MCP server (ADR 0052)

Las tools MCP **no** existen en el catálogo hasta que las importas:

1. En `/admin/projects/{id}/mcp-servers`, declara el MCP server y pulsa
   **"Probar conexión"** (`test-connection`) — lista las tools que el
   server expone.
2. Pulsa **"Importar N tools al catálogo"** y elige la selección (multi).
   El operador decide **qué** importa (no se importa todo
   automáticamente).
3. Cada tool se persiste como fila `Tool` `mcp_tool` con `name`
   **namespaced `<server>.<tool>`** (faceta Origen=MCP), `category='mcp'`
   y `security_level=sandboxed` por defecto (editable). El upsert es
   idempotente (respeta `UNIQUE(tenant_id, name)`).
4. Ahora aparecen en la pestaña **Avanzadas** y son asignables. En
   runtime, `project.mcp_servers` viaja por el spec y el `__main__`
   arranca el `MCPToolRunner` y registra el server, así la tool se
   ejecuta de verdad.

Sólo puedes asignar tools MCP a un agente cuyo **proyecto** declare ese
MCP server (un agente template sin proyecto no tiene servers ⇒ 422). Ver
[Configurar un MCP server](./configurar-mcp-server.md).

## Asignar skills (ADR 0050)

Las Skills se cablearon end-to-end (opción A del ADR 0050). En la ficha
del agente, la sección **"Skills del agente"** funciona igual que las
tools (verbo único "Asignar"):

- `GET/PUT /agents/{id}/skills` (declarativo, mismas reglas de scope que
  `agent_tools`: built-in asignable; custom sólo del tenant;
  `global_builtin` → 403, forkear primero).
- El `prompt_fragment` de cada skill asignada se **inyecta en el system
  prompt efectivo** del runtime (vía `dispatch` → `steps.py`), así una
  skill deja de ser una promesa falsa y modula de verdad el
  comportamiento del agente.

## Catálogo navegable `/admin/tools`

`/admin/tools` (item "Catálogo" en el sidebar) permite explorar el
catálogo por las **tres facetas** + búsqueda, con la misma tarjeta-fila
que la asignación. Los **built-in son read-only**; las **custom** del
tenant tienen CRUD. Crear una custom con el mismo nombre que una built-in
(u otra del tenant) se rechaza con **409** (`UNIQUE(tenant_id, name)`).

## Anti-patrones

### ❌ Esperar que la UI sea la barrera

Esconder una tool en la UI no impide su uso. La restricción real vive en
el `ToolRegistry` del runtime. Si quieres que un agente NO pueda usar una
tool, **no se la asignes** (recuerda: sin filas = sin restricción).

### ❌ Asumir que "asignada" = "ejecutable"

Una tool asignada pero **no cableada** (`is_runtime_wired=false`, p. ej.
`apply_patch`) no se ejecuta. Mira el flag / el aviso de
`effective-tools` antes de confiar en ella.

### ❌ Asignar `shell_exec` sin autorizar comandos

Sin `allowed_commands` en el proyecto, `shell_exec` está asignado pero
**deny-all**. `effective-tools` lo avisa (`shell_exec sin allowed_commands`).

### ❌ Asignar una MCP tool sin importarla / sin el server en el proyecto

Primero impórtala desde la pantalla del MCP server (y declara el server
en el proyecto del agente); luego aparece en Avanzadas.

### ❌ Confundir "avanzada" con "peligrosa"

La faceta Origen (básica/avanzada) habla del **origen** de la tool, no de
su riesgo. El riesgo lo da `security_level`.

## Reference técnica

- Fuente única de nombres:
  [`docs/04-reference/tools.md`](../04-reference/tools.md) ·
  `packages/shared-domain/src/shared_domain/tool_names.py`.
- ADRs: [0044](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md)
  (asignación + taxonomía derivada),
  [0048](../05-architecture-decisions/0048-fuente-unica-nombres-tool.md)
  (fuente única de nombres),
  [0049](../05-architecture-decisions/0049-taxonomia-y-disponibilidad-de-tools.md)
  (taxonomía 3 facetas + `is_runtime_wired`),
  [0050](../05-architecture-decisions/0050-skills-cablear-o-deprecar.md)
  (skills),
  [0051](../05-architecture-decisions/0051-runtime-templates-endpoint.md)
  (runtime-templates),
  [0052](../05-architecture-decisions/0052-import-mcp-tools-catalogo.md)
  (import MCP),
  [0025](../05-architecture-decisions/0025-mcp-tools-y-ejecutores.md)
  (MCP + ejecutores).
- Matriz RBAC: [`docs/04-reference/rbac.md`](../04-reference/rbac.md)
  (sección `agents.py`).
- Comandos/runtime por proyecto:
  [guía](./comandos-y-runtime-por-proyecto.md) · ADR 0045.
- Plan que lo materializa:
  [`docs/roadmap/06.18-tools-overhaul.md`](../roadmap/06.18-tools-overhaul.md).
