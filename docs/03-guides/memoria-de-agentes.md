---
title: Memoria de agentes (RECORDAR) — scopes, escalera de lectura y back-fill
audience: tenant admin, project owner, operator, backend-dev
phase: 06.17-capacitacion-agentes
updated: 2026-06-04
docs_language: es
---

# Memoria de agentes (RECORDAR)

La **memoria** es la vía **RECORDAR** del modelo de capacitación: lo que un agente
**trae de vuelta** de ejecuciones anteriores. No confundir con **SABER**
(Knowledge Bases, corpus curado que el operador sube): la memoria la **destila el
Memorizer** automáticamente al cerrar tareas, no se sube a mano.

> Modelo mental completo en [`../04-reference/training-model.md`](../04-reference/training-model.md).
> Guía paraguas: [`como-capacitar-agentes.md`](./como-capacitar-agentes.md).

## Documento vs memoria (no confundir)

- Un **Documento** es la unidad que se ingiere en una **Knowledge Base** (SABER).
  Lo sube el operador y se consulta vía RAG.
- Una **memoria** (`MemoryEntry`) es un hecho **destilado por el Memorizer** al
  cerrar una tarea (RECORDAR). Nace sola; el operador no la escribe a mano.

## Los cuatro scopes de memoria

Cada agente declara un `memory_scope`. El scope decide **quién** comparte esa
memoria y **bajo qué puntero de propiedad** se guarda:

| Scope            | Quién la comparte                 | Puntero de propiedad |
| ---------------- | --------------------------------- | -------------------- |
| `private`        | Solo el **usuario** dueño         | `user_id`            |
| `team_shared`    | Todo el **equipo**                | `team_id`            |
| `project_shared` | Todo el **proyecto**              | `project_id`         |
| `global`         | Toda la **organización** (tenant) | —                    |

> **Aviso clave (`private` silencioso).** Un agente **IA** con `memory_scope =
private` **no memoriza nada**: la memoria privada se ancla en `user_id`, y un
> agente IA no tiene un usuario dueño. El Hub lo avisa con honestidad ("Privada: no
> memoriza"). Para que un agente IA recuerde, asígnale `team_shared`,
> `project_shared` o `global`.

## La escalera de lectura del `memory_scope`

Cuando un agente hace `memory-recall`, **no** lee todos los scopes: lee solo
aquellos para los que tiene un puntero de propiedad que casa. El filtro real
(`recall.py:_scope_filter_sql`) exige, por cada candidato:

```
scope = 'global'
  OR (scope = 'private'        AND user_id    = :user_id)
  OR (scope = 'team_shared'    AND team_id    = :team_id)
  OR (scope = 'project_shared' AND project_id = :project_id)
```

En la práctica, la **escalera** que ve un agente al ejecutar es:

1. **`global`** — siempre visible (memoria de toda la organización).
2. **`project_shared`** — visible si la ejecución tiene un `project_id` que casa.
3. **`team_shared`** — visible si el agente pertenece al `team_id` que casa.
4. **`private`** — visible solo para el `user_id` dueño (por eso un agente IA no
   la ve ni la escribe).

Todo está **acotado por tenant** (RLS): una memoria privada o de equipo de otro
tenant **nunca** aflora.

### El caso del agente global (ADR 0054)

Un agente **global** (`project_id IS NULL`: built-in o tenant-template) que ejecuta
una **tarea de proyecto** sufría una asimetría: el Memorizer **escribía** la memoria
bajo `task.project_id`, pero la lectura iba por `agent.project_id` (None), así que
el agente global **nunca veía** su propia memoria `project_shared`. El
[ADR 0054](../05-architecture-decisions/0054-acoplamiento-contexto-proyecto-task.md)
resuelve esto: la lectura usa el **`project_id` efectivo task-scoped** (activable por
`platform_settings`), sin abrir fugas entre tenants. El Hub avisa cuando un agente
es global.

## Por qué un agente `private` no memoriza (gating)

El Memorizer solo persiste memoria si se cumplen sus reglas
(`memorizer/policy.py`). El **motivo de no-memorización** se guarda como código
estable en `executions.memorize_skip_reason` y es consultable por la UI:

| Código         | Significado                                                      |
| -------------- | ---------------------------------------------------------------- |
| `ok`           | Sí se memorizó (la columna queda NULL).                          |
| `not_done`     | El estado de la ejecución no es elegible (default: solo `done`). |
| `skip_private` | Agente IA con scope `private` (sin usuario dueño): no memoriza.  |
| `no_team`      | Scope `team_shared` pero el proyecto no tiene equipo.            |
| `no_scope`     | `memory_scope` NULL o no canónico (opt-out explícito).           |
| `llm_empty`    | El LLM no destiló ningún candidato de memoria.                   |

Consulta los motivos con **`GET /memories/skip-reasons`** (filtrable por código).
Los **estados elegibles** (por defecto solo `done`) son operator-configurable vía
`platform_settings` (`memory.memorizable_statuses`).

## Default del `memory_scope` (operator-configurable)

Históricamente, un agente IA creado por UI nacía **siempre** `private`, y
memorizaba en silencio cero. Ahora el default es **operator-configurable**: el
endpoint `POST /agents` lee `memory.default_scope` de `platform_settings` cuando el
body no envía `memory_scope`. El default de plataforma sigue siendo `private`
(backward-compat: no cambia agentes ya creados); un valor no canónico se sanea a
`private`. Un System Admin puede cambiarlo (p. ej. a `project_shared`) para que los
agentes nuevos memoricen por defecto.

## El recall híbrido (BM25 + vectorial)

`memory-recall` combina dos caminos con **Reciprocal Rank Fusion (RRF)**:

- **Texto (BM25-like)**: `ts_rank_cd` sobre `to_tsvector('public.es_unaccent',
content)` — configuración **español + unaccent** (migración 0079). Por eso
  buscar **`arquitectura`** casa **`arquitecturas`** (stemming ES) y el acento es
  irrelevante (`decision` casa `decisión`).
- **Vectorial**: distancia coseno `embedding <=> :query_vector`. **Salta** las
  filas sin embedding (`embedding IS NULL`).

## El back-fill de embeddings

El path vectorial necesita que cada memoria tenga su `embedding`. Hasta el Plan
06.17 ese embedding nunca se rellenaba, así que el recall vectorial y el detector de
"similares" salían vacíos. Ahora:

- **Al crear/mergear** una memoria se calcula su embedding en persistencia.
- Las memorias **antiguas** (con `embedding NULL`) las rellena un **worker de
  back-fill idempotente** (`workers.backfill_memory_embeddings`):
  batched, throttled y operator-configurable, con un **tope duro de lotes** por
  ejecución como defensa. **Sin auto-retry**: es un worker dedicado, nunca parte del
  flujo de un run.

> **Honestidad de estado en la UI.** Donde una memoria aún no tiene embedding, el
> slider de umbral y el detector de "similares" se muestran **"No disponible aún"**,
> no como si funcionaran. Tras correr el back-fill, pasan a estar operativos.

## Cómo lo ves en el Hub

La sección **RECORDAR** del Hub muestra el estado real:

- `private` ⇒ **"Privada: no memoriza"** (aviso).
- Sin memorias todavía ⇒ **"Sin memoria todavía"** (neutro).
- Con memorias pero sin proyecto ⇒ **"N en memoria · sin proyecto"** (info).
- Con memorias de proyecto ⇒ **"N memorias"** (verde).

## Resumen (EN)

**REMEMBER** is memory the Memorizer distills automatically when tasks close — not
documents you upload (that is **KNOW** / Knowledge Bases). Each agent declares a
`memory_scope`: `private` (per `user_id`), `team_shared` (per `team_id`),
`project_shared` (per `project_id`) or `global`. **An AI agent with `private` does
not remember anything** (private memory needs a user owner), and the Hub flags this
honestly. On recall, an agent reads a **ladder**: `global` always, then
`project_shared`/`team_shared`/`private` only where its ownership pointer matches —
all tenant-isolated by RLS. A **global agent** running a project task reads memory
via the **effective task-scoped `project_id`** ([ADR 0054](../05-architecture-decisions/0054-acoplamiento-contexto-proyecto-task.md)).
Skip reasons (`not_done`, `skip_private`, `no_team`, `no_scope`, `llm_empty`) are
queryable via `GET /memories/skip-reasons`. Recall is hybrid (BM25 ES+unaccent +
vector with RRF); an idempotent **back-fill worker** fills missing embeddings, and
the UI shows **"Not available yet"** where embeddings are absent.

## Véase también

- [como-capacitar-agentes.md](./como-capacitar-agentes.md) — guía paraguas.
- [training-model.md](../04-reference/training-model.md) — modelo mental (referencia).
- [knowledge-bases-rol-vs-stack.md](./knowledge-bases-rol-vs-stack.md) — SABER (no confundir con memoria).
- [ADR 0054](../05-architecture-decisions/0054-acoplamiento-contexto-proyecto-task.md) — agente global ve el contexto de la tarea.
