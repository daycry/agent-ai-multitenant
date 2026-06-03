---
adr_id: "0052"
title: "Importación de tools MCP descubiertas al catálogo (discovery → Tool rows) + namespacing + security_level"
status: accepted
date: 2026-06-03
authors: [system_architect]
plan_referenced: 06.18-tools-overhaul
docs_language: es
---

# ADR 0052 — Importación de tools MCP descubiertas al catálogo

> **Estado: `accepted`** (aprobado por el operador 2026-06-03, Fase 0 del Plan 06.18).
> Implementado por `task_06_18_12`.

## Contexto

El bucle de MCP está **abierto**: el operador configura un MCP server, lo prueba, ve las tools que
expone… y no hay forma de convertirlas en filas asignables, ni de que lleguen al runtime:

- El **discovery es one-shot y no persiste** (`routers/mcp.py:181-185`, `shared-mcp/discovery.py:68-102`
  — "No state is persisted").
- La **asignación exige una fila `Tool` `mcp_tool` preexistente** (`agents.py:770-787`), que no existe
  porque el discovery no la crea → la pestaña "Avanzadas" queda permanentemente vacía.
- **`project.mcp_servers` (JSONB) nunca llega al runtime** (grep en orchestrator/workers = 0;
  `dispatch.py` no lo emite) → aunque se asignara, el runtime no abre sesión MCP y la tool caería en
  `unknown tool`.
- **Colisión de nombres entre servers**: `read_file` puede existir en `filesystem-mcp` y en
  `gdrive-mcp`; el runtime desambigua con prefijo `<server>.<tool>` (`mcp_tools.py:31-34,329`) pero el
  catálogo/UI no contempla ese eje.

## Opciones consideradas

**Persistencia discovery → catálogo:**

- **P-A. Importación manual:** tras `test-connection` exitoso, botón "Importar N tools al catálogo" con
  **multiselección configurable por el operador**; upsert de filas `Tool` `mcp_tool` con `name`
  namespaced `<server>.<tool>`, `category='mcp'`, scoping tenant/proyecto. ✅ El operador controla qué
  entra (supply chain); ✅ el namespacing evita colisiones. ❌ Un paso manual.
- **P-B. Auto-import al guardar el server** (todas las tools descubiertas). ✅ Cero fricción. ❌ Mete en
  el catálogo tools que el operador quizá no quiere; ruido y superficie no deseada.
- **P-C. No persistir** (MCP solo como diagnóstico, tools no asignables). ❌ Deja el bucle abierto.

**`security_level` por defecto de las tools MCP importadas:** (i) `sandboxed` (conservador); (ii)
`safe`; (iii) derivado/manual por el operador en la importación.

## Decisión

**P-A (importación manual) + `security_level` por defecto `sandboxed` (editable):**

1. Tras `test-connection` exitoso, `mcp-servers/page.tsx` ofrece "Importar N tools al catálogo" con
   multiselección; el backend hace **upsert** de filas `Tool` con `implementation_type='mcp_tool'`,
   `implementation_ref='<server>.<tool>'`, `name` namespaced `<server>.<tool>`, `category='mcp'`,
   `security_level='sandboxed'` por defecto (el operador puede ajustarlo), tenant/proyecto-scoped.
2. El **render** muestra el server como badge/prefijo (faceta Origen=MCP del ADR 0049) para que
   `<server>.read_file` no parezca un duplicado de `read_file`.
3. **Threading de `project.mcp_servers` al runtime** (parte de `task_06_18_12`): `dispatch` →
   `ExecutionRequest`/`_agent_spec` → `__main__`; arrancar `MCPToolRunner`, `connect()` por server
   (auth vía Vault), `register_mcp_server` antes del grafo, cerrar en `finally`; respetando la
   intersección con `allowed_tools` y los nombres canónicos (ADR 0048).

Razones: la importación manual respeta el control de supply chain (el operador decide qué tools de
terceros entran en su catálogo); el namespacing resuelve las colisiones que el catálogo no contemplaba;
`sandboxed` por defecto aplica mínimo privilegio a código de terceros (coherente con el aislamiento de
contenedor del sistema), dejando que el operador lo eleve/baje conscientemente.

## Consecuencias

**Mejora:** cierra el bucle "descubres → asignas → se ejecuta"; las colisiones MCP dejan de parecer
duplicados; la config MCP de proyecto por fin llega al runtime.

**Complejidad:** upsert + UI de importación + threading + arranque/cierre de sesión MCP en el runtime.

**Trade-offs:** importación manual (un paso) a cambio de control de qué entra al catálogo; `sandboxed`
por defecto puede requerir que el operador eleve el nivel para algunas tools (consciente, no silencioso).

## Riesgos

| Riesgo                                                      | Prob. | Impacto | Mitigación                                                                   |
| ----------------------------------------------------------- | ----- | ------- | ---------------------------------------------------------------------------- |
| Re-importar duplica filas                                   | Media | Medio   | Upsert idempotente por (tenant, name namespaced); `UNIQUE` (0049)            |
| Una tool MCP de terceros hace algo peligroso                | Media | Alto    | `sandboxed` por defecto + intersección con `allowed_tools` + Vault para auth |
| `mcp_servers` mal threadeado deja la tool en `unknown tool` | Baja  | Medio   | Test de threading + e2e de importación y ejecución (`task_06_18_12`)         |

## Alternativas rechazadas

P-B (auto-import) por meter ruido/superficie no querida; P-C por dejar el bucle abierto. `safe` por
defecto se descarta por aplicarse a código de terceros.

## Trazabilidad

- Roadmap: `docs/roadmap/06.18-tools-overhaul.md` (`task_06_18_12`).
- Backend: `routers/mcp.py`, `routers/tools.py`, `orchestrator/dispatch.py`, `shared-mcp/discovery.py`.
- Runtime: `docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py`.
- Frontend: `apps/admin-panel/app/admin/projects/[id]/mcp-servers/page.tsx`.
- ADRs relacionados: 0025 (MCP+ejecutores), 0048 (nombres canónicos), 0049 (taxonomía/Origen MCP).
