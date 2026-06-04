---
title: Referencia de tools — catálogo built-in, taxonomía y effective-tools
audience: backend-dev, frontend-dev, technical-writer, system-admin
phase: 06.18-tools-overhaul
updated: 2026-06-04
docs_language: es
---

# Referencia de tools

Página de referencia del subsistema de tools tras el Plan 06.18: la
**fuente única de nombres**, el **catálogo built-in** (15 tools), la
**taxonomía de tres facetas**, el flag **`is_runtime_wired`** y el
contrato **`effective-tools`**. La guía de uso está en
[Asignar tools a un agente](../03-guides/asignar-tools-a-agentes.md).

> Fuente de verdad en código:
> [`packages/shared-domain/src/shared_domain/tool_names.py`](../../packages/shared-domain/src/shared_domain/tool_names.py)
> (nombres canónicos + alias + set cableado) y
> [`apps/api-server/src/api_server/seeds/builtin_tools.py`](../../apps/api-server/src/api_server/seeds/builtin_tools.py)
> (las 15 filas built-in). Un test de contrato (`task_06_18_14`) las
> mantiene en lock-step.

## Fuente única de nombres (ADR 0048)

Tres capas usaban nombres divergentes para la misma acción: el catálogo
(`read_file`), los chat-modes (`file_read`) y el runtime (`file_read`).
`shared_domain.tool_names` unifica:

- **`CANONICAL_TOOL_NAMES`** — los nombres canónicos (= los del
  catálogo) + las tres tools de orquestación (`kanban_update`,
  `task_comment`, `agent_invoke`).
- **`_ALIAS_TO_CANONICAL`** — alias legacy → canónico. Retrocompatible
  (no rename duro):

  | Alias (legacy)    | Canónico                 |
  | ----------------- | ------------------------ |
  | `file_read`       | `read_file`              |
  | `file_write`      | `write_file`             |
  | `file_list`       | `list_files`             |
  | `http_request`    | `http_get` + `http_post` |
  | `notify_user`     | `send_notification`      |
  | `semantic_search` | `rag_search`             |

- **`to_canonical()` / `to_canonical_set()`** — resuelven un nombre (o
  colección) a su(s) canónico(s); un nombre no aliased (custom / MCP
  `<server>.<tool>`) resuelve a sí mismo. `combine_tool_allowlists`
  calcula la intersección agente∩modo **sobre el espacio canónico**.

## Catálogo built-in — 15 tools

La familia `git` se retiró (ADR 0049): sin ejecutor de runtime.

| `name`              | Función (`category`) | Seguridad      | `implementation_type` | `is_runtime_wired`  |
| ------------------- | -------------------- | -------------- | --------------------- | ------------------- |
| `read_file`         | file                 | safe           | builtin               | ✅                  |
| `write_file`        | file                 | sandboxed      | builtin               | ✅                  |
| `apply_patch`       | file                 | sandboxed      | builtin               | ❌ (sin ejecutor)   |
| `list_files`        | file                 | safe           | builtin               | ✅                  |
| `search_code`       | file                 | safe           | builtin               | ❌ (sin ejecutor)   |
| `run_pytest`        | runtime              | sandboxed      | docker_command        | ✅                  |
| `run_lint`          | runtime              | sandboxed      | docker_command        | ✅                  |
| `run_typecheck`     | runtime              | sandboxed      | docker_command        | ✅                  |
| `run_build`         | runtime              | sandboxed      | docker_command        | ✅                  |
| `http_get`          | network              | sandboxed      | http_endpoint         | ✅                  |
| `http_post`         | network              | sandboxed      | http_endpoint         | ✅                  |
| `semantic_search`   | knowledge            | safe           | builtin               | ✅ (→ `rag_search`) |
| `summarize_text`    | knowledge            | safe           | builtin               | ❌ (sin ejecutor)   |
| `send_notification` | notification         | sandboxed      | python_function       | ✅                  |
| `shell_exec`        | command              | **privileged** | builtin               | ✅ (por proyecto)   |

Tools de **orquestación** cableadas por el runtime bajo el mismo nombre
en todas las capas (no están en el catálogo seedeado, pero el runtime las
registra y son canónicas): `kanban_update`, `task_comment`,
`agent_invoke`. Familias de **conocimiento/memoria** adicionales que el
boot registra cuando hay token de api-server: `document_convert`,
`promote_to_kb`, `memory_recall`, `memory_store`.

## Taxonomía de tres facetas (ADR 0049)

Tres ejes **ortogonales**; la UI los sirve con etiqueta ES+EN desde
`apps/admin-panel/lib/tools/taxonomy.ts`:

- **Función** (`category`): `file`, `runtime`, `network`, `knowledge`,
  `notification`, `command`, `mcp`, …
- **Seguridad** (`security_level`): `safe`, `sandboxed` (Aislada),
  `privileged` (Privilegiada). `CHECK` en `tools` (task_06_18_04).
- **Origen** (deriva de `is_builtin` / `mcp_tool`): **Plataforma**
  (built-in) · **Tenant** (custom) · **MCP** (`<server>.<tool>`).
  "Básica vs avanzada" = la proyección de Origen sobre `is_builtin`.

`security_level` es ortogonal a Origen: una básica puede ser `privileged`
(`shell_exec`); una avanzada puede ser `safe`.

## `is_runtime_wired` (ADR 0049)

Flag derivado en `ToolResponse`: ¿el runtime sabe ejecutar esta tool
**hoy**? Fuente de verdad: `RUNTIME_WIRED_TOOL_NAMES`. Resuelve el nombre
por la capa de alias primero (así `semantic_search` cuenta vía
`rag_search`). Las tools **custom** cuentan como ejecutables por su
`implementation_type` (no por este set built-in). El `PUT
/agents/{id}/tools` avisa/`422` si se asigna un nombre no ejecutable.

## Endpoint `GET /agents/{id}/effective-tools`

Contrato de frontera con el Plan 06.17. Read-only, tenant-scoped (404 en
agente oculto/inexistente). Query opcional `mode` (chat mode contra cuyo
allowlist se intersecta; un modo desconocido → 422).

Respuesta (`EffectiveToolsResponse`):

| Campo                  | Tipo                   | Significado                                                                                              |
| ---------------------- | ---------------------- | -------------------------------------------------------------------------------------------------------- |
| `agent_id`             | UUID                   | El agente.                                                                                               |
| `mode`                 | string \| null         | Modo contra el que se computó (null = sin restricción de modo).                                          |
| `assigned`             | `EffectiveToolEntry[]` | Cada asignación con `canonical_names`, faceta y `executable_in_runtime`.                                 |
| `effective`            | `string[]`             | Conjunto canónico realmente cableado = `(asignadas ∩ modo) ∩ runtime-wired` (+ `shell_exec` si procede). |
| `unrestricted`         | bool                   | `true` si el agente no tiene asignaciones (mantiene su superficie por defecto).                          |
| `shell_exec_effective` | bool                   | `true` sólo si `shell_exec` asignado **y** `allowed_commands` del proyecto no vacío.                     |
| `warnings`             | `string[]`             | Avisos legibles (set vacío en modo X; asignada pero no ejecutable; shell_exec sin allowed_commands).     |

`EffectiveToolEntry`: `tool_id`, `name`, `canonical_names`, `category`,
`implementation_type`, `security_level`, `is_builtin`,
`executable_in_runtime`.

## Endpoint `GET /runtime-templates` (ADR 0051)

Proyecta `shared_test_runtimes.CATALOG` (14 templates) con label **ES+EN**,
`dep_cache_mount` y `network_policy`. Project-agnostic, tenant-agnostic
(cualquier miembro autenticado lo lee). Elimina el triple-hardcodeo del
frontend; `default_runtime_template` se valida con `field_validator` en
`ProjectCreate`/`Update` (id fuera del catálogo → 422). Detalle en
[Comandos y runtime por proyecto](../03-guides/comandos-y-runtime-por-proyecto.md).

## Deduplicación (ADR 0049)

- `UNIQUE (tenant_id, name) WHERE deleted_at IS NULL` en `tools`
  (migración reversible). `create_tool`/`update_tool` devuelven **409**
  ante colisión con built-in u otra tool del tenant; `name` se normaliza
  a slug-case.
- `CHECK`/enum sobre `category` / `security_level` / `implementation_type`.

## Cableado del runtime (ADR 0048/0049)

`agent_runtime.__main__.run_task` construye un `WiringContext` y registra
**todas** las familias bajo su nombre **canónico**
(`register_builtin_families`: file / red / orquestación / notificación /
conocimiento / memoria) + los `run_*` (`docker_command`, vía
`register_tool_specs` desde los `tool_specs` que serializa el orquestador)

- `shell_exec` (por proyecto, desde `allowed_commands`) + MCP
  (`register_mcp_server` por cada server de `project.mcp_servers`). Cada
  familia se puede desactivar con `AGENT_TOOL_FAMILY_<FAMILIA>` (default
  habilitada). El punto **único** de intersección agente∩modo es
  `combine_tool_allowlists`.

## Referencias

- Guía: [`docs/03-guides/asignar-tools-a-agentes.md`](../03-guides/asignar-tools-a-agentes.md).
- ADRs: 0044, 0048, 0049, 0050, 0051, 0052, 0025 (en
  `docs/05-architecture-decisions/`).
- Plan: [`docs/roadmap/06.18-tools-overhaul.md`](../roadmap/06.18-tools-overhaul.md).
- Changelog: [`docs/07-changelog/06.18-tools-overhaul.md`](../07-changelog/06.18-tools-overhaul.md).
