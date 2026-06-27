---
title: "Refactor del pipeline ejecución + self-review (autoritativo + finish estructurado)"
date: 2026-06-27
adr: ["0087", "0086"]
status: implemented
docs_language: es
---

# Refactor del pipeline ejecución + self-review

Rediseño limpio (sin parches) del pipeline ejecución→self-review del
`agent-runtime`, a partir de un análisis multi-agente y tres decisiones del
operador. Spec: `docs/roadmap/refactor-pipeline-ejecucion-review.md`.

## Cambios

- **Self-review autoritativo de 3 estados** (ADR 0087): verdict
  `pass / fail / inconcluso` con orden canónico (tool-call `submit_verdict` >
  JSON > marcadores de prosa conservadores como único último recurso). Polaridad
  **fail-closed** (se acabó el aprobar-por-defecto la prosa ambigua).
- **Escalado a humano**: un veredicto inconcluso, o el presupuesto de retries
  agotado, emite `execution.status = needs_human_review` (motivo en `abort_code`)
  y el worker lleva la tarea a `blocked`, preservando el deliverable y
  surfaceándolo por la bandeja humana. Sustituye el abort
  `max_review_retries_exceeded` por una escalada que no descarta el trabajo.
- **Finish estructurado** (`submit_result(status, summary)`, ADR 0086 Fase 2
  des-diferida): advertido en `decide()` solo en los providers HTTP; `claude_sdk`
  finaliza en prosa (no se le fuerza, su `content=""` tiraría el output) y el wrap
  lo envuelve. `_decision_from` enruta por **nombre** de tool
  (`submit_result → FINISH`). `finish_status` es un HINT (no veredicto).
- **El reviewer ve los `acceptance_criteria`** (antes no se le pasaban) + el
  `finish_status` como pista.
- **Persistencia + UI**: nueva columna `executions.finish_status` (migración 0100,
  aditiva/reversible) + `ExecutionStatus.NEEDS_HUMAN_REVIEW`. La página de detalle
  del run ahora **pinta el output** (antes se fetcheaba y no se mostraba) y muestra
  el badge "Resultado del agente".

## Notas

- **Descartado por over-engineering**: la sección de texto "Resultado final"
  parseada por regex (recreaba la colisión de dominio de los marcadores), y un
  `response_schema` genérico en `shared-llm` (el review ya es tool-based en los 4
  providers; forzar no es implementable en el SDK).
- El `_parse_verdict` tolerante permanece como **red de seguridad documentada**
  (invariante): el CLI puede degradar a prosa.

## Commits

`f6b1a94` (A+A2 verdict 3-estados + escalado) · `5e4752c` (B pin claude_sdk) ·
`bcdd9cb` (C0–C3 finish estructurado runtime) · `77f37fe` (persistencia + UI) ·
ADRs 0086 (actualización) + 0087 (nuevo).
