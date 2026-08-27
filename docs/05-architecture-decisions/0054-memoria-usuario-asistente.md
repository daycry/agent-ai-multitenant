---
adr_id: "0054"
title: "Memoria del usuario en el asistente personal (memory private por user_id + tool recordar + inyección automática)"
status: accepted
date: 2026-06-08
authors: [system_architect]
plan_referenced: 10-asistente-personal
docs_language: es
---

# ADR 0054 — Memoria del usuario en el asistente personal

> ⚠️ **El número 0054 está usado dos veces.** El otro es [`0054-acoplamiento-contexto-proyecto-task.md`](./0054-acoplamiento-contexto-proyecto-task.md).
> Una referencia suelta a «ADR 0054» en este repo **es ambigua**: hoy
> aparece en decenas de ficheros y ninguno dice a cuál de los dos apunta. Al
> citar uno de estos dos, enlaza el fichero en vez de escribir el número.
> Vigilado por `tests/docs/test_adr_numbers_are_unique.py`, que impide que
> aparezca un tercero.

> **Estado: `accepted`** (aprobado por el operador 2026-06-08).
> El asistente personal debe ir generando memoria del usuario (su nombre, preferencias, gustos) y usarla
> en futuras conversaciones, reutilizando el subsistema de memoria existente.

## Contexto

El asistente personal (ADR 0033/0053) ya conversa y resuelve un modelo LLM, pero **no recuerda nada del
usuario entre turnos/sesiones**. El operador quiere que "se vaya alimentando de mis gustos".

La plataforma **ya tiene un subsistema de memoria completo** (Plan 04/16) que encaja:

- `memory_entries` (migración 0020) con **`scope='private'` + `user_id`** — memoria privada por usuario,
  exactamente "lo que sé de ti". Otros scopes: `team_shared`, `project_shared`, `global`. `type` ∈
  `{episodic, semantic}`.
- Escritura: `memorizer.persistence.persist_memory_candidates(... scope, user_id, agent_id, extra_metadata)`
  y el endpoint `POST /memories` (private → `user_id` del JWT).
- Lectura: `memorizer.recall.recall(session, *, query, tenant_id, scopes, user_id, query_embedding?, limit)`
  — híbrido **BM25 + pgvector + RRF**; sin `query_embedding` cae a **BM25 puro**. Filtra por scope+owner.
- Dedup por similitud (settings `memories.similarity.*`), embeddings rellenados async, y la página
  `/admin/memories` para ver/borrar.
- El asistente ya tiene el patrón de tools (`ASSISTANT_TOOLS` + `tool_schemas`) y `AssistantToolContext`
  expone `user_id` + `tenant_id` + la sesión RLS.

O sea: esto es **conectar** el asistente a la memoria, no construirla.

## Opciones consideradas

**Cómo se CREA la memoria:**

- **C-A. Tool `recordar` (el LLM decide).** El asistente invoca una herramienta cuando el usuario comparte
  algo duradero. ✅ Reusa el patrón de tools, controlable, barato. ❌ Depende de que el modelo decida llamarla.
- **C-B. Destilación automática post-turno** (un pase LLM por conversación, como `distil_execution`). ✅
  Pasivo. ❌ Un LLM call por turno (coste/latencia) y menos control.
- **C-C. Ambos.**

**Cómo se USA la memoria:**

- **U-A. Inyección automática en el system prompt.** Antes de responder, recall de las memorias relevantes
  → al prompt. ✅ El asistente "te conoce" sin pedir nada. ❌ Recall por turno (barato en BM25).
- **U-B. Tool de recall (el LLM decide).** ❌ A veces no la llamará cuando convenga.

**Qué inyectar:** las memorias recientes del usuario (capadas) vs recuperación por relevancia (BM25/vector).
Para pocos hechos por usuario, inyectar todas las recientes es más fiable; el ranking por relevancia importa
solo a escala.

## Decisión

**C-A (tool `recordar`) + U-A (inyección automática de las memorias recientes del usuario).**

1. **Modelo de datos (sin migración):** cada recuerdo es una fila `memory_entries` con `scope='private'`,
   `user_id` = el usuario que chatea, `type` (`semantic` por defecto para preferencias/gustos; `episodic`
   para eventos), `metadata={"source":"assistant"}`, `agent_id=NULL`. Memoria **privada por usuario** (cada
   usuario tiene la suya; aislamiento por `user_id`, ya garantizado por el filtro scope+owner del recall y
   por RLS de tenant).
2. **Crear — tool `remember_about_me(content, type?, tags?)`** (`assistant/tools.py`): delega en
   `assistant/memory.py::remember_user_fact`, que **dedup** (no reescribe un contenido normalizado idéntico
   ya guardado del usuario) y persiste vía `persist_memory_candidates(scope="private", user_id=ctx.user_id,
source="assistant")`. Activa por defecto (`DEFAULT_ENABLED_TOOLS`). El system prompt + la descripción del
   tool instruyen al asistente a guardar solo datos duraderos (nombre, preferencias, gustos, estilo).
3. **Usar — inyección automática** (`routers/assistant.py::assistant_chat`): antes del turno,
   `recall_user_memories(user_id)` trae las memorias privadas del usuario (las **más recientes**, capadas a 20) y se añaden al system prompt en una sección **"Lo que sé de ti"**, con una guía para que el asistente
   use `remember_about_me`. Sin FTS ni embeddings en el camino del chat.
4. **Inyección directa (MVP):** se inyectan las memorias recientes del usuario, **no** se filtran por la
   consulta. Es lo más fiable para pocos hechos y evita que `plainto_tsquery` (que hace AND de los términos)
   no case una pregunta en lenguaje natural con un hecho corto. A escala, el ranking por relevancia
   (`memorizer.recall`, BM25+vector+RRF — que ya existe) es un fast-follow **aditivo** (la lectura está
   aislada en `recall_user_memories`).
5. **Gestión:** la página existente `/admin/memories` ya permite ver/borrar las memorias privadas (filtrables
   por `metadata.source='assistant'`).

## Consecuencias

**Mejora:** Aria recuerda tu nombre/preferencias y los usa sin que se lo pidas; reutiliza por completo el
subsistema de memoria (escritura, recall híbrido, dedup, RLS, UI) sin migración.

**Trade-offs:** el MVP inyecta las **20 memorias más recientes** del usuario sin filtrar por la pregunta.
Para los pocos hechos típicos (nombre + preferencias) basta y el asistente siempre los ve. Si un usuario
acumula muchísimos hechos, los más antiguos podrían no entrar en ese tope; el fast-follow es rankear por
relevancia (`memorizer.recall`, BM25+vector ya existente).

**Complejidad:** una tool nueva + un helper de memoria + la inyección en el chat. Aislado en
`assistant/memory.py`.

## Riesgos

| Riesgo                                                | Prob. | Impacto | Mitigación                                                                                  |
| ----------------------------------------------------- | ----- | ------- | ------------------------------------------------------------------------------------------- |
| El modelo no llama al tool / guarda ruido             | Media | Bajo    | Descripción del tool + guía en el prompt; dedup; el usuario puede borrar en /admin/memories |
| Usuario con >20 memorias: las antiguas no se inyectan | Baja  | Bajo    | Tope de 20 recientes; fast-follow a ranking por relevancia (`memorizer.recall`)             |
| Fuga cross-user (ver memoria de otro usuario)         | Baja  | Alto    | `recall` filtra `scope='private' AND user_id=:user_id`; RLS de tenant; tests de aislamiento |
| El asistente guarda datos sensibles del usuario       | Baja  | Medio   | Solo memoria privada del propio usuario; visible y borrable en /admin/memories              |

## Alternativas rechazadas

C-B/C-C (destilación automática) por el coste/latencia de un LLM call por turno — se puede añadir como fase 2.
U-B (tool de recall) por no garantizar que el asistente recuerde cuando conviene. Recall semántico desde el
día uno por meter una dependencia de embeddings en el camino caliente del chat con poco retorno frente al
BM25 para los casos más comunes.

## Trazabilidad

- Backend: `apps/api-server/src/api_server/assistant/memory.py`,
  `apps/api-server/src/api_server/assistant/tools.py`,
  `apps/api-server/src/api_server/routers/assistant.py`,
  `apps/api-server/src/api_server/assistant/config.py`.
- Reutiliza: `apps/api-server/src/api_server/memorizer/{persistence,recall}.py`,
  `apps/api-server/src/api_server/db/memory.py`.
- Frontend: `apps/admin-panel/lib/assistant.ts` (catálogo de tools), `/admin/memories` (gestión).
- ADRs relacionados: 0033 (asistente en api-server), 0053 (modelo del asistente), scopes de memoria (Plan 04).
