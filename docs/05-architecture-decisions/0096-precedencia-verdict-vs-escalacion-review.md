---
title: "ADR 0096 — Precedencia entre el verdict del reviewer y la escalación a humano"
status: accepted
date: 2026-07-02
deciders: [operador, auditoría-runs-2026-07-02]
related: ["0087", "0095", "0084"]
---

# ADR 0096 — Precedencia entre el verdict del reviewer y la escalación a humano

## Contexto

Un run de review (ADR 0095) puede terminar de tres maneras relevantes: `done`
(review limpio), `needs_human_review` (el propio run escaló — p. ej.
`self_review_stalemate`) o `aborted`/`failed` (fallo de infra o safeguard). El
pipeline (`_apply_review_verdict`, `apps/workers/src/workers/execution.py`)
parseaba el tag `<verdict>` del output y lo aplicaba **sin mirar cómo terminó el
run**. La auditoría del 2026-07-02 encontró dos consecuencias:

1. Dos tasks pasaron a `done` por el `<verdict>approve</verdict>` de runs que a
   la vez pedían validación humana (`needs_human_review`) — contradicción
   semántica: "escalar a humano" y "cerrar automáticamente" a la vez, y las dos
   executions quedaron como ruido permanente en el inbox de revisión humana.
2. Un run de review `aborted` (research_exhausted) emitió un `<rejection>`
   parseable que se aplicó y **fue beneficioso**: el feedback llegó al
   implementador y el ciclo convergió.

## Decisión

Precedencia **asimétrica por dirección del verdict** (la dirección conservadora
gana):

| Estado del run de review | `<verdict>reject</verdict>`                                 | `<verdict>approve</verdict>`                                                                                                                             |
| ------------------------ | ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `done`                   | se aplica (hoy igual)                                       | se aplica (hoy igual)                                                                                                                                    |
| `needs_human_review`     | **se aplica** (feedback al implementador; dirección segura) | **NO cierra la task** → task a `blocked` (panel de escaladas) con el approve anotado en el audit trail para que el humano lo confirme (`approve_manual`) |
| `aborted` / `failed`     | **se aplica** (caso beneficioso observado)                  | **NO cierra la task** → mismo tratamiento que la fila anterior                                                                                           |

Racional: un reject equivocado cuesta un ciclo más de trabajo (acotado por
`retry_count`/`max_retries`); un approve equivocado cierra la task sin humano y
sin revisión válida — es la única dirección con daño irreversible. Cuando el
run escaló, su approve se degrada a "recomendación" visible para el humano.

La anotación se hace como `task_audit_events` kind `review_comment` con payload
`{"escalated": true, "reason": "escalated_review_approve", "verdict": "approve",
"review_status": ..., "abort_code": ...}` — el panel de escaladas (F1.1,
criterio por estado del último run) la muestra con su historial, y la execution
escalada deja de ser accionable-pendiente en cuanto el humano decide.

## Consecuencias

- Ningún camino automático puede volver a cerrar una task como `done` desde un
  run de review no-`done`.
- El feedback de rejects sigue fluyendo aunque el run de review acabe mal
  (comportamiento que ya demostró converger).
- El humano ve en el panel la task `blocked` con el approve recomendado y lo
  confirma con `approve_manual` (un clic) o relanza con guidance.
