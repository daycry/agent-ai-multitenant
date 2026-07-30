---
title: "ADR 0114: ask_human — pregunta a humano no terminal durante un run"
status: accepted
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

## Estado de implementación (2026-07-12)

IMPLEMENTADO reutilizando la maquinaria de aprobaciones existente (la opcion mas barata y robusta, descubierta en el analisis de diferidos): `ask_human(question, options?)` es una capacidad del LOOP (patron update_plan) anunciada en SYSTEM_TOOL_NAMES; el nodo `plan` la intercepta y PARQUEA el run con category `human_question` (graph.py) -> el worker crea el ApprovalRequest (requires_human_approval bypasa la politica para esa categoria: preguntar a un humano siempre es para un humano) -> task a awaiting_human_approval y agente liberado. El humano RESPONDE desde el inbox de aprobaciones (la UI presenta la pregunta y opciones, el boton pasa a "Responder" y exige texto; la respuesta viaja en `reason`) -> task a BACKLOG -> re-dispatch. El dispatcher lee las Q&A aprobadas (\_read_prior_human_answers, cap 3) y el runtime las pliega como preamble autoritativo (build_human_answers_preamble, fenced) tras los comentarios. El timeout de aprobaciones existente (task_02_27) degrada la pregunta no respondida al escalado terminal historico. El "reloj pausado" sale gratis: al ser re-dispatch, cada run tiene su presupuesto propio.
