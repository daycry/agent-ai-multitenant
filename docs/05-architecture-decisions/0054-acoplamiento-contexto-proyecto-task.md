---
adr_id: "0054"
title: "Acoplamiento de contexto de proyecto — un agente global usa el project_id EFECTIVO de la tarea (task-scoped) para RAG y memoria, tenant-safe"
status: accepted
date: 2026-06-04
authors: [system_architect]
plan_referenced: 06.17-capacitacion-agentes
docs_language: es
---

# ADR 0054 — `agent.project_id` vs `task.project_id` (project_id efectivo task-scoped)

> **Estado: `accepted`** (aprobado por el operador 2026-06-04, Fase 0 del Plan
> `06.17-capacitacion-agentes`). Lo consume `task_06_17_13` (agente global ve el
> contexto de la tarea) y se activa por `platform_settings`.

## Contexto

Hay una **asimetría real entre escritura y lectura** de conocimiento/memoria de
proyecto cuando un agente **global** (sin `project_id` propio, p. ej. un
`global_tenant_template` o un built-in) ejecuta una tarea **que sí pertenece a un
proyecto**:

- **Escritura (memoria):** el Memorizer destila la memoria del run usando el
  `project_id` de la **tarea** (`task.project_id`). Lo que el agente aprende se
  guarda correctamente bajo el proyecto de la tarea.
- **Lectura (RAG):** `rag_search_endpoint` (`internal_agent.py:354-355`) hace
  `if agent.project_id is None: return RagSearchResponse(hits=[])`. Un agente
  global devuelve **siempre `[]`** — no ve los chunks del proyecto de la tarea que
  está ejecutando.
- **Lectura (memoria):** `memory_recall_endpoint` (`internal_agent.py:150`)
  resuelve `project_id = agent.project_id`. Para un agente global eso es `None`,
  así que el scope `project_shared` **no recupera nada** del proyecto de la tarea.

Resultado: un agente global **escribe** memoria en `task.project_id` pero **no
puede leer** ni esa memoria ni las KBs de ese proyecto. La promesa "asigna
conocimiento de proyecto y el agente lo consulta" se rompe en silencio para
cualquier agente de catálogo reutilizable entre proyectos — justo el caso de uso
central del catálogo global del sistema.

La causa raíz es que el contexto de lectura se ancla en `agent.project_id`
(propiedad **estática** del agente) en lugar de en el `project_id` **efectivo de
la tarea en curso** (propiedad **dinámica** del run). Cualquier arreglo debe ser
**estrictamente tenant-safe** y **acotado al único proyecto de la tarea en curso**
— jamás cross-project ni cross-tenant.

## Opciones consideradas

- **A. Status quo (asimetría).** Dejar lectura anclada en `agent.project_id`;
  documentar que los agentes globales no ven conocimiento de proyecto.
  - ✅ Cero cambio de visibilidad. ❌ Mantiene la rotura de extremo a extremo:
    el agente escribe donde no puede leer. ❌ El catálogo global de agentes
    reutilizables pierde su valor en proyectos. Rechazada.

- **B. `project_id` efectivo task-scoped (ELEGIDA).** En los endpoints internos,
  resolver el `project_id` efectivo como el **de la tarea en curso** cuando el
  agente es global: el principal de autenticación del runtime ya identifica el
  `execution`/`task`, de donde se obtiene `task.project_id`. RAG y memoria
  `project_shared` usan ese `project_id` efectivo, **validando que pertenece al
  mismo tenant** y limitándose a **ese único proyecto**. Activable por
  `platform_settings` (default ON).
  - ✅ Arregla la asimetría real (read = write = `task.project_id`); ✅ acotado y
    tenant-safe (un solo proyecto, mismo tenant); ✅ reversible por flag si un
    operador quiere el comportamiento estricto antiguo. ❌ Cambia la visibilidad
    de lectura de los agentes globales → exige tests `cross_tenant` obligatorios.

- **C. Copiar `task.project_id` al `agent.project_id` en dispatch.** Mutar el
  agente (o una copia efímera) para que tenga el `project_id` de la tarea.
  - ✅ La lectura "simplemente funciona" sin tocar los endpoints. ❌ Muta una
    propiedad estática del agente con un valor de run — fuente de bugs de
    concurrencia (un mismo agente template ejecutando dos tareas de proyectos
    distintos en paralelo). ❌ Difumina la distinción agente-global vs
    agente-de-proyecto. ❌ Riesgo de que el `project_id` mutado se persista por
    error y abra fuga. Rechazada.

## Decisión

**Opción B — `project_id` efectivo task-scoped.**

1. **`project_id` efectivo.** Los endpoints internos (`/internal/agent/rag-search`
   y `/internal/agent/memory-recall`) resuelven el `project_id` de lectura como:
   - el `agent.project_id` si el agente está atado a un proyecto (comportamiento
     actual, sin cambio);
   - en su defecto (agente **global**), el **`project_id` de la tarea en curso**
     (`task.project_id`), obtenido del contexto del run que el principal del
     runtime ya porta.

2. **Estrictamente tenant-safe y un solo proyecto.** El `project_id` efectivo se
   valida contra el `tenant_id` del principal **antes** de usarse; las queries de
   RAG y de recall siguen siendo tenant-scoped con RLS. El alcance es **el único
   proyecto de la tarea en curso** — nunca un conjunto de proyectos, nunca otro
   tenant. No se relaja ningún filtro existente; solo se corrige **qué**
   `project_id` se inyecta.

3. **Activable por `platform_settings` (default ON).** Una clave operator-
   configurable (p. ej. `memory.global_agent_uses_task_project` o equivalente)
   gobierna el comportamiento; el default es **ON** porque arregla la asimetría
   real. Un operador que prefiera el aislamiento estricto antiguo (agente global =
   sin contexto de proyecto) lo apaga sin tocar código. Sin auto-retry de runs:
   esto es resolución de contexto, no reintentos.

4. **Honestidad de estado.** El Hub avisa explícitamente del comportamiento: con
   el flag ON, "este agente global usará el conocimiento/memoria del proyecto de
   la tarea"; con el flag OFF, "este agente global no verá conocimiento de
   proyecto". Nunca se finge contexto que no se va a leer.

## Consecuencias

**Mejora:** se cierra la cadena SABER/RECORDAR para agentes globales (read =
write); el catálogo de agentes reutilizables aporta valor real en proyectos; el
comportamiento es operator-configurable y reversible.

**Complejidad añadida:** resolución de `project_id` efectivo en dos endpoints + un
flag nuevo en `platform_settings` + lectura del contexto de tarea desde el
principal del runtime. Tests `cross_tenant` nuevos (obligatorios por el cambio de
visibilidad).

**Trade-offs:** cambiar visibilidad de lectura es un cambio sensible; se acota a
"un solo proyecto, mismo tenant", se cubre con tests `cross_tenant` y se hace
reversible por flag. Backward-compat: con embeddings ausentes el path sigue
funcionando (BM25); con el flag OFF el comportamiento es el antiguo exacto.

## Riesgos

| Riesgo                                                              | Prob. | Impacto | Mitigación                                                                           |
| ------------------------------------------------------------------- | ----- | ------- | ------------------------------------------------------------------------------------ |
| El `project_id` efectivo de la tarea pertenece a otro tenant (fuga) | Baja  | Alto    | Validación tenant_id antes de usar + RLS; test `@pytest.mark.cross_tenant` (B → 404) |
| Un agente global lee de varios proyectos (cross-project)            | Baja  | Alto    | Estrictamente el único `task.project_id` de la tarea en curso; nunca un conjunto     |
| El cambio de visibilidad sorprende a un operador                    | Media | Bajo    | Flag operator-configurable + aviso honesto en el Hub; default ON documentado         |

## Alternativas rechazadas

A (status quo) por mantener la rotura write≠read; C (mutar `agent.project_id`) por
muta propiedad estática con valor de run (concurrencia + riesgo de fuga si se
persiste).

## Trazabilidad

- Roadmap: `docs/roadmap/06.17-capacitacion-agentes.md` (`task_06_17_13`,
  `task_06_17_02`, `task_06_17_03`).
- Endpoints: `apps/api-server/src/api_server/routers/internal_agent.py`
  (`rag-search` `:354-355`; `memory-recall` `:150`);
  `apps/api-server/src/api_server/auth/internal_agent.py`.
- Dispatch: `apps/orchestrator/src/orchestrator/dispatch.py` (contexto de tarea).
- Flag: `apps/api-server/src/api_server/db/platform_settings.py`.
- ADRs relacionados: 0024 (internal-agent HTTP API), 0023/0026 (RAG y
  agent-scoped KBs), 0001 (RLS desde el día uno), 0010 (superadmin cross-tenant).
