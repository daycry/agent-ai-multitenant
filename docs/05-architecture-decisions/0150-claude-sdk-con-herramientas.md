---
adr: "0150"
title: "claude_sdk con herramientas: el cableado ya existe — ¿se mantiene o se bloquea la combinación?"
status: accepted
date: 2026-08-01
deciders: [operador]
phase: prod-07-fiabilidad-llm-costes
related: ["0021", "0086", "0087", "0092", "0097"]
plan_referenced: prod-07-fiabilidad-llm-costes
task: task_prod07_09
# `task_gov_01`: la opcion A retira las mitades (a) y (b) de la casilla — su
# premisa dejo de ser cierta y aplicarlas hoy romperia el proveedor principal.
rejects: [task_prod07_09]
docs_language: es
---

# ADR 0150 — `claude_sdk` con herramientas asignadas

> **Estado: `accepted` (firmado el 2026-08-01).** **Opción A**: se mantiene el
> cableado. Este ADR existe porque `task_prod07_09` pedía lo contrario y la
> premisa de aquella tarea dejó de ser cierta; implementar el bloqueo hoy habría
> roto el proveedor principal de la plataforma. No hay cambio de código: el
> documento registra lo que ya corre.

## Por qué este ADR se escribe al revés de como se planificó

`task_prod07_09` (hallazgo llm-4, `high`) decía, en junio de 2026:

> hoy `ClaudeAgentProvider.complete()` ignora tools/max_tokens/temperature y
> `ClaudeSDKModelClient.decide()` siempre devuelve FINISH: un agente claude_sdk
> con herramientas "completa" la tarea sin actuar y sin warning.

Y por eso pedía cuatro cosas: (a) **bloquear** en validación la combinación
`tools + claude_sdk`, (b) una **nota de limitación** en el catálogo de la UI,
(c) un **timeout** alrededor del `query` del SDK y (d) **este ADR**, con las
opciones A (cablear las tools) y B (mantener el bloqueo).

**Verificado el 2026-08-01 contra el código: (a) y (b) ya no proceden.** La
opción A se implementó — no bajo este plan, sino por el camino de los ADR 0086,
0087, 0092 y 0097, que necesitaban justamente lo contrario del bloqueo:

- `packages/shared-llm/src/shared_llm/providers/claude_agent.py` anuncia los
  schemas de las tools del host como un **servidor MCP in-process** e intercepta
  cada llamada con `can_use_tool` (deny + interrupt), devolviéndola al host en
  `CompletionResponse.tool_calls`. Test que lo fija:
  `packages/shared-llm/tests/test_claude_agent_provider.py::test_complete_emits_tool_calls_when_model_requests_a_tool`.
- `ClaudeSDKModelClient` (`agent_runtime/providers.py`) **hereda** `decide()` de
  `_ProviderModelClient`: ya no devuelve FINISH incondicionalmente, alcanza ACT
  igual que los proveedores OpenAI-compatibles.
- Es, además, el camino **por defecto** de los equipos reales de la plataforma
  (ADR 0092 le da incluso una allowlist de shell propia porque el SDK es agéntico
  nativo).

Implementar hoy el bloqueo de (a) **rompería el proveedor principal de la
plataforma**. Es el modo de fallo nº1 de
`docs/03-guides/verificar-antes-de-implementar.md` §1: una casilla que envejeció
hasta volverse dañina.

Lo único de la tarea que seguía teniendo sentido, (c), está entregado: el
`asyncio.wait_for` por-mensaje alrededor del query del SDK, con 7 tests
(`-k timeout` en el mismo fichero).

## Opciones

### Opción A — mantener el cableado (recomendada)

`claude_sdk` sigue siendo un proveedor de primera clase con herramientas: los
schemas viajan como MCP in-process, el host ejecuta, el grafo LangGraph conduce
los turnos.

- **A favor**: es el estado actual y el que usan los equipos reales; da paridad
  provider-agnóstica (misma forma de `tool_calls` que Copilot/Azure/Ollama); no
  hay nada que construir.
- **En contra**: la mediación por `can_use_tool` **intercepta, no compele** — no
  existe `tool_choice` forzado en el SDK, cosa que los ADR 0086 y 0087 ya
  documentaron y rodearon (por eso `_advertises_submit_result = False` y
  `_forces_verdict_choice = False` en `ClaudeSDKModelClient`). Es una limitación
  conocida y acotada, no un fallo silencioso.

### Opción B — bloquear la combinación en validación (la que pedía el plan)

Un agente con tools asignadas y `kind=claude_sdk` sería rechazado con 422 y la
UI mostraría la limitación en el catálogo.

- **A favor**: sería lo correcto **si** el cableado no existiera.
- **En contra**: hoy dejaría sin despachar a los agentes que funcionan. El
  «riesgo 5» que el propio prod-07 anotaba («Bloqueo de tools+claude_sdk rompe
  agentes existentes») ha pasado de riesgo a certeza.

## Recomendación

**Opción A.** No es una preferencia de diseño: B destruye una capacidad
entregada y verificada. Lo que queda por decidir del asunto original no es «¿se
bloquea?» sino, si acaso, si merece la pena invertir en compeler una tool en el
camino SDK — y eso ya tiene su propia respuesta en los ADR 0086/0087 (no se
puede con el SDK; el contrato de salida se resuelve por prosa + tag).

## Decisión del operador (2026-08-01)

**Opción A — se mantiene el cableado.** El ADR pasa a `accepted`.

La tarea original del plan pedía B (bloquear la combinación en validación), y
ejecutarla habría sido el modo de fallo que `verificar-antes-de-implementar.md`
documenta: implementar una casilla escrita cuando el mundo ya no es el que era
al escribirla. La premisa de aquella tarea —«claude_sdk no sabe usar
herramientas»— dejó de ser cierta cuando el cableado se entregó y se verificó.
Bloquearlo ahora no arreglaría nada: destruiría una capacidad que funciona.

## Consecuencias (opción A, firmada)

- `task_prod07_09` se cierra con (c) + (d); (a) y (b) se retiran del plan por
  obsoletas, anotando el porqué en la casilla (igual que hizo el ADR 0117 (b)
  con `task.human_validation_required`).
- La matriz de capacidades por kind de `docs/04-reference/llm-providers.md`
  (task_prod07_16) debe decir que `claude_sdk` **sí** soporta herramientas, con
  la nota de que la tool no se puede **forzar**.
- No hay cambio de código: este ADR documenta lo que ya corre.
