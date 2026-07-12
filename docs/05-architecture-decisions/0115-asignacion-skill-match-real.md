---
title: "ADR 0115: Asignación skill_match real (rol + skills + proficiency)"
status: proposed
date: 2026-07-12
---

# ADR 0115: skill_match real

## Contexto

La política `skill_match` es un no-op que cae a load-balanced (`dispatch.py`),
y la asignación por idoneidad depende de la preasignación del planner
(ADR 0091). Un pool con especialistas no se aprovecha en dispatch.

## Decisión (propuesta)

Scoring determinista de candidatos: rol de la tarea (spec) igual al rol del
agente (+2), skills requeridas-y-asignadas ponderadas por proficiency (+1 cada
una), empate resuelto por carga. Sin señal, load-balanced (comportamiento
actual). Fase 1 solo rol; fase 2 skills del spec del plan.

## Consecuencias

(+) La palanca declarada por fin opera; mejores matches sin tocar el planner.
(-) Necesita fuente de skills requeridas por tarea (hoy solo hay rol).
