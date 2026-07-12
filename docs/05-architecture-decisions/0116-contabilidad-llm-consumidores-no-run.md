---
title: "ADR 0116: Contabilidad de consumo LLM de los consumidores no-run (asistente, córtex, planning)"
status: proposed
date: 2026-07-12
---

# ADR 0116: Contabilidad LLM de consumidores no-run

## Contexto

El gasto se deriva EXCLUSIVAMENTE de `executions.total_cost_usd`: el asistente
de tenants, el córtex del owner y el chat de planning consumen LLM SIN
contabilizar ni topar — inconsistente con los budgets con auto-pausa del resto
de la plataforma (hallazgo A6, auditoría asistente 2026-07-11).

## Decisión (propuesta)

Nueva tabla `llm_usage_events` (tenant_id nullable para el córtex; source:
assistant|cortex|planning; provider, model, tokens in/out, cost_usd, user_id) +
registro best-effort en los 3 flujos (el usage ya viaja en CompletionResponse)

- suma en el budget del tenant (asistente/planning) con flag para excluirlo.

## Consecuencias

(+) Coste real visible; budgets honestos; base para topar el asistente.
(-) Migración + una escritura por turno (barata); si el chat cuenta contra el
budget del tenant es decisión de producto del operador.
