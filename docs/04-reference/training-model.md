---
title: Modelo de capacitación de agentes (SABER/RECORDAR/SER/HACER)
audience: backend-dev, frontend-dev, architect, technical-writer, operator
phase: cross-cutting
updated: 2026-06-04
docs_language: es
plan_referenced: 06.17-capacitacion-agentes
---

# Modelo de capacitación de agentes

Esta página es la **estrella polar** del Plan 06.17: el **modelo mental único**
que define qué significa "capacitar" a un agente, un equipo o un proyecto en este
sistema, y el vocabulario que **toda la UI consume**. Si una pantalla, un endpoint
o una guía habla de capacitación, este documento es la fuente de verdad de los
términos y los verbos.

> **Capacitar NO es fine-tuning.** Los LLM son externos y de catálogo cerrado
> ([ADR 0021](../05-architecture-decisions/0021-llm-provider-catalog.md)): no se
> tocan los pesos del modelo. **Capacitar = dotar de CAPACIDAD** por cuatro vías
> complementarias. Ese es todo el alcance del verbo en este producto.

## Las cuatro categorías: SABER + RECORDAR + SER + HACER

Capacitar a un agente es responder a cuatro preguntas del operador. Cada una se
apoya en un mecanismo real del código; ninguna es decorativa.

| Categoría                | Pregunta del operador         | Mecanismo real (código)                                                                                                 | Verbo en UI            |
| ------------------------ | ----------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| **SABER** (Conocimiento) | ¿Qué corpus curado consulta?  | Knowledge Bases + RAG (`agent_knowledge_bases` rol, `kb_projects` stack, catálogo global built-in)                      | **Asignar**            |
| **RECORDAR** (Memoria)   | ¿Qué recuerda entre runs?     | `MemoryEntry` por scope (private / team_shared / project_shared / global), destilada por el Memorizer                   | **Asignar/Configurar** |
| **SER** (Persona)        | ¿Quién es y cómo se comporta? | `system_prompt` + `model_config` (provider/model/temperature/prompts es+en) + skills + chat-mode                        | **Editar/Asignar**     |
| **HACER** (Acciones)     | ¿Qué puede ejecutar?          | tools (`agent_tools`) + comandos/runtime (`allowed_commands`, `default_runtime_template`) + MCP — **definido en 06.18** | **Asignar**            |

- **SABER** es conocimiento **curado y consultable** (RAG sobre KBs). Es lo que el
  agente puede **mirar** cuando lo necesita. Activar el path vectorial (query-embedder
  con reranker configurable) es parte de la "verdad backend" del plan.
- **RECORDAR** es memoria **destilada entre ejecuciones** por scope. Es lo que el
  agente **trae de vuelta** de runs anteriores. El default de `memory_scope` es
  operator-configurable (no el `private` silencioso histórico) y el motivo de no
  memorización es consultable.
- **SER** es la **persona**: prompt de sistema, modelo/proveedor/temperatura del
  catálogo cerrado ([ADR 0055](../05-architecture-decisions/0055-validacion-model-config.md)),
  skills y modo de chat. Es quién **es** el agente.
- **HACER** son las **acciones ejecutables**: tools, comandos del stack y runtime.
  El detalle de tools (catálogo ≠ runtime, namespaces, UI) vive en el **Plan 06.18**;
  06.17 lo **consume** vía `GET /agents/{id}/effective-tools`.

## Verbo único: "Asignar / Quitar"

Toda la UI usa **un solo verbo** para dotar y retirar capacidad: **"Asignar"** y su
inverso **"Quitar"**. Se acabaron los verbos inconsistentes ("conceder", "vincular",
"añadir", "habilitar"…) repartidos por seis pantallas.

- En la **UI** el operador siempre lee "Asignar" / "Quitar" (más, en SER,
  "Editar" para la persona).
- "**grant**" (conceder) queda como **término interno de datos** (filas de junction
  como `agent_knowledge_bases`, `kb_projects`), nunca como etiqueta de botón.

## Tabla de NIVELES (dónde se capacita qué)

La capacidad no se asigna en un único sitio: cada nivel decide **qué** se le puede
asignar y **dónde** ancla en el modelo de datos. Cada capacidad en la UI muestra su
**nivel explícito** (Rol/Stack/Equipo/Plataforma), reemplazando la jerga "rol vs
stack".

| Nivel                | Etiqueta UI                                    | Qué se asigna a este nivel                                                                                                         | Anclaje real                        |
| -------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| **Rol / agente**     | "Conocimiento/Tools del rol del agente"        | KBs de rol, tools, skills, persona, `memory_scope`                                                                                 | fila `agents` + junctions `agent_*` |
| **Stack / proyecto** | "Conocimiento/Comandos del stack del proyecto" | KBs de stack (`kb_projects`), `allowed_commands`, runtime, memoria `project_shared`                                                | fila `projects` + `kb_projects`     |
| **Equipo**           | "Capacidad del equipo"                         | Composición + metadata de miembro; KB/memoria de equipo SEGÚN [ADR 0053](../05-architecture-decisions/0053-capacidad-de-equipo.md) | `teams` + `team_members`            |
| **Plataforma**       | "Catálogo global (built-in)"                   | KBs/agentes/tools built-in read-only (requieren grant o fork)                                                                      | platform-tenant, `is_builtin`       |

## Reglas del modelo unificado

1. **Un verbo único** "Asignar/Quitar" en toda la UI ("grant" queda como término
   interno de datos).
2. **Un Hub por entidad** con 4 secciones (Saber/Recordar/Ser/Hacer) + estado por
   sección, que muestra el **set efectivo REAL** (la sección HACER consume
   `effective-tools` de 06.18; avisa si el agente es global y no verá conocimiento
   de proyecto).
3. **Nivel explícito** en cada capacidad (Rol/Stack/Equipo/Plataforma), reemplazando
   la jerga "rol vs stack".
4. **Honestidad de estado**: ninguna capacidad parece activa si no lo está; lo roto
   se marca "No disponible aún".
5. **Fork de primera clase**: "Personalizar (crear copia)" en agente y equipo que
   copia KBs/tools/skills (badge Linked/Forked derivado de `forked_from_agent_id`,
   nunca del scope).
6. **Onboarding/checklist**: el Hub guía el orden **Persona → Saber → Hacer →
   Recordar**.

## El contrato del Hub: `GET /{entity}/{id}/capabilities`

El **Hub de Capacidad** (ficha de agente/proyecto/equipo) consume un único endpoint
que devuelve el **set efectivo real** de las cuatro vías. `{entity}` es `agents`,
`projects` o `teams`. Es tenant-scoped (RLS): pedir la entidad de otro tenant
devuelve **404**.

| Campo                   | Significado                                                                                                                 |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `entity_type`           | `agent` \| `project` \| `team`.                                                                                             |
| `entity_id`             | UUID de la entidad.                                                                                                         |
| `saber.knowledge_bases` | KBs visibles, cada una con su `level` (`rol`/`stack`/`plataforma`) e `is_builtin`.                                          |
| `recordar.memory_scope` | `memory_scope` del agente; `null` para proyecto/equipo.                                                                     |
| `recordar.memory`       | Conteo de memorias por scope (`scope`, `count`).                                                                            |
| `ser`                   | Solo en agente: `model_configured`, `provider`, `model`, `temperature`, `system_prompt_present`. `null` en proyecto/equipo. |
| `hacer`                 | `effective` (tools efectivas, compuestas con `effective-tools` de 06.18), `unrestricted`, `shell_exec_effective`.           |
| `warnings`              | Avisos honestos (p. ej. agente global sin contexto de proyecto, ADR 0054; modelo no configurado).                           |

La sección **HACER** **no** se recalcula aquí: el endpoint la **compone** con
`GET /agents/{id}/effective-tools` del Plan 06.18. El frontend deriva el estado de
cada sección con lógica pura (`lib/capability/hub.ts`), sin inventar campos.

## Cómo se relaciona con el resto

- El **acoplamiento conocimiento/memoria de proyecto** (un agente global que ejecuta
  una tarea de proyecto ve el contexto de la tarea) lo decide el
  [ADR 0054](../05-architecture-decisions/0054-acoplamiento-contexto-proyecto-task.md).
- La **capacidad de equipo** (TeamKnowledgeBase/fork de equipo vs materialización por
  proyecto+agentes) la decide el
  [ADR 0053](../05-architecture-decisions/0053-capacidad-de-equipo.md), que aclara y
  mantiene el [ADR 0026](../05-architecture-decisions/0026-agent-scoped-kbs.md).
- La **validación de `model_config`** (SER) contra el catálogo cerrado la fija el
  [ADR 0055](../05-architecture-decisions/0055-validacion-model-config.md).
- El **modelo relacional** de agentes/KBs/memoria/tools vive en
  [domain-model.md](./domain-model.md); el **detalle de tools/HACER** en
  [tools.md](./tools.md).
- Los **términos** (Capacidad, Persona, Contexto, Documento vs Documentación) están en
  el [glosario](../context/glossary.md).
