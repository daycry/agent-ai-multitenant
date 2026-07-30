---
title: "ADR 0119: Replay de runs — reproducción paso a paso del steps_log"
status: accepted
date: 2026-07-19
---

# ADR 0119: Replay de runs

Aprobada por el operador el 2026-07-19 (tanda «adelante con todo»).

## Contexto

`executions.steps_log` ya contiene el transcript completo y ordenado de cada
run (nodos del grafo, model_calls con tokens/coste, tool_calls con args y
resultado, `mcp_wire`, self_review). El visor actual lo muestra como lista
estática; para diagnosticar («¿en qué paso se torció?») o enseñar cómo
trabaja un agente, falta poder **reproducirlo en el tiempo**.

## Decisión

En la ficha de ejecución del admin-panel, un modo _replay_: un scrubber de
timeline (play/pausa/velocidad/salto) que reproduce los steps en orden,
resaltando el step activo con su detalle (args, resultado, tokens, coste
acumulado) y los ficheros tocados. Sin backend nuevo: el replay lee el
`steps_log` ya servido por la API de ejecuciones. El estado visual de cada
step sale del MISMO módulo de mapeo puro que la Oficina (ADR 0118,
`lib/office/mapping.ts`): un solo lugar decide qué significa cada `kind`.

## Consecuencias

- Depuración y auditoría paso a paso sin tocar el backend.
- Material de onboarding/demo inmediato (cualquier run histórico es
  reproducible).
- El acoplamiento con la Oficina es solo el módulo puro compartido (testeable
  en aislado); ninguna de las dos superficies depende de la otra.
