---
title: "ADR 0112: Reflexión semántica periódica del agente durante el run"
status: accepted
date: 2026-07-12
---

# ADR 0112: Reflexión semántica periódica durante el run

## Contexto

`reflect` es bookkeeping + nudges heurísticos (regex, contadores). No hay
auto-evaluación del MODELO («¿estoy avanzando hacia los criterios?»): las
derivas se cortan a posteriori (backstops) en vez de detectarse.

## Decisión (aceptada — fase 1 implementada 2026-07-12)

**Fase 1 (elegida e implementada)**: el self-check viaja como sticky
`self_check_nudge` en el turno NORMAL de decide() cada K=10 iteraciones
(`_SELF_CHECK_EVERY`, graph.reflect): el modelo puntúa su progreso 0-10
contra los criterios, refresca su scratchpad (`update_plan`, P1-6) y, si es
su segundo self-check consecutivo sin avance real, cierra con
`submit_result status='failed'` explicando el bloqueo (escalado temprano).
Fuera de cadencia el sticky se limpia. Instrumentado como
`nudge:self_check` en `safeguard_stats`.

**Fase 2 (diferida)**: el mini-turno LLM DEDICADO de reflexión (con
puntuación estructurada y escalado determinista tras 2 estancamientos) queda
pendiente de que la telemetría de fase 1 muestre que el nudge no basta.

## Consecuencias

(+) Corta derivas antes; sinergia natural con el scratchpad; fase 1 tiene
COSTE CERO (cero llamadas LLM extra) y cero riesgo de meta-razonamiento
circular — el modelo decide en su turno normal con la instrucción presente.
(-) La fase 1 no puede FORZAR el escalado (depende de que el modelo obedezca
la instrucción); los backstops existentes (racha estéril, presupuestos)
siguen siendo el techo determinista.
