---
title: Skills de agentes — fragmentos de persona asignables
audience: tenant admin, project owner, operator
phase: 06.17-capacitacion-agentes
updated: 2026-06-04
docs_language: es
---

# Skills de agentes

Las **skills** son parte de la vía **SER** (persona): cada skill aporta un
**`prompt_fragment`** que se **inyecta en el system prompt efectivo** del agente al
ejecutar. Asignar una skill es, en la práctica, **enriquecer la persona** del
agente con una pieza de comportamiento reutilizable (p. ej. "revisa siempre OWASP
Top-10", "escribe tests antes que código").

> Modelo mental completo en [`../04-reference/training-model.md`](../04-reference/training-model.md).
> Guía paraguas: [`como-capacitar-agentes.md`](./como-capacitar-agentes.md).
> La decisión de cablearlas (vs deprecarlas) está en el
> [ADR 0050](../05-architecture-decisions/0050-skills-cablear-o-deprecar.md).

## Qué hace una skill (y qué no)

- **Sí**: aporta un `prompt_fragment` que se **suma al prompt efectivo** del agente
  cuando ejecuta. Es su efecto central y observable.
- **Sí**: el orquestador usa las skills para el `skill_match_score`, que ayuda a
  **elegir qué agente** encaja mejor con una tarea.
- **No (todavía)**: una skill **no** asigna tools por sí misma. El campo
  `required_tools` es una **recomendación**, no una concesión automática
  (follow-up con su propio plan según el ADR 0050).
- **No (todavía)**: la `proficiency` (`basic`/`standard`/`expert`) se almacena pero
  aún no modula el comportamiento (follow-up).

## Skill vs tool vs KB (no confundir)

| Concepto  | Vía   | Efecto                                                            |
| --------- | ----- | ----------------------------------------------------------------- |
| **Skill** | SER   | Inyecta `prompt_fragment` en el prompt efectivo (comportamiento). |
| **Tool**  | HACER | Habilita una **acción ejecutable** (definido en el Plan 06.18).   |
| **KB**    | SABER | Corpus curado consultable por RAG (conocimiento).                 |

## Asignar skills a un agente

El ciclo está cableado de extremo a extremo (ADR 0050, Opción A):

1. Abre la ficha del agente (`/admin/agents/[id]`).
2. En la sección **Skills**, **Asigna** las que quieras del catálogo (verbo único
   "Asignar/Quitar"). Las **built-in** del catálogo global son asignables; las
   **custom** solo dentro de tu tenant.
3. Al ejecutar, el `prompt_fragment` de cada skill asignada se inyecta en el system
   prompt efectivo (vía `dispatch` → spec → runtime), respetando el "prompt
   efectivo" descrito en [persona-y-system-prompt.md](./persona-y-system-prompt.md).

> **Built-in read-only.** Un agente `global_builtin` no admite asignar skills
> directamente (devuelve 403): primero **"Personalizar (crear copia)"** (fork) y
> asigna sobre la copia. El fork **hereda las skills** del original (task_06_17_12).

### Contrato (resumen)

- `GET /agents/{id}/skills` (`tenant_user`) — lista las skills asignadas vía la
  junction `agent_skills`.
- `PUT /agents/{id}/skills` (`tenant_admin`) — declarativo (espeja el patrón de
  `agent_tools` y de los grants de KB).

## Categorías

La `category` de una skill está **cerrada al conjunto del seed** (CHECK en BD,
ADR 0050 / migración 0078): el value set se deriva de `SkillCategory`, la única
declaración. No inventes categorías fuera de ese conjunto.

## Resumen (EN)

**Skills** belong to the **BE** (persona) path: each skill carries a
`prompt_fragment` that is **injected into the agent's effective system prompt** at
run time (the central, observable effect; [ADR 0050](../05-architecture-decisions/0050-skills-cablear-o-deprecar.md),
Option A). They also feed the orchestrator's `skill_match_score` for agent
selection. A skill is **not** a tool grant (its `required_tools` is a
recommendation, not an automatic grant) and `proficiency` is stored but not yet
behavioral — both are documented follow-ups. Assign skills from the agent page
(single verb **Assign/Remove**); built-in agents are read-only — **fork** them
first (the fork inherits skills). Endpoints: `GET /agents/{id}/skills`
(`tenant_user`), `PUT /agents/{id}/skills` (`tenant_admin`). Categories are closed
to the seed set via a DB CHECK.

## Véase también

- [como-capacitar-agentes.md](./como-capacitar-agentes.md) — guía paraguas.
- [persona-y-system-prompt.md](./persona-y-system-prompt.md) — el prompt efectivo donde se inyecta el fragment.
- [asignar-tools-a-agentes.md](./asignar-tools-a-agentes.md) — HACER (tools), no confundir con skills.
- [ADR 0050](../05-architecture-decisions/0050-skills-cablear-o-deprecar.md) — cablear vs deprecar skills.
