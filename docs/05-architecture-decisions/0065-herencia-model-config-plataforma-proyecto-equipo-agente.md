---
adr_id: "0065"
title: "Herencia de model_config en cadena plataforma → proyecto → equipo → agente"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
supersedes: []
extends: ["0055", "0057"]
---

# ADR 0065 — Herencia de `model_config` en cadena plataforma → proyecto → equipo → agente

> **Estado: `accepted`** (Ola A del diseño _personalización de equipos built-in_,
> 2026-06-19). Extiende el [ADR 0055](0055-validacion-model-config.md) (default
> seguro de plataforma + saneo de spec `{}`) y se apoya en el
> [ADR 0057](0057-resolucion-modelo-agentes-por-proveedor.md) (resolución
> provider→ejecutable en el worker). No toca el catálogo cerrado del
> [ADR 0021](0021-proveedores-llm-catalogo-cerrado.md).

## Contexto

Tras el ADR 0055 el modelo de un agente se resolvía en **dos niveles**: si el
agente pineaba `provider`+`model` se usaba su `model_config`; si no (el caso
`{}` legacy o un agente sembrado que solo trae `system_prompts`, como el equipo
built-in de CodeIgniter 4), el dispatch rellenaba con el **default de
plataforma** (`model.default_config` en `platform_settings`).

Esto deja un hueco operativo: **no hay forma de fijar un modelo a nivel de
proyecto ni de equipo**. El operador quería, por ejemplo, "este proyecto cliente
corre todo con Claude" o "el equipo de QA usa un modelo más barato", sin tener
que editar agente por agente (y sin poder hacerlo en los built-in, que no se
editan). El default de plataforma es demasiado grueso; el override por agente,
demasiado fino.

## Decisión

Introducir dos niveles intermedios de `model_config` y resolver el modelo por
una **cadena de herencia, gana el más específico que PINEE `provider`+`model`**:

```
agente  →  equipo (project.team_id)  →  proyecto  →  plataforma (default)
(más específico)                                      (menos específico)
```

- **Esquema**: `teams.model_config` y `projects.model_config`, ambos `JSONB NOT
NULL DEFAULT '{}'::jsonb` (migración `0085`, aditiva y reversible). `{}` = "no
  fija modelo" (hereda del siguiente nivel). Mismo shape que `agents.model_config`.
- **Función de resolución** (`resolve_model_config_chain`, pura, en
  `api_server.db.platform_settings`): si el agente pinea, se devuelve verbatim;
  si no, se baja a equipo, luego proyecto, luego plataforma, tomando el **primer
  nivel que pinee `provider`+`model`**. Ese nivel rellena
  `provider`/`model`/`temperature`; las claves **no-modelo** del agente
  (`system_prompts`) se preservan (merge `{**nivel, **agente}`, idéntico al que
  el ADR 0055 hacía con el default).
- **Un nivel parcial no cuenta**: si un nivel trae solo `provider` (sin `model`)
  se ignora y se baja al siguiente (la condición es la misma
  `config_needs_default_model` del ADR 0055; un spec `kind` _scripted_ tampoco
  entra en la cadena, pasa intacto).
- **Punto de resolución**: el **orchestrator** (`dispatch._route_ai`), que es
  quien ya conoce el proyecto de la tarea. Carga el equipo vía `project.team_id`
  (BYPASSRLS, filtrando por tenant del proyecto como defensa en profundidad) y
  llama a la función pura. El worker (ADR 0057) sigue recibiendo un spec con
  `provider`+`model` y lo resuelve a ejecutable + credencial **sin cambios**: la
  cadena es transparente aguas abajo.

## Alternativas consideradas

1. **Solo proyecto (sin equipo)**: más simple, pero el equipo es la unidad que
   el operador reutiliza entre proyectos (built-in adoptados); fijar el modelo en
   el equipo evita repetirlo en cada proyecto. Rechazada por perder ese nivel.
2. **Resolver en el worker**: el worker no tiene contexto de proyecto/equipo de
   forma barata (resuelve por spec ya armado, ADR 0057). Mantener la cadena en el
   orchestrator respeta esa frontera.
3. **Materializar el modelo efectivo al asignar la tarea**: acoplaría la
   resolución al momento de asignación y quedaría obsoleta si cambia el default;
   resolver en dispatch es _late-binding_ y siempre refleja el estado actual.

## Consecuencias

- **+** El operador fija el modelo en el nivel adecuado (proyecto/equipo) sin
  tocar agentes ni poder editar built-in. Los built-in adoptados heredan el
  modelo del equipo/proyecto que los aloja.
- **+** Cien por cien retro-compatible: filas existentes traen `{}` en los nuevos
  campos → la cadena cae al default de plataforma, comportamiento idéntico al
  previo (cubierto por `test_dispatch_fills_default_model_for_inherit_only_agent`).
- **+** La lógica de precedencia vive en una función pura testeada en aislamiento
  (`test_model_config_chain`, 6 casos) + un test de integración del dispatch
  (`test_dispatch_inherits_team_model_config_over_platform_default`).
- **−** Un nuevo lugar donde mirar al depurar "¿por qué este agente usó este
  modelo?": son cuatro niveles. Mitigado documentando la cadena aquí y dejando el
  _origen_ visible en la UI (Ola D — modelo efectivo + nivel de origen).
- **Pendiente (otras olas)**: exponer `model_config` de equipo/proyecto en la API
  y en la UI (Ola A-UI) y mostrar el modelo efectivo + su origen en las pantallas
  de agente/equipo (Ola D). Este ADR cubre solo el esquema + la resolución.
