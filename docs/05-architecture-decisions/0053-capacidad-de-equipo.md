---
adr_id: "0053"
title: "Capacidad de equipo — vista agregada read-only + metadata de miembro, sin subsistema TeamKnowledgeBase (mantiene ADR 0026)"
status: accepted
date: 2026-06-04
authors: [system_architect]
plan_referenced: 06.17-capacitacion-agentes
docs_language: es
---

# ADR 0053 — Capacidad de equipo (mantener ADR 0026, agregar + editar metadata)

> **Estado: `accepted`** (aprobado por el operador 2026-06-04, Fase 0 del Plan
> `06.17-capacitacion-agentes`). Lo consumen `task_06_17_15` (capacidad de equipo
> y metadata de miembro) y el Hub de capacidad (`task_06_17_09`).

## Contexto

El roadmap del Plan 06.17 pregunta cómo se "capacita" a un **equipo**: ¿qué sabe
(KBs), qué recuerda (memoria) y qué puede hacer (tools) un `team`, y cómo se
edita esa capacidad? Hoy la realidad del código es:

- **No existe ninguna noción de capacidad a nivel de equipo.** El conocimiento
  (KBs) se grants al **rol del agente** (`agent_knowledge_bases`) o al **stack del
  proyecto** (`kb_projects`) — los dos ejes independientes que fijó el **ADR
  0026**. No hay tabla `team_knowledge_bases` ni memoria de scope "team-owned"
  desligada de proyecto.
- El `teams` + `team_members` ya modela la composición y trae metadata de miembro
  **editable** (`is_team_leader`, `role_in_team`, `assignment_priority`) vía un
  `PUT /teams/{id}/members` que **existe pero la UI no invoca** (`teams.py:233-261`).
- En `domain.py` arrastra un campo **muerto**: `teams.shared_memory_namespace`,
  sin lectura productiva en ningún path de recall/store (la memoria
  `team_shared` se resuelve por `project.team_id`, no por ese namespace).
- La UI miente con un badge Linked/Forked inferido del **scope** del agente, no de
  `forked_from_agent_id` (`teams/[team_id]/page.tsx:106-119,256-272`) — un
  problema de persona/fork que resuelve `task_06_17_12`, no este ADR.

El **ADR 0026** ya consideró y **rechazó** atar las KBs al equipo (su "Alt-2: KBs
por team"): los agentes no siempre pertenecen a un solo equipo, el catálogo
built-in son `global_tenant_template` no atados a equipo, y refinar "el reviewer
ve algo distinto que el `backend_dev` aunque comparten equipo" obliga a volver a
grants per-agent. Lo dejó anotado como follow-up "si surge la necesidad real".

La pregunta de este ADR: ¿esa necesidad ya surgió — creamos el subsistema
`team_knowledge_bases` + memoria de equipo + fork de equipo persistido —, o
entregamos el **valor que pide el operador** (ver qué sabe el equipo + editar la
metadata de miembro) **sin** persistencia nueva?

## Opciones consideradas

- **A. Subsistema TeamKnowledgeBase nuevo.** Crear `team_knowledge_bases(team_id,
kb_id, tenant_id, …)` con su migración + RLS, un scope de memoria "team-owned"
  propio, y un `fork_team` que materializa una copia persistida del equipo y sus
  KBs. El resolver de visibilidad ganaría una cuarta fuente (proyecto ∪ rol ∪
  global ∪ **equipo**).
  - ✅ Modela "el equipo sabe X" como primer ciudadano; el fork de equipo es una
    entidad real.
  - ❌ **Contradice el ADR 0026**, que ya rechazó exactamente esto por buenas
    razones (agentes en varios equipos, built-ins sin equipo, vuelta a grants
    per-agent). ❌ Tabla nueva + migración + RLS + cuarta rama en el visibility
    filter de chunks (riesgo de fuga si se cablea mal). ❌ Duplica el modelo
    mental: el operador ahora tendría **tres** sitios donde "el conocimiento
    vive" (rol, stack, equipo) en lugar de dos. ❌ El "fork de equipo" persistido
    es una entidad pesada para un valor que se compone clonando agentes.

- **B. Recortar a vista agregada read-only + metadata de miembro (ELEGIDA).**
  **Mantener el ADR 0026 sin cambios** (no hay grants de KB a agentes
  `global_builtin`, no hay `team_knowledge_bases`). En su lugar:
  - (a) una **vista de capacidad de equipo** que **agrega read-only** las KBs y
    tools de los **miembros** del equipo (la UNIÓN de lo que ya saben/pueden los
    agentes que lo componen) — cero persistencia nueva, solo una query de lectura
    sobre las junctions existentes;
  - (b) la UI cablea el `PUT /teams/{id}/members` ya existente para editar la
    **metadata de miembro** (`is_team_leader` / `role_in_team` /
    `assignment_priority`);
  - (c) el **"fork de equipo" se compone** clonando los agentes miembros (cada
    `fork_agent` copia sus KBs/tools/skills — `task_06_17_12`), no una tabla nueva;
  - (d) se **retira** el campo muerto `teams.shared_memory_namespace`.
  - ✅ Respeta el ADR 0026; ✅ menor riesgo (sin schema nuevo de visibilidad); ✅
    entrega el valor real que pide el operador; ✅ el modelo mental sigue con dos
    ejes de conocimiento. ❌ La capacidad de equipo es **derivada** (no
    asignable directamente al equipo); si mañana hace falta "el equipo entero
    sabe X aunque ningún miembro lo tenga", habría que reabrir hacia la Opción A.

- **C. Híbrido: solo memoria de equipo, sin KBs de equipo.** Mantener KBs como en
  0026 pero añadir un scope de memoria "team-owned" desligado de proyecto.
  - ✅ Pequeño. ❌ Introduce un cuarto scope de memoria fuera de la escalera
    actual (private/team_shared/project_shared/global), donde `team_shared` ya
    cubre la memoria de equipo vía `project.team_id`. Inventa un concepto sin
    demanda y complica el recall. Rechazada.

## Decisión

**Opción B.** Se **mantiene el ADR 0026** tal cual: el conocimiento vive en dos
ejes (rol del agente vía `agent_knowledge_bases`, stack del proyecto vía
`kb_projects`) + el catálogo global built-in; **no** se crea `team_knowledge_bases`
ni un scope de memoria de equipo nuevo, y **no** se conceden grants de KB a
agentes `global_builtin` (un tenant que quiera customizar un built-in lo forkea,
exactamente como dice el 0026).

Lo que **sí** entrega el Plan 06.17 a nivel de equipo:

1. **Vista de capacidad de equipo (read-only, agregada).** Un endpoint de equipo
   (`GET /teams/{id}/capabilities`, `task_06_17_08`) y su sección en el Hub
   (`task_06_17_09`) que **agregan** lo que ya saben/pueden los **miembros**: la
   UNIÓN de las KBs visibles y las tools efectivas de los agentes del equipo,
   marcando de qué miembro proviene cada capacidad. Es una **lectura** sobre las
   junctions existentes (`team_members` ⨝ `agent_knowledge_bases` /
   `effective-tools` de 06.18), **tenant-scoped con RLS**, sin persistencia nueva.
   Honestidad de estado: si el equipo no tiene miembros, o ningún miembro tiene
   KBs, la sección lo dice ("sin conocimiento de equipo aún"), no finge capacidad.

2. **Metadata de miembro editable.** La UI invoca el `PUT /teams/{id}/members` ya
   existente para fijar `is_team_leader`, `role_in_team` y `assignment_priority`.
   Es la única escritura nueva de la UI a nivel de equipo y opera sobre columnas
   que ya existen.

3. **Fork de equipo compuesto, no persistido.** "Personalizar (crear copia)" de un
   equipo se resuelve clonando los agentes miembros built-in vía `fork_agent`
   (que copia `AgentKnowledgeBase`/`AgentTool`/`agent_skills` — `task_06_17_12`).
   No hay entidad `forked_team` ni copia de KBs a nivel de equipo.

4. **Retirar `shared_memory_namespace`.** Se elimina el campo muerto de `teams`
   (migración reversible, encadenada al head vigente). La memoria de equipo se
   sigue resolviendo por el scope `team_shared` vía `project.team_id`, sin cambio
   de comportamiento.

**Referencia explícita:** esta decisión es la materialización del follow-up
anotado en el **ADR 0026** ("Alt-2: KBs por team — anotada como follow-up si surge
la necesidad real"): se concluye que la necesidad **no** justifica el subsistema;
se entrega la vista agregada en su lugar. El ADR 0026 gana una nota que enlaza
aquí.

## Consecuencias

**Mejora:** el operador por fin ve "qué sabe / qué puede el equipo" (agregado de
miembros) y edita la metadata de miembro desde la UI; se retira un campo muerto;
el modelo mental de conocimiento se mantiene en dos ejes (no tres); se respeta la
regla del ADR 0026 de no grantear built-ins.

**Complejidad añadida:** una query de agregación read-only nueva (con su RLS y su
test `cross_tenant`) y el cableado de un `PUT` que ya existía. Coste de schema:
**negativo** (se retira una columna).

**Trade-offs:** la capacidad de equipo es **derivada de los miembros**, no
asignable directamente al equipo. Si en el futuro aparece la demanda real de "el
equipo entero sabe X con independencia de sus miembros", este ADR se **supersede**
hacia la Opción A (subsistema `team_knowledge_bases` con la misma maquinaria de
RLS que `kb_projects`/`agent_knowledge_bases`). Hoy sería over-engineering.

## Riesgos

| Riesgo                                                            | Prob. | Impacto | Mitigación                                                                       |
| ----------------------------------------------------------------- | ----- | ------- | -------------------------------------------------------------------------------- |
| La vista agregada filtra capacidades de agentes de otro tenant    | Baja  | Alto    | Query tenant-scoped + RLS + test `@pytest.mark.cross_tenant` (tenant B → 404)    |
| Retirar `shared_memory_namespace` rompe un lector oculto          | Baja  | Medio   | Grep previo confirma sin uso productivo; migración reversible; tests de memoria  |
| El operador espera asignar KB "al equipo" y no encuentra el botón | Media | Bajo    | La guía y el Hub explican que la capacidad de equipo es la UNIÓN de los miembros |

## Alternativas rechazadas

A (subsistema TeamKnowledgeBase) por contradecir el ADR 0026 y triplicar el modelo
mental sin demanda probada; C (scope de memoria de equipo nuevo) por inventar un
cuarto scope donde `team_shared` ya cubre el caso.

## Trazabilidad

- Roadmap: `docs/roadmap/06.17-capacitacion-agentes.md` (`task_06_17_08`,
  `task_06_17_09`, `task_06_17_12`, `task_06_17_15`).
- Endpoints: `apps/api-server/src/api_server/routers/teams.py` (`PUT
/teams/{id}/members` ya existente; `GET /teams/{id}/capabilities` nuevo).
- Schema: `apps/api-server/src/api_server/db/domain.py` (retirar
  `teams.shared_memory_namespace`).
- ADRs relacionados: **0026** (agent-scoped KBs — se mantiene; gana nota a este
  ADR), 0006 (linked/forked), 0021 (catálogo cerrado de proveedores).
