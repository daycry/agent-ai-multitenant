---
title: "Agentes #2 — costura tools→schemas: que el modelo reciba las tools en ejecución"
date: 2026-06-21
status: draft
author: Claude (auditoría audit-memory-subsystems + investigación del agent-runtime)
---

# Agentes #2 — el modelo nunca ve los schemas de las tools

## Problema (confirmado por la auditoría + lectura del código)

En una ejecución real, el agente **no puede invocar ninguna tool** (`memory_recall`,
`rag_search`, `read_file`, `shell_exec`, …) porque el LLM **nunca recibe sus
schemas**. Cadena:

- `__main__.run_task` construye `AgentDeps(model=model_from_spec(spec["model"]), tools=registry, …)`
  (`docker/agent-runtimes/agent-runtime/agent_runtime/__main__.py:308-312`). El
  `registry` (con las tools reales) solo se usa para EJECUTAR en el nodo `act`,
  **no para informar al modelo**.
- `model_from_spec(spec["model"])` → `build_provider_client(spec)` lee
  `tools = spec.get("tools")` (`providers.py:512`). El `model_config` (el sub-dict
  `spec["model"]`) **nunca lleva la clave `tools`** → `tools=None` → el provider
  llama `complete(tools=None)` para los 4 kinds → el modelo no sabe que existen.
- El `ToolRegistry` solo expone `names()`, no schemas (`tools.py`).
- (`claude_sdk` además forzaba FINISH — ya arreglado: **#2b**, commit en
  `feat/builtin-customization`. Y `shared-llm.complete()` ya honra tools — commit
  `0bc524b`. Falta SOLO entregar los schemas al modelo.)

## Decisión de diseño — LOCUS: el worker (fuente única = catálogo api_server)

Construir los schemas OpenAI de las tools **permitidas** del agente en el WORKER
(que tiene acceso al catálogo + a `tool_specs`) e inyectarlos en
`model_spec["tools"]`. El runtime ya los propaga al provider sin cambios
(`build_provider_client` lee `spec.get("tools")`).

Fuentes de schema (sin duplicar definiciones):

- **Builtin** (`memory_recall`, `rag_search`, `read_file`, `http_get`, …):
  `api_server.seeds.builtin_tools.BUILTIN_TOOLS` — cada `BuiltinTool` tiene `slug`,
  `name`, `description`, `input_schema` (JSON Schema). El worker importa
  `api_server` (su imagen es FROM `api-server:ci`), así que es accesible.
- **Asignadas/custom** (`run_*`, `http_endpoint`, `python_function`, MCP):
  `spec["tool_specs"]` ya viajan con `input_schema` (serializadas por
  `serialize_agent_tool_specs`, Plan 06.18).

## Plan de implementación

1. **Helper** `build_model_tool_schemas(allowed_tool_names, tool_specs) -> list[dict]`
   (nuevo módulo, p.ej. `apps/workers/src/workers/agent_tool_schemas.py`):
   - Para cada nombre canónico permitido: buscar en `BUILTIN_TOOLS` (por slug
     canónico) → `{"type":"function","function":{"name","description","parameters":input_schema}}`.
   - Para las `tool_specs` (custom): igual, desde su `input_schema`.
   - Devolver la unión, **intersección con la allowlist efectiva** del agente
     (`combine_tool_allowlists` / `allowed_tools`), deduplicada por nombre.
2. **Inyección**: donde el worker arma el `model_spec`
   (`apps/workers/src/workers/execution.py` `_agent_spec` / `resolve_model_spec` en
   `model_resolver.py`), añadir `model_spec["tools"] = build_model_tool_schemas(...)`
   cuando haya tools permitidas. (Localizar dónde se conoce la allowlist efectiva en
   el momento de armar el spec — probablemente junto a `tool_specs`/`allowed_tools`.)
3. **Runtime**: sin cambios (ya propaga `spec["model"]["tools"]`). Verificar que el
   nodo `act` ejecuta la tool elegida por el modelo (ya existe).
4. **(Opcional) recall proactivo**: el nodo `recall` del grafo del runtime es un
   placeholder `_no_recall→[]` (decisión Plan 04.5/ADR 0024 = vía reactiva). Con la
   tool `memory_recall` ya invocable, la vía reactiva basta; documentar/cerrar por
   ADR si se quiere recall proactivo.

## Tests

- Unit del helper: nombres builtin → schema correcto desde `BUILTIN_TOOLS`;
  tool_specs → schema; intersección con allowlist; dedup.
- Integración: un `model_spec` de un agente con `memory_recall` asignada lleva su
  schema en `model_spec["tools"]`; un provider OpenAI-compat (fake) recibe `tools`
  no vacío y puede emitir un `tool_call` que el runtime ejecuta (`act`).
- Verificar en los 4 providers (claude_sdk vía el fix #2b/0bc524b).

## Despliegue

Rebuild `workers:ci` (arma el spec) + `agent-runtime:v1` ya está (#2b). El catálogo
builtin no cambia.

## Criterio de "hecho"

Un agente con `memory_recall`/`rag_search` asignadas, en ejecución real, RECUPERA
memoria/conocimiento por iniciativa del LLM (invoca la tool), con cualquiera de los
4 providers. Cierra el bucle aprender→recordar junto con #3 (commit `e373273`).
