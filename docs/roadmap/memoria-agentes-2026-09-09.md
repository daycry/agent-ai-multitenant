---
plan_id: memoria-agentes-2026-09-09
title: Memoria de los agentes — de «recall que devuelve texto» a memoria por capas con citas, uso y aprendizaje
status: in_progress
blocking_plan: []
started_at: 2026-09-30
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

- [x] **Título**: `memory_recall` (tool del runtime + `POST /internal/agent/memory-recall`) pasa a
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
  - ✅ **Cerrada el 2026-09-30**, con el agente `implementer` del plugin. `POST /internal/agent/memory-recall`
    devuelve el índice `{id, headline, type, kind, scope, created_at, agent, project, score, tokens}` sin
    contenido (`memorizer/index_format.py`); `kind` sale `null` hasta `task_mem_04`. `POST /internal/agent/memory-get`
    trae el detalle de hasta 20 ids con el mismo filtro de ámbito que el recall, nunca `private`. La tool
    `memory_get` es de sistema y no pasa por el gate de aprobación, porque es lectura. El auto-recall sube de 5 a
    15 hits y pone el índice en el preámbulo, con la instrucción de pedir el detalle y de citar `[mem:<id corto>]`.
    Rojo visto: 7 tests del runtime (`test_memory_get.py`), el unit del formato y la integración (`KeyError: 'id'`).
    Tests existentes ajustados a la forma nueva (`content`→`headline`, `memory_id`→`id`).

### `task_mem_02` — Citas: la memoria que se usó queda registrada

- [x] **Título**: cada `memory_get` y cada hit de `memory_recall` incrementan `recall_count` y fijan
      `last_recalled_at` (columnas nuevas, migración aditiva); además se escribe una fila en una tabla
      `memory_recall_log(memory_id, execution_id, agent_id, kind: index|detail, created_at)` con
      retención (misma familia append-only del ADR 0151). El runtime recomienda al agente citar
      `[mem:<id corto>]` en su informe cuando una memoria haya condicionado una decisión; el visor de
      la ejecución resuelve la cita a la memoria.
      **Test**: unit del incremento idempotente por run; integración del log bajo RLS; test del render
      de citas en el visor de runs (vitest).
      **Coste**: 1 d.
  - ✅ **Cerrada el 2026-09-30.** Migración `0151_memory_recall_usage`, aditiva y reversible: `recall_count` y
    `last_recalled_at` en `memory_entries`, y la tabla `memory_recall_log`, particionada por mes (ADR 0151), con RLS
    en el padre y en cada partición, sin FK (ADR 0154) y registrada en `PARTITIONED_TABLES`. El uso cuenta una vez
    por run, aunque se repita el recall o se pidan índice y detalle, y se escribe en un SAVEPOINT: un fallo nunca
    rompe el recall. La ejecución se resuelve en el servidor a partir del `task_id` del token. `GET /memories`
    devuelve ya el `recall_count` real, que era lo que esperaba `task_ui_31`. El visor de la ejecución pinta las
    citas `[mem:…]` como chips resueltos con el filtro nuevo `id_prefix`. **Rojo verificado por el orquestador con
    mutaciones**, porque la tabla de log, la resolución por prefijo y el render de citas nacieron en verde:
    neutralizar la escritura de uso pone rojos tres tests de integración, y cambiar el marcador de cita, tres de
    vitest.

### `task_mem_03` — Línea de tiempo

- [x] **Título**: nueva tool `memory_timeline(memory_id | execution_id, window=5)` (+ endpoint interno
      y `GET /memories/{id}/timeline`) que devuelve los vecinos cronológicos de una memoria dentro del
      mismo proyecto o equipo (según ámbito): las memorias de la misma ejecución y las de las
      ejecuciones inmediatamente anterior y posterior, como índice. Responde «qué estaba pasando cuando
      se aprendió esto».
      **Test**: integración con tres ejecuciones sembradas (orden, ventana, ámbito, cross-tenant).
      **Coste**: 1 d.
  - ✅ **Cerrada el 2026-09-30.** Tool `memory_timeline`, `POST /internal/agent/memory-timeline` y
    `GET /memories/{id}/timeline` (`memorizer/timeline.py`). `window` cuenta ejecuciones vecinas con alguna memoria
    visible, de 1 a 20, 5 por defecto. Los vecinos comparten ámbito y dueño con el ancla. La búsqueda de ejecuciones
    se acota a ±30 días del ancla para podar particiones, así que una vecina más lejana no aparece (recorta el «inmediatamente anterior y posterior» del enunciado: en un proyecto con runs cada más de un mes, la línea de tiempo sale sin vecinas). El endpoint
    interno usa la derivación de ámbito del recall y nunca ve `private`. El público enseña las privadas sólo a su
    dueño. Un ancla ajena o inexistente da 404. Rojo visto: 4 de integración y 5 del runtime. El bloque del endpoint
    interno se reconstruyó tras borrarlo por error el script de `task_mem_01`. El orquestador revisó que reutiliza
    la derivación de ámbito del recall, y `test_memory_timeline.py` pasa.

### `task_mem_04` — Tipos de observación y resumen por ejecución

- [x] **Título**: `memory_entries.kind` (columna nueva, vocabulario cerrado: `summary`, `decision`,
      `bugfix`, `pattern`, `gotcha`, `preference`, `fact`; `type` episodic/semantic se conserva). El
      destilado pide al LLM **un** `summary` por ejecución (qué se hizo, decisiones, siguiente paso,
      ≤ 600 caracteres) más observaciones tipadas; el prompt y el parser se actualizan y las memorias
      existentes quedan `kind = NULL` (la UI enseña «sin clasificar»). `memory_recall` acepta filtro
      `kinds`. La etiqueta `<private>…</private>` en un texto humano (comentarios, chat) se retira antes
      de destilar (`sanitize.py`).
      **Test**: unit del parser (tipos válidos, uno y sólo un summary, `<private>` retirado); integración
      de la migración y del filtro por `kinds`.
      **Coste**: 1,5 d.
  - ✅ **Cerrada el 2026-09-30.** Migración `0152_memory_entries_kind`, aditiva, reversible y transaccional:
    columna `kind` nullable con `CHECK` de vocabulario cerrado; las memorias anteriores quedan `NULL` y el panel
    enseña «sin clasificar». El destilado pide un `summary` (≤ 600 caracteres) más observaciones tipadas. **Pensado
    para modelos de ~9B** (ADR 0169): el parser acepta texto o vallas alrededor del JSON, un envoltorio o un item
    suelto, `kind` en mayúsculas, con acentos o en español; un `kind` inventado pasa a `fact`; con varios `summary`
    gana el primero y el resto pasa a `fact`; sin ninguno, la ejecución queda sin resumen y las observaciones se
    conservan. `<private>…</private>` se retira antes de destilar, también sin cerrar. El filtro `kinds` está en
    `recall()`, en `/memory-recall` y en la tool, y es tolerante: lo desconocido se descarta, nunca un 422. Rojo
    visto en unit, esquema, runtime y vitest; la integración nació en verde y **el orquestador la verificó con una
    mutación**: sin el filtro `kinds`, tres tests de `test_memory_kinds.py` en rojo.

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
  - ⏳ **Implementada el 2026-09-30; la casilla sigue abierta por el eval del dataset dorado.** Tool
    `memory_feedback` (`useful|wrong|outdated`): un voto por memoria y votante, que se reescribe si cambia, y
    contadores recalculados desde `memory_feedback`. `wrong`/`outdated` marcan `disputed_at` sin borrar. El recall
    del agente suma utilidad (suavizada) y frescura (vida media de 60 días, sólo para `episodic`; `semantic` y
    `summary` no decaen) y penaliza lo disputado. Los pesos `memory.rank_*` están en `platform_settings` con cotas;
    con los pesos a 0 el orden es el de antes, y si falla la lectura de los pesos el recall sigue con RRF puro.
    `memory_feedback` es escritura y se gatea como `memory_store` (`code_changes`). **Eval:** el dataset dorado del
    Plan 14 califica prompts con un juez LLM y no mide el recall, así que se midió sobre un conjunto sintético
    reproducible (`tests/unit/test_memory_rank_eval.py`, semilla fija, 400 consultas). Con feedback informativo el
    MRR pasa de 0,518 a 0,551; con feedback de puro ruido baja de 0,542 a 0,515. Los defaults (utilidad 0,15,
    frescura 0,05) salen de una rejilla que limita esa pérdida a 0,03. **La penalización de lo disputado
    (`disputed = 1,0`) no sale de la rejilla**, cuyo óptimo la deja en 0: se fija alta porque el plan exige que una
    memoria marcada errónea caiga, y para que un solo voto de un agente no baste se exige el acuerdo de varias
    ejecuciones o un voto humano (revisión adversarial, intento 1). **Este eval tampoco mide el riesgo de
    `task_mem_01`** (que un agente que no pide el detalle rinda peor con sólo el índice): compara pesos de
    ranking, no índice frente a contenido. **Decide el operador** si el eval sintético sustituye al del dataset
    dorado o si hace falta un dataset de recall, que mediría también ese riesgo. Rojo visto en unit y runtime; la integración nació
    en verde y se verificó con mutaciones: sin pesos en `/memory-recall`, los dos tests de ranking se ponen rojos.

### `task_mem_11` — Caducidad y compactación

- [x] **Título**: job de mantenimiento (beat, `workers.maintenance`) que **archiva** (soft) las
      `episodic` sin `kind = summary`, con `recall_count = 0` y más de N días (default 90, ajuste de
      plataforma), y que **compacta** las memorias `disputed` de un mismo proyecto en una revisión que
      el operador confirma desde la UI (`task_ui_31` pinta la cola). Nunca borra: archiva con motivo y
      deja fila de auditoría.
      **Test**: unit de la selección (qué archiva y qué no); integración del job; el archivado no aparece
      en el recall pero sí en el visor con filtro.
      **Coste**: 1 d.
  - ✅ **Cerrada el 2026-09-30.** Job diario `workers.maintain_memory_lifecycle` (04:10, importado en la fachada de
    `workers.maintenance`, así que no cae en la trampa del beat sin importar). Archiva con motivo y fila de
    `audit_log` las `episodic` que no son `summary`, tienen `recall_count = 0` y superan `memory.archive_after_days`
    (90 por defecto). Sólo toca `team_shared`, `project_shared` y `global`: nunca `private`, ni las memorias del
    córtex, cuyas lecturas no suman `recall_count`. Compacta las `disputed` de cada proyecto en una propuesta
    pendiente que no aplica nada; la confirma o la rechaza un `tenant_admin` con `GET /memories/compactions` y
    `POST …/{id}/confirm|reject`, que dejan auditoría. «Compactar» es archivar el grupo confirmado, sin fundir su contenido en una memoria corregida: el plan no dice cómo consolidar y no se inventa. El recall, `memory_get`, los contadores y la línea de tiempo
    del agente excluyen lo archivado; `GET /memories?archived=true` lo enseña. Migración `0153`, aditiva y
    reversible, con dos tablas nuevas bajo RLS. Rojo visto en el unit de la selección; la integración se verificó
    con una mutación: sin `archived_at IS NULL`, el test del recall se pone rojo.

## Ola 3 — Que se vea y se pueda operar (P1 · ~2 d)

### `task_mem_20` — API y UI de memoria con la nueva forma

- [x] **Título**: `GET /memories` acepta `kind`, `agent_id`, `execution_id`, `min_recall_count`,
      `disputed`, `archived` y devuelve `recall_count`, `last_recalled_at`, `useful_count`,
      `harmful_count`, `kind`; `GET /memories/{id}` devuelve la ficha con su procedencia resuelta
      (nombre del agente, título de la ejecución, proyecto) y su línea de tiempo. La UI de `task_ui_31`
      (memorias por ámbito con procedencia) consume estos campos y añade la pestaña «Disputadas» y el
      filtro «nunca recuperadas». El preámbulo del run y el visor de ejecución muestran las citas.
      **Test**: integración del listado con filtros (cross-tenant y privado ajeno); vitest de la ficha.
      **Coste**: 2 d.
  - ✅ **Cerrada el 2026-09-30.** `GET /memories` suma los filtros `min_recall_count`, `max_recall_count`
    (`max_recall_count=0` significa «nunca recuperada») y `disputed`, más los campos `last_recalled_at` y
    `disputed_at`. `GET /memories/{id}` devuelve la ficha con procedencia resuelta (agente, proyecto y título de la
    tarea de la ejecución), el voto propio y la línea de tiempo embebida. `POST /memories/{id}/feedback` es el voto
    humano, uno por persona, que necesita `human_mem_02`. Una privada ajena, otro tenant o una memoria archivada dan
    404 sin escribir. En el panel: ruta `/admin/memories/[id]`, pestaña «Disputadas», casilla «Nunca recuperadas»,
    botones de voto, la cola de compactación para `tenant_admin`, y citas del visor que enlazan a la ficha. No hizo
    falta migración: `memory_feedback` ya admitía votante humano. Rojo visto en integración (6) y vitest (5).
    **Verificado por el orquestador:** vitest completo 1823 de 1823, integración de la API de memorias 54 de 54, e2e
    de memorias y del visor 10 de 10 con build de producción. Tres tests de la página fallaban porque sus mocks no
    respondían a `/me`, que la página ahora lee para la cola; se corrigieron con el mock de `useCurrentUser` que
    usan otros tests del repo.

---

## Revisión adversarial del plan

**Intento 1 (2026-09-30)**, skill `adversarial-review` del plugin sobre `1df542e5..3b90fb63`, con las lentes
A, B, C y D y el agente `reviewer`. **Ninguna fuga de `private` ni entre tenants.** La lente A confirma las seis
casillas marcadas contra su enunciado y su test, y confirma que `task_mem_10` debe seguir abierta: el dataset del
Plan 14 no mide el recall, y cerrarla con el eval sintético contradiría la regla de no marcar `[x]` sin su test.

| #   | Grado     | Lente | Gap                                                                                                    | Tarea          | Estado                                           |
| --- | --------- | ----- | ------------------------------------------------------------------------------------------------------ | -------------- | ------------------------------------------------ |
| 1   | Critical  | C, D  | La primera pasada del archivado se llevaría todo el histórico, que nació con `recall_count = 0`        | mem_11         | corregido en código, integración sin verificar   |
| 2   | Important | C, A  | Un solo voto `wrong` de un run hunde una memoria `global` para siempre y no se puede retirar           | mem_10         | corregido en código, integración sin verificar   |
| 3   | Important | B     | El parser pierde un item suelto con `tags`, el summary de un envoltorio y el JSON truncado             | mem_04         | corregido en código, integración sin verificar   |
| 4   | Important | B, C  | `<private>` anidado, con espacio o con atributos llega a la memoria compartida (CWE-184)               | mem_04         | corregido en código, integración sin verificar   |
| 5   | Important | D     | La cola de compactación pide la ficha completa de cada memoria, y cada voto la recarga                 | mem_20         | corregido en código, integración sin verificar   |
| 6   | Important | A     | La penalización `disputed = 1,0` no estaba medida y la nota lo callaba                                 | mem_10         | corregido: nota y regla de acuerdo               |
| 7   | Important | A     | El eval no mide el riesgo de `task_mem_01` (índice frente a contenido)                                 | mem_10         | corregido: anotado para la decisión del operador |
| 8   | Minor     | B     | Un id mal formado tumba el lote de `memory_get`                                                        | mem_01         | corregido en código, integración sin verificar   |
| 9   | Minor     | B, D  | Citas del visor: una petición por chip y un prefijo ambiguo resuelve a una memoria cualquiera          | mem_02         | corregido en código, integración sin verificar   |
| 10  | Minor     | B     | Uso contado sobre memorias archivadas y doble conteo con llamadas concurrentes del mismo run           | mem_02         | corregido en código, integración sin verificar   |
| 11  | Minor     | B     | Carrera entre el job de compactación y confirmar o rechazar                                            | mem_11         | corregido en código, integración sin verificar   |
| 12  | Minor     | D     | Faltan índices en `source_execution_id` y para el predicado del archivado; auditoría fila a fila       | mem_03, 11     | corregido en código, integración sin verificar   |
| 13  | Minor     | D     | El uso reescribe `last_recalled_at` en cada llamada y lee el log sin acotar particiones                | mem_02         | corregido en código, integración sin verificar   |
| 14  | Minor     | B     | La demo del test humano 04.5 lee la forma vieja del hit                                                | mem_01         | corregido en código, integración sin verificar   |
| 15  | Minor     | B     | `memory_timeline` cuenta como investigación estéril; «bug fix» con espacio no se normaliza             | mem_03, 04     | corregido en código, integración sin verificar   |
| 16  | Minor     | C     | Un texto todo `<private>` daba 500 al crear la memoria                                                 | mem_04         | corregido en código, integración sin verificar   |
| 17  | Minor     | A     | `propose_compactions` no excluía `private` explícitamente                                              | mem_11         | corregido en código, integración sin verificar   |
| 18  | Minor     | A     | Cifra de la nota (0,543 en vez de 0,542), el recorte de ±30 días y «compactar = archivar» sin escribir | mem_03, 10, 11 | corregido: notas                                 |

**Estado al 2026-09-30 por la tarde.** Las correcciones de los gaps 1 a 5 y 8 a 17 están en el código:
arranque del archivado desde `memory.usage_tracking_since`, disputa sólo con varias ejecuciones o un voto
humano, parser que conserva el item suelto, el envoltorio y el JSON truncado, `<private>` con anidamiento y
atributos, cola de compactación sin N+1, `GET /memories/resolve` para las citas, `memory_get` tolerante,
índices de la migración `0154_memory_indexes`, y el resto. **Los dos implementadores se cortaron por el límite
de uso antes de su informe final**, y la integración no se ha podido verificar porque Docker Desktop no arranca
en la máquina, y la BD de tests vive en él. Lo verificado por el orquestador sin Docker: runtime 815 passed;
unit, docs y seguridad 6855 passed (tres rojos de entorno que pasan al repetirlos solos); vitest 1826 de 1827
(el rojo es el test de `team-picker` que falla por carga y pasa solo); `tsc`, eslint, `check-i18n`,
`check-component-size`, `ruff` y `mypy_gate` limpios. **Pendiente al volver:** correr la integración de memoria
(`tests/integration/test_memor*.py` y `test_memory_*.py`, incluida la migración 0154 con up→down→up y
`test_memory_review_fixes.py`) y lanzar el intento 2 de la revisión.

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
