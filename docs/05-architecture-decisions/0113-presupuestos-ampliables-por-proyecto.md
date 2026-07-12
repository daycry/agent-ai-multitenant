---
title: "ADR 0113: Presupuestos de ejecución ampliables por proyecto (techo configurable)"
status: proposed
date: 2026-07-12
---

# ADR 0113: Presupuestos ampliables por proyecto

## Contexto

`projects.execution_budgets` solo puede APRETAR: el clamp al techo hardcoded
(50 iter / 500k tokens / 5 USD / 7200s, `budgets/envelope.py`) impide que un
proyecto pesado pida más margen; los budgets reales por-kind son env del
operador, globales al despliegue.

## Decisión (propuesta)

Techo por PLATAFORMA configurable (platform setting, System Admin) y override
de proyecto que puede subir hasta ese techo (nunca sobre él). El default actual
se conserva; ampliar es una decisión explícita de coste del operador.

## Consecuencias

(+) Proyectos legítimamente pesados dejan de estrellarse contra el techo.
(-) Superficie de coste: pide visibilidad (prod-08) antes o junto a esto.
