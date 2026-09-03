---
adr_id: "0052"
title: "Importación de tools MCP descubiertas al catálogo (discovery → Tool rows) + namespacing + security_level"
status: accepted
date: 2026-06-03
authors: [system_architect]
plan_referenced: 06.18-tools-overhaul
amended_by: ["0166"]
docs_language: es
---

# ADR 0052 — Importación de tools MCP descubiertas al catálogo

> **Estado: `accepted`** (aprobado por el operador 2026-06-03, Fase 0 del Plan 06.18).
> Implementado por `task_06_18_12`.

> **Enmendado por el [ADR 0166](0166-tools-mcp-en-el-catalogo-sin-paso-manual.md) el 2026-09-03.**
> Este ADR **sigue `accepted`** y casi todo él sigue rigiendo: el namespacing
> `<server>.<tool>`, la fila `Tool` como ancla única de gobernanza, `category='mcp'`,
> `implementation_ref`, `security_level='sandboxed'` editable, la faceta Origen=MCP
> (ADR 0049) y el threading de `project.mcp_servers` al runtime. **Lo único que cae es
> que la importación manual sea el _único_ camino**: se enmienda la opción **P-B**
> (§Opciones consideradas), queda **derogada** la primera frase de §Alternativas
> rechazadas y el punto 1 de §Decisión pasa de ser _el_ camino a ser uno de tres.
>
> **Por qué, en dos datos que en junio de 2026 no estaban sobre la mesa.** (1) Un
> servidor MCP declarado cuyas tools nadie importó es un **dead end silencioso**: sin
> fila `Tool` no hay ni permiso ni anuncio (`_project_mcp_tool_rows`), así que el
> operador ve «conexión OK, 12 tools» y el agente contesta que no tiene esa
> herramienta — hallazgo **MK-02 (ALTO)** de la auditoría del 2026-09-02. (2) Con los
> MCP remotos por OAuth (ADR 0127/0131) el flujo manual que decide este ADR no es
> fricción: es **imposible**, porque `discover_tools` no propagaba credencial.
>
> **La objeción que motivó P-A no se descarta, se responde de otra forma**: el tope de
> 200 tools por servidor con **abstención** en vez de truncado (§D9 · L1 del 0166) y el
> recuento de tools importadas visible por servidor (§D4) sustituyen a la prohibición.
> Y el control de supply chain que el clic **sí** protegía sigue en pie: la fila `Tool`
> continúa siendo el ancla de `security_level`, categoría para el gate humano,
> guardrails, allowlist y procedencia. Cambia **quién pulsa**, no qué controla la
> plataforma.

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

  > **[Enmendada por el [ADR 0166](0166-tools-mcp-en-el-catalogo-sin-paso-manual.md), 2026-09-03]** El
  > «❌» sigue siendo un riesgo real, pero **deja de justificar la prohibición**: el 0166 lo acota con
  > límites (200 tools por servidor, y por encima **abstención** con mensaje, nunca truncado) y con
  > visibilidad (recuento de tools importadas por servidor, procedencia estampada en `source_*`, y
  > retirada R3/R4 de lo que el servidor deja de anunciar o de un servidor que se retira del proyecto).
  > Ojo al matiz, porque no es la lectura literal de esta viñeta: lo que el 0166 adopta es el import
  > automático **atado a un acto sobre ese servidor** (un despliegue del marketplace, un «Conectar» de
  > OAuth), no al guardado. **El auto-import al guardar el proyecto sigue rechazado** (§D1 del 0166),
  > y por una razón que en junio de 2026 no estaba medida: la transacción del request permanece abierta
  > durante todo el handler, y meter ahí una llamada de red de hasta 300 s **por servidor** reproduce
  > el hallazgo perf-2/db-2 que cerró prod-13 — además de convertir un servidor caído en «no se puede
  > guardar el proyecto».

- **P-C. No persistir** (MCP solo como diagnóstico, tools no asignables). ❌ Deja el bucle abierto.

**`security_level` por defecto de las tools MCP importadas:** (i) `sandboxed` (conservador); (ii)
`safe`; (iii) derivado/manual por el operador en la importación.

## Decisión

**P-A (importación manual) + `security_level` por defecto `sandboxed` (editable):**

1. Tras `test-connection` exitoso, `mcp-servers/page.tsx` ofrece "Importar N tools al catálogo" con
   multiselección; el backend hace **upsert** de filas `Tool` con `implementation_type='mcp_tool'`,
   `implementation_ref='<server>.<tool>'`, `name` namespaced `<server>.<tool>`, `category='mcp'`,
   `security_level='sandboxed'` por defecto (el operador puede ajustarlo), tenant/proyecto-scoped.

   > **[Enmendado por el [ADR 0166](0166-tools-mcp-en-el-catalogo-sin-paso-manual.md), 2026-09-03]** La
   > multiselección **se conserva íntegra** —el 0166 la deja como uno de los tres llamantes de la
   > función extraída `import_server_tools`—, pero **deja de ser el único camino**: `tool_names` pasa a
   > ser opcional (ausente = «todas las que el servidor anuncie ahora»), el botón sale del diálogo a la
   > tarjeta del servidor —que es donde se ve el hueco— y hace discovery+import **en un solo viaje**, y
   > un despliegue del marketplace lo dispara sin nadie mirando la pantalla. Dos precisiones que este
   > ADR no preveía y que el 0166 añade sobre esta misma línea: el re-import **automático** ya **no
   > toca `security_level`** (sólo el manual, que es donde hay un operador mirando el selector), para no
   > degradar en silencio un `privileged` o un `safe` elegido a mano; y toda ruta automática es un
   > **atajo sobre esta ruta manual, que permanece disponible y visible** — ninguna automática puede ser
   > la única forma de llegar al estado final.

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

~~P-B (auto-import) por meter ruido/superficie no querida~~ — **derogado por el
[ADR 0166](0166-tools-mcp-en-el-catalogo-sin-paso-manual.md) el 2026-09-03**; P-C por dejar el bucle
abierto. `safe` por defecto se descarta por aplicarse a código de terceros.

> **La frase tachada se conserva tal cual se escribió** —es la traza de qué se rechazó y por qué, y
> borrarla dejaría el rechazo sin motivo escrito— pero **ya no rige**. Lo que sustituye a esa
> prohibición está en el 0166: límites con abstención (L1/L2/L3), estado derivado y visible (D4),
> reconciliación auditada (R3/R4) y el import automático atado a un acto sobre ese servidor (D3), no
> al guardado. **Lo demás de esta sección sigue vigente sin matices**: P-C sigue rechazada por dejar
> el bucle abierto, y `safe` por defecto sigue descartada por aplicarse a código de terceros — el
> 0166 ratifica expresamente el `sandboxed` por defecto de este ADR.

## Trazabilidad

- Roadmap: `docs/roadmap/06.18-tools-overhaul.md` (`task_06_18_12`).
- Backend: `routers/mcp.py`, `routers/tools.py`, `orchestrator/dispatch.py`, `shared-mcp/discovery.py`.
- Runtime: `docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py`.
- Frontend: `apps/admin-panel/app/admin/projects/[id]/mcp-servers/page.tsx`.
- ADRs relacionados: 0025 (MCP+ejecutores), 0048 (nombres canónicos), 0049 (taxonomía/Origen MCP).
- **Enmendado por**: [ADR 0166](0166-tools-mcp-en-el-catalogo-sin-paso-manual.md) (2026-09-03) —
  enmienda P-B, deroga la primera frase de §Alternativas rechazadas y convierte el punto 1 de
  §Decisión en uno de tres caminos. Lo implementa `task_mk_01` del plan
  [`remediacion-marketplace-mcp-2026-09-02`](../roadmap/remediacion-marketplace-mcp-2026-09-02.md).
