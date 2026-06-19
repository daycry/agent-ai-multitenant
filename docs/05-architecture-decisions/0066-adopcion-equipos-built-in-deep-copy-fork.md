---
adr_id: "0066"
title: "Adopción de equipos built-in por copia profunda + enlaces de fork"
status: accepted
date: 2026-06-19
authors: [system_architect]
plan_referenced: personalizacion-equipos-built-in
docs_language: es
extends: ["0065"]
related: ["0026", "0049", "0050", "0053"]
---

# ADR 0066 — Adopción de equipos built-in por copia profunda + enlaces de fork

> **Estado: `accepted`** (Ola C del diseño _personalización de equipos built-in_,
> 2026-06-19). Reusa la maquinaria de **fork por-agente** (clonado de
> persona + KBs/tools/skills + `forked_from_*`) y se apoya en el
> [ADR 0065](0065-herencia-model-config-plataforma-proyecto-equipo-agente.md)
> (herencia de modelo) para fijar opcionalmente el modelo del equipo adoptado.

## Contexto

Los equipos y agentes built-in son **read-only** (globales, del tenant
plataforma). Hasta ahora el único mecanismo de personalización era el **fork
por-agente** (`POST /agents/{id}/fork`), que copia un agente a un proyecto. No
existía forma ergonómica de **adoptar el equipo entero**: el operador tenía que
forkear agente por agente y recomponer el equipo a mano, perdiendo la
composición (roles, líder, prioridades) del built-in.

## Decisión

Añadir **`POST /teams/{source_id}/adopt`**: crea una **copia profunda editable**
del equipo en el tenant, enlazada al origen para diff/re-sync futuros.

- **Esquema (migración `0086`, aditiva y reversible)**: `teams.forked_from_team_id`
  (FK self a `teams.id`, `ON DELETE SET NULL`) + `teams.forked_from_version`
  (timestamp ISO del origen al adoptar) + índice parcial. Espejo exacto de los
  campos `forked_from_*` de `agents`.
- **Body**: `{ target: "project"|"tenant", project_id?, name?, model_config? }`.
  `target` decide el **scope** de los agentes forkeados:
  - `project` → `project_local` (requiere `project_id` del tenant; 404 si no es
    suyo o es plantilla).
  - `tenant` → `global_tenant_template` (sin `project_id`).
- **Transacción (tenant-scoped, RLS)**:
  1. Crea `Team` del tenant (`is_builtin=false`, `forked_from_team_id`/`version`,
     `model_config` = el elegido al adoptar → engancha con la cadena del ADR 0065).
  2. Por cada miembro del origen, **forkea el agente** (persona + `model_config` +
     clona KBs/tools/skills vía el helper de fork por-agente, `forked_from_agent_id`
     al origen) con el scope destino.
  3. Recrea el `TeamMember` (rol, líder, `assignment_priority`) → agente forkeado.
- **Visibilidad / multi-tenant**: el origen built-in es visible por la policy de
  SELECT (`teams_builtin_read`); `team_members` no tiene RLS (visible) y los
  agentes built-in se exponen por su policy de lectura, así que la lectura del
  roster origen es segura. Las filas creadas (team, agentes, miembros, KBs) llevan
  el `tenant_id` del que adopta. Las KBs de rol del origen built-in (RLS por
  tenant, ADR 0026) **no** se arrastran: el fork no las ve, coherente con el fork
  por-agente.
- **Re-adopción permitida**: cada llamada crea copias nuevas, distinguibles por
  `forked_from_team_id`. El built-in original **no se muta**.

## Alternativas consideradas

1. **Overlay / referencia en runtime** (el equipo adoptado "apunta" al built-in y
   solo guarda los deltas): descartada por complejidad de runtime/RLS y por la
   dificultad de razonar sobre el estado efectivo. La copia profunda es explícita
   y depurable.
2. **Reusar `POST /agents/{id}/fork` por miembro desde el cliente**: deja la
   recomposición del equipo (roles/líder/prioridades) en manos del frontend, con
   N+1 llamadas y sin atomicidad. El endpoint server-side lo hace en una
   transacción.
3. **Extraer un módulo `forking` compartido**: se valoró mover
   `_clone_agent_capabilities` a un módulo común; se optó por **reusar el helper
   existente** (import directo) para no refactorizar el `fork_agent` ya probado.
   El builder de la fila Agent se repite mínimamente porque varía por scope.

## Consecuencias

- **+** Un clic adopta un equipo built-in completo y editable, conservando su
  composición; el operador personaliza modelo/tools/skills/prompts sin tocar el
  global.
- **+** Reusa la maquinaria de fork (diff/merge `forked_from_*` ya existente para
  agentes), ahora también a nivel de equipo.
- **+** Cubierto por integración: adoptar el equipo CI4 (10 miembros) a un
  proyecto crea 10 agentes `project_local` forkeados con tools/skills clonadas +
  10 `TeamMember`; adoptar a tenant los crea `global_tenant_template`; el built-in
  no se muta; el `model_config` opcional se aplica al equipo nuevo.
- **−** Copia profunda = N agentes nuevos por adopción (no se comparten con el
  built-in). Es el coste explícito de la independencia; mitigado por los enlaces
  `forked_from_*` que permiten un futuro "traer mejoras del upstream".
- **Pendiente (frontend)**: el botón "Adoptar / Personalizar equipo" + diálogo
  (destino, modelo, nombre) y la navegación al equipo nuevo (Ola C-UI / D).
