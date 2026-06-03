---
adr_id: "0050"
title: "Skills: cablear end-to-end (asignación + inyección de prompt_fragment) o deprecar"
status: accepted
date: 2026-06-03
authors: [system_architect]
plan_referenced: 06.18-tools-overhaul
docs_language: es
---

# ADR 0050 — Skills: cablear end-to-end o deprecar

> **Estado: `accepted`** (aprobado por el operador 2026-06-03, Fase 0 del Plan 06.18).
> Implementado por `task_06_18_13`. Condiciona el fork de capacidades del Plan 06.17 (`task_06_17_12`)
> y la existencia de una página `/admin/skills`.

## Contexto

Las **Skills** son hoy una **promesa falsa de extremo a extremo**:

- El modelo `AgentSkill(agent_id, skill_id, proficiency)` está definido (`domain.py:519-540`) y hay
  **33 skills seedeadas** (`builtin_skills.py`), pero la junction solo se puebla por seed/SQL.
- **No hay endpoint** `/agents/{id}/skills` (grep `skill` en `routers/agents.py` = 0), **no existe**
  `/admin/skills`, y la ficha del agente solo monta `AgentKbsSection` + `AgentToolsSection`.
- El `prompt_fragment` que el glosario y el seed (`builtin_skills.py:4-6`) venden como el efecto
  central ("inyecta un prompt y sugiere tools") **nunca se inyecta** en el runtime; el único consumo
  es `skill_match_score` en `orchestrator/assignment.py:53` (ordena la asignación de agentes).
- El enum `SkillCategory` (9 valores, `domain.py:152-164`) **no coincide** con las categorías del seed
  (6: backend/frontend/devops/qa/research/docs) y no se valida; `AgentSkillProficiency`
  {basic,standard,expert} es un eje muerto.

El glosario presenta Skills como palanca de capacitación, pero el operador no puede asignarlas, verlas
ni crearlas, y el efecto prometido no ocurre. Hay que **decidir su destino** antes de diseñar el
catálogo (¿hay `/admin/skills`?) y el fork de 06.17 (¿copia skills?).

## Opciones consideradas

- **A. Cablear el ciclo completo (MVP):** `GET/PUT /agents/{id}/skills` (tenant-scoped, patrón espejo
  de `agent_tools`) + `AgentSkillsSection` en la ficha + **inyección del `prompt_fragment`** de las
  skills asignadas en el system prompt efectivo del runtime + alinear el enum de categoría con `CHECK`.
  `tool_suggestions`/`proficiency` quedan para fase posterior. ✅ Cumple la promesa con poco código
  (modelo y seeds ya existen); ✅ da sentido a las 33 skills. ❌ Añade superficie (endpoint, UI, paso
  en dispatch).
- **B. Deprecar Skills:** retirarlas del seed/glosario/enum y eliminar `AgentSkill`. ✅ Menos superficie;
  honestidad inmediata. ❌ Tira trabajo ya modelado y seedeado; pierde una palanca de capacitación
  conceptualmente valiosa; el `skill_match_score` del orquestador perdería su insumo.
- **C. Mantener solo como metadatos read-only** (catálogo de referencia) sin asignación ni inyección
  hasta una fase posterior. ✅ Intermedio sin prometer efecto. ❌ Sigue sin entregar valor; el operador
  ve algo que "no hace nada".

## Decisión

**Opción A (cablear el MVP) — ACEPTADA por el operador el 2026-06-03.** Razones: el modelo, las 33 skills y el `skill_match_score` ya existen;
deprecar tiraría trabajo y empobrecería la capacitación; el MVP (asignación + inyección de
`prompt_fragment`) es barato y convierte una promesa falsa en capacidad real. Alcance del MVP:

1. `GET /agents/{id}/skills` (`tenant_user`) y `PUT /agents/{id}/skills` (`tenant_admin`), declarativo,
   con las mismas reglas de scope que `agent_tools`/grants de KB (built-in asignable; custom solo del
   tenant; `global_builtin` → 403, hay que forkear).
2. `AgentSkillsSection` en `/admin/agents/[id]` (verbo único "Asignar") y, si se mantiene el catálogo,
   `/admin/skills` navegable.
3. **Inyección del `prompt_fragment`** de las skills asignadas en el system prompt efectivo (vía
   `dispatch.py` → spec → `agent_runtime/steps.py`), respetando el "prompt efectivo" del Plan 06.17.
4. Alinear `SkillCategory` con las categorías reales del seed y aplicarlo con `CHECK`/validación.

`proficiency` y `tool_suggestions` (que una skill sugiera tools) se documentan como follow-up con su
propio plan/ADR.

> **Aceptada la Opción A** (cablear el MVP) por el operador el 2026-06-03; la Opción B (deprecar)
> queda descartada. El fork de capacidades de 06.17 (`task_06_17_12`) heredará skills.

## Consecuencias

**Mejora (si A):** Skills pasan a ser capacidad real, asignable y con efecto observable en el prompt;
coherencia del enum; el fork de 06.17 hereda skills.

**Complejidad:** un endpoint + UI + un paso de inyección en dispatch + migración del enum.

**Trade-offs:** se cablea el MVP, no el universo de skills (sin `proficiency`/`tool_suggestions` aún);
se acepta para entregar valor pronto sin sobre-construir.

## Riesgos

| Riesgo                                                                    | Prob. | Impacto | Mitigación                                                      |
| ------------------------------------------------------------------------- | ----- | ------- | --------------------------------------------------------------- |
| Inyectar muchos `prompt_fragment` infla el prompt                         | Media | Bajo    | Límite/orden configurable; el "prompt efectivo" lo hace visible |
| Enum migrado deja categorías huérfanas                                    | Baja  | Bajo    | Migración reversible + saneo del seed a la lista canónica       |
| Si se elige B, el `skill_match_score` del orquestador se queda sin insumo | Baja  | Bajo    | Documentar el impacto en el ADR al aceptar B                    |

## Trazabilidad

- Roadmap: `docs/roadmap/06.18-tools-overhaul.md` (`task_06_18_13`); fork en `06.17` (`task_06_17_12`).
- Modelo: `apps/api-server/src/api_server/db/domain.py` (`AgentSkill`, `SkillCategory`).
- Endpoints/UI: `routers/agents.py`, `routers/skills.py`, `admin-panel/.../agent-skills-section.tsx`.
- Runtime: `orchestrator/dispatch.py`, `agent_runtime/steps.py` (inyección de `prompt_fragment`).
- ADRs relacionados: 0044 (asignación de tools — patrón espejado), 0048 (nombres canónicos).
