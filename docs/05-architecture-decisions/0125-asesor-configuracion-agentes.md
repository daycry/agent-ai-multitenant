---
title: "ADR 0125: Asesor de configuración de agentes (leaderboard → propuesta con gate humano)"
status: accepted
date: 2026-07-19
---

# ADR 0125: Asesor de configuración de agentes

Aprobada por el operador el 2026-07-19 (2ª tanda, «implementa todo»).

## Contexto

El leaderboard (ADR 0121) muestra qué combinación modelo×agente rinde
mejor con la carga real, pero mirar el ranking y actuar sigue siendo
manual. El bucle datos→decisión merece cerrarse SIN ceder el control:
cambiar el modelo de un agente es y debe seguir siendo una decisión humana
(herencia de modelo, ADR 0055/0082).

## Decisión

Beat semanal `workers.config_advisor` (lunes 07:00 UTC): agrega los runs
de 30 días por (agente, modelo) — misma agregación que el leaderboard — y
emite una PROPUESTA (`config_proposal`, in_app) solo cuando se sostiene con
datos: la combinación actual (la de mayor volumen) tiene n≥5 con éxito
≤60% Y otra combinación del MISMO agente tiene n≥5 con éxito ≥ actual+25
puntos. La notificación lleva agente, de→a modelo y la evidencia numérica.
Nada se aplica automáticamente, nunca.

## Consecuencias

- El operador recibe recomendaciones accionables basadas en SU carga, sin
  vigilar el ranking; el control queda intacto (gate humano total).
- Umbrales conservadores → pocas propuestas y con fundamento; ajustables en
  el módulo si el operador quiere más sensibilidad.
