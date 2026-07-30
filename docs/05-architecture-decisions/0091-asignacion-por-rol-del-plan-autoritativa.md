---
adr: "0091"
title: La asignación por rol del plan es autoritativa — el dispatch la respeta, LOAD_BALANCED es solo fallback
status: accepted
date: 2026-06-29
deciders: operador, System Architect (claude-opus)
phase: remediacion-ciclo-vida-ejecucion
related: ["0022", "0044", "0065", "0090"]
docs_language: es
---

# ADR 0091 — Asignación por rol del plan, autoritativa

## Contexto

En un run real, la tarea "Implementar JWT" quedó asignada al agente **Project Manager** como
implementer. Causa: el spec del plan **ya lleva un `role` por tarea** (`planning_llm` →
`{id,title,description,role,depends_on}`) y existe `team_role_agents(session, project)` (mapa
`PlanningRole → agent_id`, ya usado por la capa de coste, ADR 0065), pero:

- `sync_to_kanban._build_task` **descartaba el `role`** → la tarea se materializaba con
  `assigned_agent_id = NULL`.
- El dispatch (`orchestrator/dispatch.py::_pick`) solo respetaba un preset bajo política `MANUAL`;
  con el default `LOAD_BALANCED` elegía por **carga** (menos tareas activas), ignorando el rol →
  la implementación caía en el PM (con `review_capability=False` y prompt "no escribo código").

La intención del operador: **"la tarea la implementa el agente asignado en el detalle del plan"**.

## Decisión

### D1 — La materialización resuelve `role` → `assigned_agent_id` (y `reviewer_agent_id`)

`sync_plan_to_kanban` carga el `Project` una vez y computa `role_agents = team_role_agents(...)`;
`_build_task` resuelve el `role` del spec al agente del equipo de ese rol y estampa
`assigned_agent_id`. El `reviewer_agent_id` se toma del rol `reviewer` del equipo, **nunca igual al
implementer** (invariante reviewer ≠ implementer). Rol desconocido, rol sin agente en el equipo, o
proyecto sin equipo → slot `NULL` (no se fuerza un agente arbitrario; decide el fallback).

### D2 — El dispatch HONRA el preset, sea cual sea la política

`_pick` devuelve `task.assigned_agent_id` directamente cuando está seteado (la asignación del plan
es autoritativa), **antes** de evaluar la política. `LOAD_BALANCED`/`ROUND_ROBIN`/`SKILL_MATCH` solo
aplican cuando **no** hay preset. `_route_ai` queda idempotente (reclama el mismo agente).

## Invariantes preservadas

- **Reutilización, no duplicación**: se reusa `team_role_agents` (responder) y `PlanningRole`
  (planning_graph); no se añade una segunda fuente de verdad de roles (ADR 0044/0048).
- **Multi-tenancy**: `team_role_agents` filtra por `team_id`/`tenant_id` y agentes no borrados; la
  resolución respeta la adopción/fork del equipo por tenant (ADR 0066).
- **Compatibilidad**: proyectos legacy sin equipo → `role_agents = {}` → tareas sin preset → el
  dispatch se comporta EXACTAMENTE como antes.

## Alternativas rechazadas

- **Filtro de rol en el pool de candidatos del dispatch** (excluir PM/no-codificadores): resuelve el
  síntoma pero ignora la asignación explícita del plan, que es la fuente de verdad correcta.
- **Que el planning escriba el `agent_id` resuelto en el spec**: acopla el spec (portable, por rol) a
  ids de agentes concretos (no portables entre tenants por el fork ADR 0066). El rol es el nivel de
  abstracción correcto; la resolución a agente ocurre en la materialización.

## Trazabilidad

Plan de 5 tracks en `~/.claude/plans`; investigación R3 en el scratchpad de la sesión.
Implementación: `apps/api-server/.../chat/sync_to_kanban.py` (`_resolve_assignment`, `_build_task`),
`apps/orchestrator/.../dispatch.py` (`_pick`). Tests unit: `tests/unit/test_sync_kanban_assignment.py`,
`tests/unit/test_orchestrator_dispatch_unit.py`.
