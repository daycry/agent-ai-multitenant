---
plan_id: memoria-agentes-2026-09-09
title: Memoria de los agentes — de «recall que devuelve texto» a memoria por capas con citas, uso y aprendizaje
status: approved
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 3 semanas
estimated_effort_person_days: 12
created_by: claude-fable-5-1-analysis-2026-09-09
docs_language: es
priority: P1
source_audit: análisis comparativo con thedotmack/claude-mem (2026-09-09) sobre el memorizer, el recall híbrido y las tools memory_* de la plataforma
---

# Memoria de los agentes (2026-09-09) — análisis y plan

## Cabecera

| Campo             | Valor                                                                                                                                                                 |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **ID del Plan**   | `memoria-agentes-2026-09-09`                                                                                                                                          |
| **Prioridad**     | P1                                                                                                                                                                    |
| **Bloqueado por** | Ninguno formal. Va detrás de `remediacion-marketplace-mcp-2026-09-02` y de `ui-reestructuracion-2026-09-09` (un solo plan `in_progress`); su UI la pinta `task_ui_31` |
| **Rama sugerida** | `plan/memoria-agentes-2026-09-09`                                                                                                                                     |
| **Método**        | TDD; los cambios de esquema son migraciones reversibles y aditivas; el recall nunca rompe un run (best-effort)                                                        |
| **Origen**        | Petición del operador: comprobar que los flujos de memoria «tienen sentido» y aplicar lo que valga de `claude-mem`; decisión delegada al asistente                    |

## Qué tenemos hoy (medido en el código, no en la doc)

La base es **mejor de lo que parecía desde la UI**:

- **Captura y destilado**: al terminar una ejecución (`done`) o una sesión de trabajo
  humana, `workers.memorize_execution` aplica una **política** (`policy.py`: estados
  elegibles, ámbito efectivo, tipo → ámbito) y **destila con LLM** los `steps_log` en
  candidatos `episodic`/`semantic` con `tags` y `entities`, saneados
  (`sanitize.py`) y escritos bajo tenant + ámbito (`persistence.py`).
- **Modelo** (`memory_entries`): `scope` (private / team_shared / project_shared /
  global), `type`, `content`, `embedding` (pgvector), `user_id`, `team_id`,
  `project_id`, **procedencia** (`source_execution_id`,
  `source_human_work_session_id`, `agent_id`), `tags`, `entities`, `metadata`.
  La procedencia que la UI no enseña **sí está en la fila**.
- **Recall híbrido con fusión RRF**: BM25 + vectorial + entidades
  (`memorizer/recall.py`), filtrado por ámbito y RLS. Es exactamente la «búsqueda
  híbrida semántica + palabra clave» que `claude-mem` presenta como su ventaja.
- **Dos tools para el agente** (`memory_recall`, `memory_store`) vía el API interno
  del runtime, y un **auto-recall al arrancar el run** que construye la consulta
  con el título, la descripción y los criterios de aceptación de la tarea y mete
  hasta N memorias en el preámbulo.
- **Higiene**: duplicados por similitud (`/memories/{id}/similar`), fusión
  (`merge-into`), motivos de omisión visibles (`/memories/skip-reasons`), backfill
  de embeddings.

## Qué hace `claude-mem` que nosotros no, y cuánto vale

| Idea de `claude-mem`                                                                                                                                                         | En la plataforma hoy                                                                                                                                          | Vale la pena  |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------- |
| **Revelación progresiva**: `search` devuelve un índice barato (id + titular, 50-100 tokens), `get_observations` trae el detalle sólo de los ids elegidos (~10× menos tokens) | `memory_recall` devuelve el **contenido completo** de cada hit; el auto-recall lo recorta a un tope de caracteres sin que el agente pueda pedir más           | **Sí, mucho** |
| **Citas por id**: cada observación tiene un número que el agente cita y que el visor resuelve                                                                                | `MemoryRecallHit` lleva `memory_id`, pero el runtime lo descarta antes de enseñárselo al modelo; no hay cita posible                                          | **Sí**        |
| **Línea de tiempo**: `timeline` devuelve los vecinos cronológicos de un hit («qué pasaba entonces»)                                                                          | No existe. La procedencia (`source_execution_id`) lo permitiría en una consulta                                                                               | **Sí**        |
| **Resumen por sesión** además de observaciones sueltas                                                                                                                       | El destilado produce observaciones (`episodic`/`semantic`); no hay un **resumen de la ejecución** como pieza propia (qué se hizo, decisiones, siguiente paso) | **Sí**        |
| **Tipos de observación** (bugfix, decision, pattern…) para filtrar                                                                                                           | `tags` libres. Sin vocabulario cerrado no se puede filtrar «sólo decisiones» ni pintar iconos                                                                 | Sí, barato    |
| **Coste en tokens visible** para que el agente decida cuánto leer                                                                                                            | No                                                                                                                                                            | Sí, barato    |
| **Etiqueta `<private>`** para excluir texto de la memoria                                                                                                                    | `sanitize.py` limpia secretos; no hay marca explícita del humano                                                                                              | Sí, barato    |
| **Visor en tiempo real** de la memoria                                                                                                                                       | La lista plana de `/admin/memories` (lo cubre `task_ui_31`)                                                                                                   | Lo cubre UI   |
| **Hooks de ciclo de vida del IDE**, observador SaaS, sincronización a la nube                                                                                                | Nuestro ciclo es el propio del sistema (ejecuciones), y la memoria no sale del tenant                                                                         | **No**        |

Y dos cosas que `claude-mem` **no** hace y que aquí faltan igualmente:

- **Uso y aprendizaje**: ninguna memoria sabe si se ha recuperado alguna vez ni si
  sirvió. Sin `recall_count`, `last_recalled_at` ni una señal de utilidad, el
  ranking no aprende y las memorias inútiles pesan lo mismo que las buenas para
  siempre.
- **Caducidad**: una memoria `episodic` de hace tres meses que nunca se ha
  recuperado sigue compitiendo en igualdad con una de ayer.

## Decisión (delegada por el operador el 2026-09-09)

Adoptar **la forma** de `claude-mem` —capas, citas, línea de tiempo, resumen por
sesión, tipos— sobre **nuestro** motor (pgvector + BM25 + RRF, RLS, ámbitos), y
añadir lo que ninguno de los dos tenía: **uso y utilidad** como señal de ranking
y de caducidad. Nada de infraestructura nueva (ni SQLite, ni Chroma, ni servicio
observador): todo cabe en `memory_entries`, el memorizer y las tools existentes.

**Lo que NO cambia**: los cuatro ámbitos y su regla (una memoria `private` de una
persona no la lee ni la escribe un agente — CLAUDE.md), la política de qué
ejecuciones producen memoria, el saneado, y que el recall es best-effort y nunca
rompe un run.

## Criterios de cierre del plan

1. Todas las casillas `[x]` con test en verde (unit del memorizer y del ranking,
   integración del recall por capas y de la línea de tiempo con caso cross-tenant,
   runtime con `ScriptedModelClient`).
2. Migraciones reversibles; `alembic downgrade` probado en CI.
3. Un run real en el stack de dev muestra en su preámbulo el índice de memorias
   con ids y el agente recupera el detalle de una con `memory_get`.
4. Entrada en `docs/07-changelog/memoria-agentes-2026-09-09.md`; `docs/04-reference/`
   de memoria actualizado con las tools y el modelo.
5. Tests humanos `human_mem_01..02` validados.

---

## Ola 1 — Capas, citas y línea de tiempo (P1 · ~4 d)

### `task_mem_01` — El recall devuelve un índice; el detalle se pide por id

- [ ] **Título**: `memory_recall` (tool del runtime + `POST /internal/agent/memory-recall`) pasa a
      devolver por hit `{id, headline, type, kind, scope, created_at, agent, project, score, tokens}`
      donde `headline` son los primeros ~120 caracteres del contenido y `tokens` una estimación del
      detalle. Nueva tool `memory_get(ids: list[str])` (+ `POST /internal/agent/memory-get`) que trae el
      contenido completo de hasta 20 ids, respetando ámbito y RLS. El auto-recall del arranque
      (`__main__.py`) pone en el preámbulo el **índice** (no el contenido) con la instrucción de pedir
      el detalle con `memory_get` cuando haga falta; el tope de N hits sube porque cada uno cuesta
      diez veces menos.
      **Test**: unit del formato del hit y de la estimación de tokens; integración de `memory-get`
      (ids de otro tenant o de ámbito privado ajeno no se devuelven); runtime: el preámbulo lleva ids y
      un `ScriptedModelClient` llama `memory_get` y recibe el contenido.
      **Coste**: 1,5 d.

### `task_mem_02` — Citas: la memoria que se usó queda registrada

- [ ] **Título**: cada `memory_get` y cada hit de `memory_recall` incrementan `recall_count` y fijan
      `last_recalled_at` (columnas nuevas, migración aditiva); además se escribe una fila en una tabla
      `memory_recall_log(memory_id, execution_id, agent_id, kind: index|detail, created_at)` con
      retención (misma familia append-only del ADR 0151). El runtime recomienda al agente citar
      `[mem:<id corto>]` en su informe cuando una memoria haya condicionado una decisión; el visor de
      la ejecución resuelve la cita a la memoria.
      **Test**: unit del incremento idempotente por run; integración del log bajo RLS; test del render
      de citas en el visor de runs (vitest).
      **Coste**: 1 d.

### `task_mem_03` — Línea de tiempo

- [ ] **Título**: nueva tool `memory_timeline(memory_id | execution_id, window=5)` (+ endpoint interno
      y `GET /memories/{id}/timeline`) que devuelve los vecinos cronológicos de una memoria dentro del
      mismo proyecto o equipo (según ámbito): las memorias de la misma ejecución y las de las
      ejecuciones inmediatamente anterior y posterior, como índice. Responde «qué estaba pasando cuando
      se aprendió esto».
      **Test**: integración con tres ejecuciones sembradas (orden, ventana, ámbito, cross-tenant).
      **Coste**: 1 d.

### `task_mem_04` — Tipos de observación y resumen por ejecución

- [ ] **Título**: `memory_entries.kind` (columna nueva, vocabulario cerrado: `summary`, `decision`,
      `bugfix`, `pattern`, `gotcha`, `preference`, `fact`; `type` episodic/semantic se conserva). El
      destilado pide al LLM **un** `summary` por ejecución (qué se hizo, decisiones, siguiente paso,
      ≤ 600 caracteres) más observaciones tipadas; el prompt y el parser se actualizan y las memorias
      existentes quedan `kind = NULL` (la UI enseña «sin clasificar»). `memory_recall` acepta filtro
      `kinds`. La etiqueta `<private>…</private>` en un texto humano (comentarios, chat) se retira antes
      de destilar (`sanitize.py`).
      **Test**: unit del parser (tipos válidos, uno y sólo un summary, `<private>` retirado); integración
      de la migración y del filtro por `kinds`.
      **Coste**: 1,5 d.

## Ola 2 — Uso, utilidad y caducidad (P1 · ~3 d)

### `task_mem_10` — Señal de utilidad y ranking que aprende

- [ ] **Título**: nueva tool `memory_feedback(memory_id, verdict: useful|wrong|outdated, note?)` que
      escribe `useful_count`/`harmful_count` (columnas nuevas) y, para `wrong`/`outdated`, marca la
      memoria como `disputed` sin borrarla. El recall añade un cuarto término a la fusión RRF:
      **utilidad** (`useful − harmful`, con suavizado) y **frescura** (decay exponencial sobre
      `last_recalled_at` o `created_at` para `episodic`; `semantic` y `summary` no decaen). Los pesos
      viven en `platform_settings` (`memory.rank_*`, tipo decimal) con defaults medidos en un eval.
      **Test**: unit del ranking (una memoria marcada `wrong` cae; una útil sube; el decay no afecta a
      `semantic`); integración de `memory_feedback` bajo RLS; eval de recall antes/después sobre el
      dataset dorado de evals (Plan 14) con el resultado en el changelog.
      **Coste**: 2 d.

### `task_mem_11` — Caducidad y compactación

- [ ] **Título**: job de mantenimiento (beat, `workers.maintenance`) que **archiva** (soft) las
      `episodic` sin `kind = summary`, con `recall_count = 0` y más de N días (default 90, ajuste de
      plataforma), y que **compacta** las memorias `disputed` de un mismo proyecto en una revisión que
      el operador confirma desde la UI (`task_ui_31` pinta la cola). Nunca borra: archiva con motivo y
      deja fila de auditoría.
      **Test**: unit de la selección (qué archiva y qué no); integración del job; el archivado no aparece
      en el recall pero sí en el visor con filtro.
      **Coste**: 1 d.

## Ola 3 — Que se vea y se pueda operar (P1 · ~2 d)

### `task_mem_20` — API y UI de memoria con la nueva forma

- [ ] **Título**: `GET /memories` acepta `kind`, `agent_id`, `execution_id`, `min_recall_count`,
      `disputed`, `archived` y devuelve `recall_count`, `last_recalled_at`, `useful_count`,
      `harmful_count`, `kind`; `GET /memories/{id}` devuelve la ficha con su procedencia resuelta
      (nombre del agente, título de la ejecución, proyecto) y su línea de tiempo. La UI de `task_ui_31`
      (memorias por ámbito con procedencia) consume estos campos y añade la pestaña «Disputadas» y el
      filtro «nunca recuperadas». El preámbulo del run y el visor de ejecución muestran las citas.
      **Test**: integración del listado con filtros (cross-tenant y privado ajeno); vitest de la ficha.
      **Coste**: 2 d.

---

## Tests humanos

| ID             | Qué valida                                                                                                                                   |
| -------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `human_mem_01` | Un run recibe el índice de memorias con ids en su preámbulo, pide el detalle de una y la cita en su informe; la cita se resuelve en el visor |
| `human_mem_02` | Marcar una memoria como errónea desde la UI la saca del primer puesto del recall en el siguiente run del mismo proyecto                      |

## Riesgos

- **El índice sin detalle empeora un agente que no pida el detalle**: el preámbulo lleva la instrucción
  explícita y el eval de `task_mem_10` mide el recall antes/después. Si empeora, el auto-recall vuelve
  a incluir el contenido de los 3 primeros hits y el índice del resto.
- **Coste del LLM del destilado** sube al pedir tipos y resumen: una llamada por ejecución igual que
  hoy, con un prompt más largo; se mide en el changelog.
- **Feedback envenenado por un agente**: `memory_feedback` sólo pesa dentro del mismo ámbito y tenant, y
  `disputed` nunca borra; el operador confirma la compactación.
- **Dos planes vecinos**: `task_ui_31` pinta lo que este plan expone. Regla: este plan entrega el dato y
  la API; el de UI, el sitio donde se ve.
