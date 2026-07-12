---
title: "ADR 0112: Reflexión semántica periódica del agente durante el run"
status: proposed
date: 2026-07-12
---

# ADR 0112: Reflexión semántica periódica durante el run

## Contexto

`reflect` es bookkeeping + nudges heurísticos (regex, contadores). No hay
auto-evaluación del MODELO («¿estoy avanzando hacia los criterios?»): las
derivas se cortan a posteriori (backstops) en vez de detectarse.

## Decisión (propuesta)

Cada K iteraciones (p.ej. 10) un mini-turno de reflexión: el modelo puntúa su
progreso contra los criterios y actualiza su scratchpad (`update_plan`, P1-6).
Si se declara estancado 2 veces seguidas, escalar antes de agotar presupuesto.

## Consecuencias

(+) Corta derivas antes; sinergia natural con el scratchpad.
(-) Coste extra por run (1 llamada/K iter) y riesgo de meta-razonamiento
circular; requiere calibración y telemetría antes de default-ON.
