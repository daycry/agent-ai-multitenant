---
title: "ADR 0113: Presupuestos de ejecución ampliables por proyecto (techo configurable)"
status: accepted
date: 2026-07-12
---

# ADR 0113: Presupuestos ampliables por proyecto

> **Resolución 2026-07-12**: IMPLEMENTADO como multiplicador de techo por
> plataforma (`execution.budget_ceiling_multiplier`, 1.0–4.0, System Admin);
> el override de proyecto puede pedir hasta techo×multiplicador. El wall-clock
> queda exento (lo mata el timeout del contenedor del worker).

## Contexto

`projects.execution_budgets` solo puede APRETAR: el clamp al techo hardcoded
(50 iter / 500k tokens / 5 USD / 7200s, `budgets/envelope.py`) impide que un
proyecto pesado pida más margen; los budgets reales por-kind son env del
operador, globales al despliegue.

## Decisión

Techo por PLATAFORMA configurable (platform setting, System Admin) y override
de proyecto que puede subir hasta ese techo (nunca sobre él). El default actual
se conserva; ampliar es una decisión explícita de coste del operador.

## Consecuencias

(+) Proyectos legítimamente pesados dejan de estrellarse contra el techo.
(-) Superficie de coste: pide visibilidad (prod-08) antes o junto a esto.
