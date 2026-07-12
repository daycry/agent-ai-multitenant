---
title: "ADR 0114: ask_human — pregunta a humano no terminal durante un run"
status: proposed
date: 2026-07-12
---

# ADR 0114: ask_human no terminal

## Contexto

El escalado actual es TERMINAL (blocked + inbox): una ambigüedad pequeña
cuesta el run entero. No hay canal para preguntar y continuar.

## Decisión (propuesta)

Tool `ask_human(question, options?)`: el run se SUSPENDE (estado reanudable o
re-dispatch con la respuesta en el preámbulo), la pregunta llega al inbox +
notificación, y la respuesta reanuda el run como sticky. Timeout configurable
que degrada al escalado terminal actual.

## Consecuencias

(+) Tareas ambiguas sobreviven; menos blocked por nimiedades.
(-) Toca ciclo de vida de ejecuciones (suspensión/reanudación), UI de inbox,
presupuestos (el reloj se pausa) y el reaper. Cambio de producto: gated a ADR.
