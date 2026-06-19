---
adr_id: "0068"
title: "Fork opt-in del equipo al crear un proyecto desde plantilla"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
extends: ["0066"]
related: ["0065", "0053", "0054"]
---

# ADR 0068 — Fork opt-in del equipo al crear un proyecto desde plantilla

> **Estado: `accepted`** (extensión de la Ola C, 2026-06-19; el operador aprobó la
> opción "fork opt-in" frente a "siempre referenciar" o "siempre forkear").
> Reutiliza la maquinaria de [ADR 0066](0066-adopcion-equipos-built-in-deep-copy-fork.md)
> (`fork_team_into`).

## Contexto

Al crear un proyecto desde una plantilla, el proyecto **referenciaba** el `team_id`
de la plantilla (linked): para una plantilla built-in eso significaba compartir el
equipo built-in global, read-only para ese proyecto. El operador preguntó si
debería **forkear** el equipo también.

Tres caminos: (1) **referenciar** siempre (linked, actual) — barato y hereda
mejoras de plataforma, pero no se puede editar el equipo del proyecto; (2)
**forkear** siempre — aislamiento total pero clona N agentes por proyecto aunque
nunca se personalicen (la plantilla CI4 son 10 agentes); (3) **fork opt-in** —
referenciar por defecto, forkear solo si el operador lo pide al crear.

## Decisión

**Opción 3 (fork opt-in).** `ProjectCreateRequest` gana un flag `fork_team`
(default `False`). En `create_project`, si `fork_team=True` Y el proyecto tiene
`team_id`, tras crear el proyecto se **forkea** el equipo referenciado a una copia
editable del tenant (agentes `project_local` atados al nuevo proyecto, KBs/tools/
skills clonadas, enlazada por `forked_from_team_id`) y se **repunta**
`project.team_id` al fork. El equipo original queda intacto.

- **Reutiliza** `fork_team_into` (extraído del endpoint `POST /teams/{id}/adopt`,
  ADR 0066): misma copia profunda, ahora también desde la creación de proyecto.
  `adopt_team` quedó como un wrapper fino sobre `fork_team_into`.
- **Default retro-compatible**: `fork_team=False` referencia el equipo tal cual
  (comportamiento previo). Ningún proyecto existente cambia.
- **Por qué no "forkear siempre"**: la personalización más común —el modelo— ya se
  resuelve sin forkear vía `projects.model_config` y la cadena de herencia
  ([ADR 0065](0065-herencia-model-config-plataforma-proyecto-equipo-agente.md)).
  Forkear de oficio inflaría la base con agentes nunca tocados. Un equipo built-in
  enlazado **sí ejecuta** en el proyecto del tenant (contexto task-scoped del
  [ADR 0054](0054-...md) si existe; los agentes global_builtin son visibles), así
  que "referenciar" no limita la ejecución, solo la edición — y la edición se
  habilita forkeando, on-demand.

## Consecuencias

- **+** El operador elige al crear: equipo compartido (linked, default) o equipo
  propio editable para ese proyecto (fork) — sin coste para quien no personaliza.
- **+** Cero duplicación de lógica: el fork de equipo vive en una sola función
  (`fork_team_into`), usada por adopción (ADR 0066) y por creación de proyecto.
- **−** Dos formas de obtener un equipo forkeado (al crear con `fork_team`, o
  después con el botón "Adoptar"). Es intencional: misma operación, dos entradas.
- **UI**: el wizard de creación de proyecto muestra un checkbox "Personalizar el
  equipo para este proyecto" (default off) que envía `fork_team`.

## Tests

Integración: crear proyecto con `fork_team=True` repunta `team_id` a un equipo
nuevo del tenant (`is_builtin=false`, `forked_from_team_id`=origen) sin mutar el
original; con `fork_team=False` (default) referencia el equipo tal cual. El
forkeo de miembros (persona + KBs/tools/skills) lo cubre el test de adopción
(ADR 0066), que ejercita la misma `fork_team_into`.
