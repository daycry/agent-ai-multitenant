---
adr: "0097"
title: Sesión persistente del Claude Agent SDK por run (host tools mediados sin interrupt)
status: proposed
date: 2026-07-03
deciders: operador (pendiente)
phase: guardas-research-por-novedad
related: ["0018", "0021", "0064", "0087", "0092"]
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

## Decisión propuesta (pendiente de aprobación)

Mantener **una sesión SDK viva por run** (`ClaudeSDKClient` multi-turno): el system prompt +
task se envían una vez; cada turno del grafo es un mensaje más de la MISMA conversación. Los
host tools se siguen mediando (deny en `can_use_tool` **sin interrupt**, devolviendo el
resultado del host como tool_result a la sesión), preservando ToolRegistry, allowlists,
approval gates y loop-detection del host (ADR 0018/0092).

**Solo claude_sdk**: los providers HTTP conservan el loop actual sin cambios — el contrato
común (nudges, PROGRESS, budgets, steps) NO cambia para nadie; esto es una optimización de
transporte de UN provider (restricción del operador: todo debe funcionar para los 4).

## Opciones evaluadas

| Opción                                                          | Pros                                                                                                              | Contras                                                                                                                                                                                           |
| --------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A. Statu quo** (sesión por turno)                             | simple, probado, paridad total                                                                                    | memoria cero, sin caching, usage frágil                                                                                                                                                           |
| **B. Sesión persistente + mediación sin interrupt** (propuesta) | memoria conversacional total (adiós relecturas), prompt caching (coste ↓ ~60-80 % input), usage por turno íntegro | superficie nueva: vida de la sesión ≈ vida del contenedor; el `can_use_tool` sin interrupt debe devolver resultados host de forma fiable (probar exhaustivamente); divergencia de camino con HTTP |
| C. `run_agent()` nativo del SDK (escape hatch total)            | máxima capacidad agéntica                                                                                         | pierde la mediación host de tools/approvals/guardrails (inaceptable: principios 2 y 10)                                                                                                           |

## Riesgos y mitigaciones (para la implementación, si se aprueba)

- **Sesión colgada** → los timeouts por-turno actuales (900 s × 3) siguen aplicando por mensaje;
  el wall-clock del run no cambia.
- **Context growth de la sesión** → el SDK gestiona su propio contexto; los budgets de tokens
  por-kind (500k) acotan; medir con la instrumentación B1 antes/después.
- **Semántica de deny sin interrupt** → spike previo: verificar que el CLI acepta tool_result
  inyectado tras un deny y continúa el mismo turno (si no, fallback a A sin pérdida).

## Criterio de aceptación

Mismo e2e del plan CI4 con: relecturas/run ↓ (medido vía `safeguard_stats`), coste input/run ↓,
0 regresiones en approval gates y allowlists (suite de seguridad del runtime completa).
