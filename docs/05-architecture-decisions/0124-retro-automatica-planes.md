---
title: "ADR 0124: Retrospectiva automática de planes hacia la memoria compartida"
status: accepted
date: 2026-07-19
---

# ADR 0124: Retrospectiva automática de planes

Aprobada por el operador el 2026-07-19 (2ª tanda, «implementa todo»).

## Contexto

A nivel de run el sistema ya aprende de sus fracasos (AUD16-17 memoriza
runs done/failed/aborted/escalados), pero al cerrar un PLAN — la unidad de
cambio — no queda ninguna destilación: cuántas tareas se atascaron, cuántos
runs escalaron, qué costó y qué se aprendió se pierde al pasar al plan
siguiente.

## Decisión

Beat `workers.plan_retro` (cada 15 min): barre los planes `completed`/
`cancelled` de las últimas 48 h sin retro (marker `retro:plan:<id>` en el
Redis del worker, TTL 30 días — sin migración) y para cada uno calcula por
SQL las estadísticas (tareas hechas/canceladas, runs totales/escalados/
abortados, coste, duración), pide al LLM UNA lección breve (fail-open: sin
LLM se persiste la versión estructurada) y la inserta como memoria
`semantic` con scope `project_shared` del proyecto (tag `plan_retro`,
embedding NULL que el back-fill existente indexa). Los agentes del
siguiente plan la recuerdan por el recall normal.

## Consecuencias

- El aprendizaje sube del run al plan sin tocar el pipeline de ejecución.
- Idempotente y fail-open: una retro nunca se pierde por un proveedor
  caído ni se duplica por un reintento (el marker se escribe tras persistir).
- Reuso: redactor del standup (ADR 0120), back-fill de embeddings, recall
  existente. Sin migraciones.
