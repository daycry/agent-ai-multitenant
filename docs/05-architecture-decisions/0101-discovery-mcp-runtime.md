---
adr: "0101"
title: Discovery de tools MCP en runtime vs importación manual
status: proposed
date: 2026-07-03
deciders: operador (pendiente)
phase: auditoria-plataforma-2026-07-03
related: ["0052", "0048", "0049", "0021"]
docs_language: es
---

# ADR 0101 — Discovery de tools MCP en runtime vs importación manual

## Contexto

La auditoría de plataforma 2026-07-03 registró el hallazgo **g2** (veredicto **matizado**) en
`docs/roadmap/tools-y-cierre-plan-fixes.md:40`: «Las tools MCP declaradas-pero-no-importadas son
invisibles al LLM (`agent_tool_schemas.py:197` no hace discovery MCP)». La verificación adversarial
confirmó que **eso no es un defecto**: es exactamente el diseño del **ADR 0052 (`accepted`)**, que
eligió **importación manual (P-A)** y **rechazó el auto-import (P-B) por ruido/superficie no deseada**
y el no-persistir (P-C) por dejar el bucle abierto
(`docs/05-architecture-decisions/0052-import-mcp-tools-catalogo.md:37-42`, `47-65`). El «importar para
ver» es intencional: el operador controla qué tools de terceros entran en su catálogo (supply chain).

**El residuo real, sin embargo, sí es un defecto** y lo destapa la propia auditoría: al importar, el
endpoint **no persiste el `input_schema` descubierto**, de modo que la tool se anuncia al LLM con
parámetros vacíos. La cadena de evidencia en HEAD `3d22337`:

1. `POST /test-connection` **sí descubre y devuelve** el `input_schema` de cada tool
   (`apps/api-server/src/api_server/routers/mcp.py:137` — `DiscoveredTool.input_schema`;
   `routers/mcp.py:257-261` lo proyecta a la respuesta). La UI ya tiene el schema.
2. `POST /servers/{server}/import-tools` lo **descarta**: `ImportMcpToolsRequest` solo transporta
   `tool_names: list[str]` (nombres crudos) + `security_level`
   (`routers/mcp.py:283-284`). El constructor `Tool(...)` fija `name`, `description`, `category`,
   `implementation_type`, `implementation_ref`, `security_level`, `is_builtin` — **pero no
   `input_schema`** (`routers/mcp.py:373-382`).
3. Por tanto `Tool.input_schema` queda en su default de columna `'{}'::jsonb`
   (`apps/api-server/src/api_server/db/domain.py:572-574`).
4. La serialización hacia el runtime emite ese schema tal cual: `_tool_to_spec` copia
   `tool.input_schema` al `ToolSpec` (`apps/api-server/src/api_server/agent_tools_enforcement.py:184`),
   y su propio comentario advierte que `input_schema` **es requerido** para que el LLM sepa que la tool
   existe (`agent_tools_enforcement.py:176-179`).
5. `build_model_tool_schemas` usa ese `input_schema` como `parameters` del schema OpenAI que ve el
   modelo (`apps/workers/src/workers/agent_tool_schemas.py:245-251`). Con `{}`, la tool se anuncia
   **sin propiedades**: el LLM no sabe qué argumentos pasar.

**La asimetría que lo vuelve grave:** en el runtime, `register_mcp_server` conecta en vivo al server y
obtiene el `input_schema` **real** de cada tool (`docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py:390-395`),
y `_make_tool_fn` valida los argumentos de la llamada contra ese schema real vía `_validate_args`
(`mcp_tools.py:91`, `400-429`). Resultado: al LLM se le dice «esta tool no tiene parámetros», pero el
runtime exige los parámetros reales → toda tool MCP con argumentos requeridos se llama mal y el
pre-guard la devuelve como `ToolResult(ok=False)`. La tool importada queda, en la práctica,
**inservible** salvo que no tenga argumentos.

La pregunta de arquitectura que este ADR resuelve: **¿debe el runtime hacer discovery de tools MCP y
anunciarlas al LLM automáticamente (revisando la decisión del ADR 0052), o mantener la importación
manual y limitarse a arreglar la persistencia del schema?**

## Decisión propuesta (pendiente de aprobación)

**Opción A — Mantener la importación manual del ADR 0052 y corregir la persistencia del
`input_schema`.** No se introduce discovery MCP en runtime ni auto-visibilidad de tools no importadas.
El ADR 0052 permanece `accepted`; este ADR ratifica su decisión de producto (control de supply chain vía
importación manual) y sana el defecto de implementación que la volvía inútil:

1. **Persistir el schema descubierto al importar.** `import_mcp_tools` fija `Tool.input_schema` (y
   refresca `description`) con el schema autoritativo de cada tool seleccionada. Fuente del schema, en
   orden de preferencia:
   - **Re-discovery server-side al importar** (recomendado): el backend reejecuta `discover_tools`
     (mismo camino que `test-connection`, con el `VaultResolver`) y toma el `input_schema` de las tools
     cuyo nombre está en la selección. Autoritativo y **no confía en un schema enviado por el cliente**.
   - _Fallback ligero:_ que `ImportMcpToolsRequest` transporte `tools: [{name, input_schema,
description}]` (los objetos que `test-connection` ya devolvió a la UI), evitando una segunda
     conexión a costa de confiar en el cliente.
2. **Upsert idempotente que refresca el schema.** El upsert existente (`routers/mcp.py:384-389`) ya
   actualiza filas re-importadas; se extiende para actualizar también `input_schema`/`description`. Así
   **re-importar = refrescar el schema**, que es la vía de saneamiento ante deriva del server y el
   backfill de las filas ya importadas con `{}`.
3. **Señalización UX (residuo g2), no comportamiento nuevo.** La UI de asignación marca las tools MCP
   **declaradas-pero-no-importadas** con un badge «requiere import» y las no cableadas como no
   asignables — comportamiento del ADR 0052 preservado, solo hecho visible. Esto es la tarea **T6** del
   plan `tools-y-cierre-plan-fixes.md` (`:107-111`).

Con esto la tool importada se anuncia al LLM con su `parameters` real, coincide con lo que el runtime
valida en vivo, y toda la gobernanza del catálogo (namespacing, `security_level`, categoría de gate
g6, guardrails g1) sigue anclada en la fila `Tool`.

## Opciones evaluadas

| Opción                                                                                                        | Pros                                                                                                                                                                                                                                                                                                                                                                                                                    | Contras                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Mantener import manual (ADR 0052) + persistir `input_schema` al importar + badge UX (recomendada)**      | Cambio mínimo y quirúrgico; **respeta la decisión de producto ya tomada** en ADR 0052 (control de supply chain); conserva la gobernanza por fila `Tool` (namespacing ADR 0048/0049, `security_level`, categoría para el gate humano g6, enganche de guardrails g1); arregla el defecto real (schema vacío → tool llamable); no requiere migración (la columna existe); re-import idempotente sirve de backfill/refresh. | Persiste un **snapshot** del schema → puede derivar si el server cambia sus tools (mitigado: re-import refresca; el runtime valida siempre contra el schema vivo, así que la deriva degrada a `ok=False`, no a ejecución incorrecta); sigue habiendo **un paso manual** (por diseño de ADR 0052).                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **B. Discovery automático opt-in por server (flag `auto_import`)**                                            | Menos fricción para servers de confianza; sigue creando filas `Tool` (gobernanza preservada); el schema se persiste en el auto-import.                                                                                                                                                                                                                                                                                  | Reintroduce el **ruido/superficie de P-B** (todas las tools del server entran al catálogo), aunque sea opt-in; añade un flag por server + lógica de **reconciliación** (¿qué pasa cuando el server añade/quita tools? ¿auto-sync? ¿borrado de filas?); **sigue necesitando la corrección de schema de la Opción A** por debajo; más superficie de configuración para un problema que hoy es solo un bug de persistencia. Aplazable como capa futura sobre A.                                                                                                                                                                                                                                                                                          |
| **C. Discovery efímero en runtime, sin persistir (el LLM ve las tools del server conectado sin fila `Tool`)** | Schema **siempre fresco** (cero deriva); cero paso manual; el runtime ya conecta en vivo (`register_mcp_server`, `mcp_tools.py:390-395`), el schema está ahí mismo.                                                                                                                                                                                                                                                     | **Contradice frontalmente el ADR 0052** (revive P-B/P-C rechazados). Sin fila `Tool` no hay `security_level`, ni **categoría para el gate humano** (agrava el fail-open g6), ni enforcement de guardrails por `security_level` (g1), ni allowlist/asignación por agente: el agente vería tools que **nunca se le asignaron**. Rompe el modelo de **catálogo cerrado** y el control multi-tenant de supply chain (principios CLAUDE.md 2, 9, 11). Además el anuncio al LLM se construye **worker-side antes** de que el runtime conecte (`execution.py:434`), así que exigiría reestructurar el orden (el runtime tendría que reconstruir/aumentar el anuncio tras conectar): coste arquitectónico alto para reintroducir una superficie ya rechazada. |
| **D. No hacer nada (statu quo)**                                                                              | Cero trabajo.                                                                                                                                                                                                                                                                                                                                                                                                           | Deja toda tool MCP con argumentos **inservible** (anunciada con `parameters: {}`, rechazada por el pre-guard del runtime); el bucle «descubres → asignas → se ejecuta» que ADR 0052 dice cerrar queda roto en el último tramo. Inaceptable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |

## Consecuencias

**Si se acepta (Opción A):**

- El ADR 0052 sigue `accepted` y sin cambios de política; este ADR **no lo revisa**, lo completa: la
  importación manual gana también la persistencia del schema que le faltaba.
- Cambia `ImportMcpToolsRequest`/`import_mcp_tools` (`routers/mcp.py:270-402`) para fijar
  `Tool.input_schema` y `description` desde el discovery autoritativo, y el upsert para refrescarlos.
- **Sin migración de esquema**: la columna `input_schema` ya existe (`domain.py:572-574`). Las filas MCP
  ya importadas con `{}` se sanean **re-importando** (upsert idempotente) o con un script puntual que
  reejecute `discover_tools` y actualice; hasta entonces siguen anunciándose vacías (degradación
  conocida, no regresión nueva).
- Se coordina con el plan **`tools-y-cierre-plan-fixes.md`**: el badge «requiere import» es su tarea
  **T6** (`:107-111`); la persistencia del schema se añade como corrección de la **Fase B** (junto a
  T6, no es un plan nuevo). La tarea **T2** (`:84-87`, forwardear `category` para que las tools MCP
  sean gateables) es complementaria y ortogonal a este ADR.
- `build_model_tool_schemas` **no se toca**: sigue sin hacer discovery MCP (diseño ADR 0052); el arreglo
  vive aguas arriba, en lo que la fila `Tool` persiste.

**Riesgos y mitigaciones:**

| Riesgo                                                                                  | Prob. | Impacto | Mitigación                                                                                                                                                                                                    |
| --------------------------------------------------------------------------------------- | ----- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| El schema persistido **deriva** del server (el server cambia sus tools)                 | Media | Bajo    | El runtime valida siempre contra el schema **vivo** (`mcp_tools.py:91`), así que la deriva degrada a `ok=False`, nunca a ejecución con args erróneos; re-import refresca; opcional «refrescar schemas» futuro |
| Re-discovery al importar añade una conexión al server (coste/latencia y necesita Vault) | Baja  | Bajo    | Reusar el camino de `test-connection` (ya probado, con `VaultResolver`); fallback: transportar el schema que la UI ya descubrió                                                                               |
| Confiar en un `input_schema` enviado por el cliente (si se elige el fallback)           | Baja  | Medio   | Preferir **re-discovery server-side**; el schema solo alimenta el anuncio al LLM, no baja privilegios (el `security_level` es campo aparte, editable por el operador)                                         |
| Filas ya importadas quedan con `{}` hasta el backfill                                   | Alta  | Bajo    | Documentar el re-import como saneamiento; en dev el número de tools MCP importadas es pequeño                                                                                                                 |

## Criterio de aceptación

1. **Persistencia:** tras importar una tool MCP que declara argumentos, `Tool.input_schema` es el JSON
   Schema descubierto (no `{}`). Test unitario/integración sobre `import_mcp_tools` que lo afirma sobre
   la fila persistida.
2. **Idempotencia/refresh:** re-importar la misma tool con un schema cambiado **actualiza**
   `Tool.input_schema` sin duplicar filas (respetando `UNIQUE(tenant_id, name)`, `domain.py:543-549`).
3. **Anuncio correcto al LLM:** el `ToolSpec` serializado (`_tool_to_spec`, `agent_tools_enforcement.py:184`)
   lleva el schema real, y `build_model_tool_schemas` anuncia la tool con `parameters` no vacíos.
4. **e2e:** en una ejecución de agente con una tool MCP asignada que requiere args, el LLM la llama con
   argumentos válidos y **pasa** el pre-guard `_validate_args` del runtime (`mcp_tools.py:91`) — sin el
   `ok=False` por «invalid args» que hoy provoca el schema vacío.
5. **UX (residuo g2):** una tool MCP declarada-pero-no-importada muestra el badge «requiere import» y no
   es asignable (T6 del plan).
6. **No regresión de política:** el ADR 0052 permanece `accepted`; ninguna tool MCP llega al LLM sin
   una fila `Tool` importada (no se introduce discovery en runtime); un test de contrato lo verifica.
