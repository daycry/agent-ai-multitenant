---
adr: "0097"
title: Sesión persistente del Claude Agent SDK por run (host tools mediados sin interrupt)
status: accepted
date: 2026-07-03
decided_at: 2026-07-13
deciders: operador
phase: guardas-research-por-novedad
related: ["0018", "0021", "0064", "0087", "0092", "0110"]
docs_language: es
---

# ADR 0097 — Sesión SDK persistente por run

## Contexto

Hoy cada `decide()` de un run claude_sdk abre una **sesión NUEVA** del Claude Agent SDK
(one-shot: prompt completo → captura de tool call vía `can_use_tool` deny+interrupt →
sesión descartada). Consecuencias medidas en la auditoría 2026-07-02/03:

1. **Memoria cero entre turnos**: el modelo no recuerda lo que leyó hace 2 turnos → relecturas
   (read-churn) que todo el sistema de guardas/digests (plan guardas-research-por-novedad)
   existe para compensar.
2. **Coste**: cada turno re-envía todo el contexto SIN prompt caching entre sesiones
   (~4,5k tokens/turno medidos; un run de 20 turnos paga ~100k tokens de input repetido).
3. **Usage frágil**: el interrupt corta el `ResultMessage` y pierde el usage del turno
   (fix multi-canal F1.4, pero la causa raíz es el interrupt).

## Decisión

Mantener **una sesión SDK viva por run** (`ClaudeSDKClient` multi-turno): el system prompt +
task se envían una vez; cada turno del grafo es un mensaje más de la MISMA conversación. Los
host tools se siguen mediando (deny en `can_use_tool` **sin interrupt**), preservando
ToolRegistry, allowlists, approval gates y loop-detection del host (ADR 0018/0092).

**No es una vía exclusiva del SDK** (restricción del operador): el _hilo conversacional por
run_ es **UNA capacidad del contrato con DOS transportes**, y este ADR es la mitad que le
faltaba al ADR 0110:

| Transporte                            | Cómo se mantiene el hilo                                                                                        |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| HTTP (azure_foundry, copilot, ollama) | El cliente **re-envía el hilo de mensajes** cada turno; el proveedor reusa su KV-cache (ADR 0110).              |
| claude_sdk                            | El proveedor mantiene una **sesión SDK viva**; solo se le manda el mensaje nuevo (el historial ya vive dentro). |

Los cuatro comparten flag (`conversation_thread` del spec, desde
`WORKERS_RUNTIME_CONVERSATION_THREAD`), contrato (`LLMProvider.complete()`, un ACT por turno,
la observación del host en el turno siguiente) y comportamiento por defecto (**OFF**). El
resto del sistema no distingue el transporte.

## Opciones evaluadas

| Opción                                                        | Pros                                                                                                              | Contras                                                                                                                                                                                           |
| ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Statu quo** (sesión por turno)                           | simple, probado, paridad total                                                                                    | memoria cero, sin caching, usage frágil                                                                                                                                                           |
| **B. Sesión persistente + mediación sin interrupt** (ELEGIDA) | memoria conversacional total (adiós relecturas), prompt caching (coste ↓ ~60-80 % input), usage por turno íntegro | superficie nueva: vida de la sesión ≈ vida del contenedor; el `can_use_tool` sin interrupt debe devolver resultados host de forma fiable (probar exhaustivamente); divergencia de camino con HTTP |
| C. `run_agent()` nativo del SDK (escape hatch total)          | máxima capacidad agéntica                                                                                         | pierde la mediación host de tools/approvals/guardrails (inaceptable: principios 2 y 10)                                                                                                           |

## Spike de habilitación (2026-07-13) — VEREDICTO: PASA

El ADR exigía un spike con credencial viva antes de comprometerse. Ejecutado dentro del
contenedor `api-server` (CLI `claude` real + fila `llm_providers` kind=claude_sdk + secreto de
Vault; `claude-agent-sdk` 0.2.116). Dos variantes, ambas verdes:

| Pregunta                                                                                | Resultado                                                                     |
| --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| ¿El deny **sin interrupt** con el resultado del host embebido continúa el MISMO turno?  | **Sí** — el modelo usó el número inyectado y cerró el turno.                  |
| ¿El deny **sin interrupt** de tipo «lo ejecuta el host, espera» cierra el turno limpio? | **Sí** — 1 solo deny, sin reintentos, `ResultMessage` completo.               |
| ¿La misma sesión recuerda el turno anterior?                                            | **Sí** — respondió con el dato del turno 1 sin volver a llamar a la tool.     |
| ¿Hay prompt caching intra-sesión?                                                       | **Sí** — `cache_read_input_tokens` ≈ 45k en los turnos 2+.                    |
| ¿Sobrevive el usage del turno con tool call?                                            | **Sí** — el interrupt era justamente lo que lo cortaba (causa raíz del F1.4). |

**Variante implementada: la segunda** (deny «lo ejecuta el host, termina el turno»). Preserva
el contrato del grafo tal cual: la tool la ejecuta el HOST con su ToolRegistry / approvals /
loop-detection, y su observación viaja en el siguiente mensaje del hilo — que es exactamente
el mensaje que el grafo ya envía hoy. La primera variante (resultado embebido en el deny)
funcionaría, pero movería la ejecución de tools dentro de la sesión y difuminaría la
mediación: se descarta por principio, no por capacidad.

## Riesgos y mitigaciones

- **Sesión colgada** → los timeouts por-turno actuales (900 s × 3) siguen aplicando por mensaje;
  el wall-clock del run no cambia.
- **Sesión muerta a mitad de run** → un turno fallido **destruye** la sesión y el siguiente la
  reabre con el historial completo (el cliente sigue trayendo el hilo entero en `messages`):
  auto-sanado sin pérdida de contexto.
- **Loop de la sesión** → el runtime puentea cada llamada con `asyncio.run()` (loop nuevo por
  turno) y el transporte del SDK queda ligado al loop que lo creó, así que la sesión vive en un
  **event loop de fondo propio** (mismo patrón que el runner MCP) y se cierra al acabar el run.
- **Context growth de la sesión** → el SDK gestiona su propio contexto; los budgets de tokens
  por-kind (500k) acotan; medir con la instrumentación B1 antes/después.

## Criterio de aceptación

Mismo e2e del plan CI4 con: relecturas/run ↓ (medido vía `safeguard_stats`), coste input/run ↓,
0 regresiones en approval gates y allowlists (suite de seguridad del runtime completa).

## Estado

**Implementado (2026-07-13), flag OFF.** `ClaudeAgentSessionProvider`
(`packages/shared-llm/.../claude_agent_session.py`) + selección de transporte en
`ClaudeSDKModelClient` + cierre del proveedor al acabar el run. Se activa con
`WORKERS_RUNTIME_CONVERSATION_THREAD` (la MISMA flag que enciende el hilo de los tres
transportes HTTP): encenderla es ya una decisión de operación, no de arquitectura.
