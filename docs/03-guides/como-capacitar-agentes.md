---
title: Cómo capacitar a un agente, un equipo o un proyecto
audience: tenant admin, project owner, operator
phase: 06.17-capacitacion-agentes
updated: 2026-06-04
docs_language: es
---

# Cómo capacitar a un agente, un equipo o un proyecto

Esta es la **guía paraguas** de la capacitación. Explica, de extremo a extremo,
qué significa "capacitar" en este sistema, las **cuatro vías** por las que se hace
(**SABER / RECORDAR / SER / HACER**), el **Hub de Capacidad** que las reúne en una
sola pantalla, el **verbo único** "Asignar/Quitar" y la **escalera de niveles**
(Rol → Stack → Equipo → Plataforma).

> El **modelo mental** completo (con su anclaje en el código) vive en la
> referencia [`../04-reference/training-model.md`](../04-reference/training-model.md).
> Esta guía es el **cómo hacerlo** desde la UI; la referencia es el **qué es
> exactamente cada cosa**.

> **Capacitar NO es fine-tuning.** Los LLM son externos y de catálogo cerrado
> ([ADR 0021](../05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md)): no se
> tocan los pesos del modelo. **Capacitar = dotar de CAPACIDAD** por cuatro vías
> complementarias.

## TL;DR

1. Abre la ficha del agente (o del proyecto/equipo): arriba verás el **Hub de
   Capacidad** con cuatro secciones y su **estado real**.
2. Sigue el **checklist** del Hub en orden: **Persona → Saber → Hacer → Recordar**.
3. En cada sección el verbo es siempre **"Asignar"** (y su inverso **"Quitar"**);
   en SER, **"Editar"** la persona.
4. Cada capacidad muestra su **nivel** (Rol/Stack/Equipo/Plataforma): sabes de un
   vistazo si la KB la consulta este agente, todo el proyecto o el catálogo global.
5. Si algo **no está realmente activo**, el Hub lo marca con honestidad ("Sin
   conocimiento asignado", "Privada: no memoriza", "Modelo no configurado", "No
   disponible aún"). Nada finge estar listo.

## Las cuatro vías

Capacitar es responder a cuatro preguntas del operador. Cada una se apoya en un
mecanismo real del código; ninguna es decorativa.

| Vía          | Pregunta                      | Qué asignas                                                    | Guía dedicada                                                                                                                                                        |
| ------------ | ----------------------------- | -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **SABER**    | ¿Qué corpus curado consulta?  | Knowledge Bases (RAG) de rol y de stack + catálogo global      | [kb-ingestion.md](./kb-ingestion.md), [rol-vs-stack](./knowledge-bases-rol-vs-stack.md)                                                                              |
| **RECORDAR** | ¿Qué recuerda entre runs?     | El `memory_scope` (private/team_shared/project_shared/global)  | [memoria-de-agentes.md](./memoria-de-agentes.md)                                                                                                                     |
| **SER**      | ¿Quién es y cómo se comporta? | Persona: proveedor/modelo/temperatura, system prompt, skills   | [persona-y-system-prompt.md](./persona-y-system-prompt.md), [skills-de-agentes.md](./skills-de-agentes.md)                                                           |
| **HACER**    | ¿Qué puede ejecutar?          | Tools + comandos del stack + runtime (lo define el Plan 06.18) | [asignar-tools-a-agentes.md](./asignar-tools-a-agentes.md), [comandos-y-runtime](./comandos-y-runtime-por-proyecto.md), [recetas e2e](./recetas-mcp-tools-skills.md) |

- **SABER** es conocimiento **curado y consultable** (RAG sobre KBs): lo que el
  agente puede **mirar** cuando lo necesita.
- **RECORDAR** es memoria **destilada entre ejecuciones** por scope: lo que el
  agente **trae de vuelta** de runs anteriores.
- **SER** es la **persona**: prompt de sistema, modelo/proveedor/temperatura del
  catálogo cerrado, skills y modo de chat.
- **HACER** son las **acciones ejecutables**: tools, comandos del stack y runtime.
  El detalle vive en el **Plan 06.18**; aquí el Hub muestra el **set efectivo real**.

## El Hub de Capacidad

El Hub es la pantalla que **reúne las cuatro vías** de una entidad en un solo
sitio, con el **estado real** de cada una. Está en la cabecera de la ficha del
agente, del proyecto y del equipo.

Cada sección muestra:

- Un **badge de estado honesto**: "3 KBs asignadas", "Privada: no memoriza", "5
  acciones efectivas", "Modelo no configurado"… Si la capacidad no está activa, el
  badge es neutro/aviso, no verde.
- El **nivel explícito** de cada capacidad (Rol/Stack/Equipo/Plataforma).
- El **verbo único** de la sección y un enlace a dónde se edita.

Encima de las secciones, el Hub muestra:

- Un **checklist de onboarding** en el orden **Persona → Saber → Hacer → Recordar**,
  con cada paso marcado "hecho" solo si su capacidad está realmente activa.
- Un **aviso de agente global** (cuando aplica): un agente sin proyecto propio no
  ve conocimiento ni memoria de proyecto en esta vista; al ejecutar una tarea de
  proyecto usará el contexto de la tarea ([ADR 0054](../05-architecture-decisions/0054-acoplamiento-contexto-proyecto-task.md)).

El Hub es **read-only**: muestra el set efectivo. Cada "Asignar/Editar/Quitar"
enlaza a la sección concreta de la ficha donde de verdad se edita.

## El verbo único: "Asignar / Quitar"

Toda la UI usa **un solo verbo** para dotar y retirar capacidad: **"Asignar"** y su
inverso **"Quitar"** (más **"Editar"** para la persona en SER). Se acabaron los
verbos inconsistentes ("conceder", "vincular", "añadir", "habilitar"…).

El término interno de datos sigue siendo "grant" (filas de junction como
`agent_knowledge_bases`, `kb_projects`), pero **nunca** aparece como etiqueta de
botón.

## La escalera de niveles: ¿dónde capacito qué?

La capacidad no se asigna en un solo sitio. Cada **nivel** decide qué se le puede
asignar y dónde ancla:

| Nivel                | Etiqueta UI                  | Qué se asigna aquí                                                                                           |
| -------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **Rol / agente**     | "…del rol del agente"        | KBs de rol, tools, skills, persona, `memory_scope`                                                           |
| **Stack / proyecto** | "…del stack del proyecto"    | KBs de stack, `allowed_commands`, runtime, memoria `project_shared`                                          |
| **Equipo**           | "Capacidad del equipo"       | Composición + metadata de miembro (ver [ADR 0053](../05-architecture-decisions/0053-capacidad-de-equipo.md)) |
| **Plataforma**       | "Catálogo global (built-in)" | KBs/agentes/tools built-in read-only (requieren **Asignar** o **fork**)                                      |

**Regla práctica**: si la documentación es agnóstica del stack, va con el **rol**
del agente; si menciona un framework concreto (FastAPI, Next.js…), va con el
**stack** del proyecto. El detalle está en
[knowledge-bases-rol-vs-stack.md](./knowledge-bases-rol-vs-stack.md).

## Fork: "Personalizar (crear copia)"

Los agentes y equipos **built-in** del catálogo global son read-only. Para
adaptarlos, pulsa **"Personalizar (crear copia)"** en su ficha: se crea una copia
en tu tenant que **hereda KBs, tools y skills** del original. El badge **Linked /
Forked** se deriva de `forked_from_agent_id` (no del scope), así que siempre
refleja la verdad del linaje.

## Recorrido de capacitación (paso a paso)

1. **SER (persona)** — Abre la ficha del agente, "Editar". Elige proveedor y modelo
   del **catálogo cerrado** (los únicos 4: `claude_sdk`, `copilot`, `azure_foundry`,
   `ollama`) y la temperatura; escribe el system prompt es/en. Sin modelo válido el
   agente no despacha bien. Detalle: [persona-y-system-prompt.md](./persona-y-system-prompt.md).
2. **SABER (conocimiento)** — En la sección Knowledge Bases del agente o del
   proyecto, **Asigna** las KBs. Sube documentos primero ([kb-ingestion.md](./kb-ingestion.md))
   y verifica que quedan "indexadas" (no "indexado vacío"). Decide rol vs stack con
   [knowledge-bases-rol-vs-stack.md](./knowledge-bases-rol-vs-stack.md).
3. **HACER (acciones)** — En la sección Tools, **Asigna** las tools; autoriza
   comandos del stack y runtime en el proyecto ([comandos-y-runtime-por-proyecto.md](./comandos-y-runtime-por-proyecto.md)).
   El Hub muestra el **set efectivo** resultante.
4. **RECORDAR (memoria)** — Ajusta el `memory_scope` para que el agente memorice
   (un `private` en un agente IA **no memoriza**: ver [memoria-de-agentes.md](./memoria-de-agentes.md)).
5. **Verifica en el Hub** — El checklist debe quedar marcado y los badges en verde
   donde corresponda. Lo que siga en aviso/neutro es honesto: aún no está activo.

## Resumen (EN)

"Enabling" an agent/team/project is **not** fine-tuning (LLMs are external,
closed-catalog — [ADR 0021](../05-architecture-decisions/0021-shared-llm-layer-catalogo-cerrado.md)).
It means granting **CAPABILITY** along four paths: **KNOW** (knowledge bases +
RAG), **REMEMBER** (memory by scope), **BE** (persona: provider/model/prompt +
skills) and **DO** (tools + stack commands + runtime, defined in Plan 06.18). The
**Capability Hub** on each entity's page gathers the four paths with their **real
state**, a single verb **Assign/Remove** (Edit for the persona), an explicit
**level** (Role/Stack/Team/Platform) per capability, and an onboarding checklist
**Persona → Know → Do → Remember**. Built-in agents/teams are read-only; use
**"Customize (make a copy)"** to fork them, inheriting KBs/tools/skills. Dedicated
guides cover memory, skills and the persona/system prompt.

## Véase también

- [training-model.md](../04-reference/training-model.md) — el modelo mental único (referencia).
- [memoria-de-agentes.md](./memoria-de-agentes.md) — RECORDAR en profundidad.
- [skills-de-agentes.md](./skills-de-agentes.md) — skills como fragmentos de persona.
- [persona-y-system-prompt.md](./persona-y-system-prompt.md) — SER en profundidad.
- [asignar-tools-a-agentes.md](./asignar-tools-a-agentes.md) — HACER (tools).
