---
title: "Córtex F2 — Modelo afectivo computacional (PAD + drives) + Panel de Mente"
status: pending_human_validation
blocking_plan:
  [
    "docs/roadmap/cortex-system-owner.md (F1) — IMPLEMENTADO",
    "docs/05-architecture-decisions/0075-modelo-afectivo-computacional-cortex.md",
  ]
started_at: 2026-06-23
completed_at: null
date: 2026-06-23
related_adrs: ["0075", "0074", "0070", "0021", "0056"]
docs_language: es
next_migration: "0092"
gated: false
---

# Córtex F2 — Modelo afectivo computacional + Panel de Mente

> **Auditoría 2026-07-27 — las casillas de este plan se verificaron una a una
> contra el código.** Las marcadas `[x]` lo están con evidencia `file:line` y una
> segunda pasada adversarial; las que siguen sin marcar tienen su hueco concreto
> descrito en
> [`gaps-cortex-2026-07-27.md`](gaps-cortex-2026-07-27.md) (informe:
> [`auditoria-cortex-2026-07-27.md`](auditoria-cortex-2026-07-27.md)).
> Antes de implementar una casilla sin marcar, **abre el fichero**: la pasada
> adversarial dio al menos un falso positivo comprobado.

> **✅ IMPLEMENTADO Y DESPLEGADO** (verificado 2026-07-06 — auditoría de estado del roadmap). El
> banner "GATED — bloqueado por F1 sin código" quedó congelado desde el commit de diseño `cf8f7cd`;
> F1 SÍ existe y F2 se implementó encima: `cortex/affective.py`, `affect_store.py`, `affect_cache.py`,
> migración `0093_cortex_affect`, worker `cortex_affect.py` (en `beat_schedule.py`), router
> `cortex_mind.py` (`/mind`, `/affect/timeseries`, `/episodes`), WS `cortex_ws.py`, con
> `test_cortex_affective*`/`test_cortex_affect_store.py`/`test_cortex_affect_cache.py`/
> `test_cortex_affect_task.py` en verde. Ver [cortex-identidad-real.md](cortex-identidad-real.md)
> para el cierre del lazo "el afecto modula el texto" (§2 de este plan lo dejaba solo prometido).
> Checkboxes de tareas NO re-verificados línea a línea; el status refleja el veredicto agregado.

## Objetivo

Dar al córtex un **estado afectivo continuo, determinista y auditable** (PAD + drives homeostáticos) que evoluciona turno a turno mediante un **distilador asíncrono** (Celery + Ollama local, fail-open), persistido como snapshots y servido en vivo a un **Panel de Mente** con copy honesto, sin bloquear nunca la respuesta al owner.

## Arquitectura (3-5 frases)

El **motor PAD** (`cortex_affective.py`) es código puro testeable FUERA del LLM: decay lazy en lectura hacia el baseline (homeostasis), update por evento (aplica un delta PAD), EWMA del mood, clamps duros y baseline/set-point clampeado; el estado vivo de la emoción reside en **Redis** `cortex:affect:{owner}` con decay calculado en lectura (no por timer). Tras cada turno persistido en `cortex_turns` (F1), un **Celery task post-turno** (`workers/cortex_affect.py`, Ollama local, sin egress) puntúa `turno + drives + identidad → delta PAD + razón`; el motor aplica el delta de forma determinista, escribe un snapshot a `cortex_affect_snapshots` y publica un frame de telemetría en el stream Redis `cortex:telemetry:{owner}` — **fail-open**: Ollama caído ⇒ delta=0, el turno ya respondió. Toda lectura de las tablas `cortex_*` (tenant-less sobre BYPASSRLS, ADR 0074) **filtra `owner_user_id` explícito en SQL** y se prueba con un test cross-owner. El **Panel de Mente** (en `app/admin/cortex`) muestra diales PAD en vivo (WS), espacio PAD 2D con estela, gráfico de mood, mapa afectivo de episodios (hover = `appraisal_reason`) y barras de drives, con el rótulo honesto "modelo computacional de afecto, no sentimientos reales" siempre visible; el mood del último snapshot **sesga el system_prompt** del siguiente turno (modula tono y `reasoning_effort`, nunca bloquea).

## Tablas nuevas

### `cortex_affect_snapshots` (tenant-less / singleton del owner, BYPASSRLS — ADR 0074)

Columnas clave:

- `id` UUID PK (uuid7, `UUIDPrimaryKeyMixin`).
- `owner_user_id` UUID NOT NULL — FK lógica a `users.id` (el system_owner). **Aislamiento explícito**: todo SELECT filtra por esta columna.
- `valence` DOUBLE PRECISION NOT NULL — `[-1,1]`.
- `arousal` DOUBLE PRECISION NOT NULL — `[0,1]`.
- `dominance` DOUBLE PRECISION NOT NULL — `[-1,1]`.
- `intensity` DOUBLE PRECISION NOT NULL — `[0,1]`.
- `mood_valence`, `mood_arousal`, `mood_dominance` DOUBLE PRECISION NOT NULL — EWMA lento (la "capa mood").
- `mood_label` String(32) NOT NULL — etiqueta categórica derivada SOLO para UI.
- `drives` JSONB NOT NULL server_default `'{}'::jsonb` — `{curiosity,bonding,coherence,competence} ∈ [0,1]`.
- `appraisal_reason` Text NULL — razón emitida por el distilador (NULL si fail-open delta=0).
- `source_turn_id` UUID NULL — back-link a `cortex_turns.id` (F1) del turno que disparó el snapshot; NULL para snapshots de decay/mantenimiento.
- `created_at` TIMESTAMPTZ (`TimestampMixin`; **sin** `updated_at` ni soft-delete — son inmutables append-only).

Índices:

- `ix_cortex_affect_snapshots_owner_created` sobre `(owner_user_id, created_at DESC)` — sirve `/affect/timeseries` y el "último snapshot".
- `ix_cortex_affect_snapshots_owner_mood_label` sobre `(owner_user_id, mood_label)` — sirve `/episodes?emotion=`.
- UNIQUE parcial `uq_cortex_affect_snapshot_per_turn` sobre `(source_turn_id)` `WHERE source_turn_id IS NOT NULL` — **idempotencia**: una re-entrega del distilador para el mismo turno no duplica snapshot.

> NOTA: La **episódica emocional** (puntuaciones que ve el owner por mensaje) vive en `memory_entries` con `metadata_.emotion={valence,arousal,dominance,intensity,mood_label,appraisal_reason}` (ADR 0077, reutiliza esquema existente, NO se toca). `cortex_affect_snapshots` es la **serie temporal del estado del motor** (mood + drives + emoción muestreada), distinta de la episódica por-memoria. `/episodes?emotion=` lee de `memory_entries` (episódica), `/affect/timeseries` lee de `cortex_affect_snapshots`.

## Endpoints / WS (todos gated `require_system_owner`, DB-authoritative)

- `GET /owner/cortex/mind` → snapshot vivo: emoción actual (Redis con decay lazy aplicado), último mood/drives de `cortex_affect_snapshots`, `mood_label`, y bloque `honesty` con el copy honesto. 200.
- `GET /owner/cortex/affect/timeseries?since=&until=&limit=` → lista de snapshots `(created_at, valence, arousal, dominance, intensity, mood_*, mood_label, drives)` filtrada por `owner_user_id`, orden cronológico. Para el gráfico de mood y el espacio 2D con estela.
- `GET /owner/cortex/episodes?emotion=&limit=` → memorias episódicas emocionales del owner desde `memory_entries` (scope=private, user*id=owner, metadata*.cortex=true, metadata\_.emotion presente), filtradas por `mood_label==emotion` cuando se pasa `emotion`. Cada item incluye `appraisal_reason` para el hover del mapa.
- `WS /ws/owner/cortex/telemetry` → tail del stream Redis `cortex:telemetry:{owner}`; reenvía cada frame `{type:'affect', valence, arousal, dominance, intensity, mood_label, drives, appraisal_reason, occurred_at}`. Gate de WS clonando `_resolve_principal` + check DB-authoritative de owner (cierre 1008 si falla).

---

## FASE A — Migración + modelo ORM de `cortex_affect_snapshots`

- [x] **Test de migración up/down + invariantes de tabla**
  - Crear `tests/integration/test_cortex_affect_migration.py`.
  - TDD: escribe test que (a) `alembic upgrade head` crea `cortex_affect_snapshots` con las columnas y los 3 índices; (b) inserta dos snapshots con `source_turn_id` distinto y verifica que un segundo INSERT con el MISMO `source_turn_id` viola `uq_cortex_affect_snapshot_per_turn`; (c) `alembic downgrade -1` elimina la tabla; (d) la tabla NO tiene política RLS (es tenant-less, accedida por BYPASSRLS) — assert vía `pg_class.relrowsecurity = false`.
  - Falla (tabla no existe) → implementa migración → pasa → commit.
  - Ficheros: crear `apps/api-server/migrations/versions/20260623_0092_cortex_affect_snapshots.py` (revision `0092_cortex_affect_snapshots`, `down_revision="0091_system_owner_f0"`). `upgrade()` crea tabla + índices (patrón de `20260618_0084_memory_entities.py`); `downgrade()` los retira. NO añadir `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` (consciente: tenant-less BYPASSRLS).
  - Aceptación: `alembic upgrade head` y `downgrade -1` pasan; el índice UNIQUE parcial rechaza el duplicado por turno.

- [x] **Modelo ORM `CortexAffectSnapshot`**
  - TDD: en el test anterior, importa `from api_server.db.cortex_affect import CortexAffectSnapshot` y verifica `__tablename__`, columnas y que `Base.metadata` lo incluye (autogenerate-clean: `alembic check` no detecta drift).
  - Ficheros: crear `apps/api-server/src/api_server/db/cortex_affect.py` (hereda `Base, UUIDPrimaryKeyMixin, TimestampMixin`; NO `TenantScopedMixin`, NO `SoftDeleteMixin`); registrar import en `apps/api-server/migrations/env.py` (junto a `from api_server.db import models`) y en `apps/api-server/src/api_server/db/models.py` (añadir `from api_server.db.cortex_affect import CortexAffectSnapshot  # noqa: F401`) para que `Base.metadata` lo cargue.
  - Aceptación: el modelo mapea la tabla 1:1; `alembic check` limpio.

## FASE B — Motor PAD determinista (código puro, FUERA del LLM)

- [x] **`PADState`, `Drives` y constantes de dinámica**
  - TDD: crear `apps/api-server/tests/unit/test_cortex_affective.py`; test que construye `PADState(valence, arousal, dominance, intensity)` y `Drives(curiosity, bonding, coherence, competence)` y verifica que el constructor **clampa** cada eje a su rango (`valence/dominance∈[-1,1]`, resto `∈[0,1]`).
  - Falla (módulo no existe) → implementa dataclasses frozen + clamps → pasa → commit.
  - Ficheros: crear `apps/api-server/src/api_server/assistant/cortex_affective.py`. Dataclasses `frozen=True`; helper `_clamp(x, lo, hi)`; constantes `DECAY_HALF_LIFE_S` (emoción→baseline), `MOOD_EWMA_ALPHA=0.98` (ADR 0075), `DRIVE_DECAY_PER_HOUR`, `MOOD_FLOOR`/`MOOD_CEIL` (evitar "depresión/manía"), `BASELINE_MAX_DELTA_PER_REFLECTION` (clamp del set-point, lo usará F3).
  - Aceptación: rangos garantizados por construcción; valores fuera de rango se recortan, no lanzan.

- [x] **`decay_emotion(state, baseline, elapsed_s)` — decay lazy hacia baseline**
  - TDD: test que con `elapsed_s=0` devuelve el estado igual; con `elapsed_s` grande converge al `baseline`; es monótono hacia el baseline y nunca lo cruza (homeostasis). Test de propiedad: tras `2*half_life` la distancia al baseline es ≈1/4.
  - Implementa decay exponencial por eje hacia el baseline (set-point), determinista.
  - Aceptación: convergencia correcta y estable; sin oscilación.

- [x] **`apply_event(state, delta, baseline)` — update por evento + clamps**
  - TDD: test que aplicar un `delta` positivo sube valence pero recorta al techo; `delta` cero deja el estado intacto (camino fail-open); `intensity` sube con la magnitud del delta y decae con el tiempo.
  - Aceptación: el delta del distilador se integra de forma determinista y siempre clampeada.

- [x] **`update_mood(mood, emotion)` — EWMA lento + piso/techo**
  - TDD: test que `mood' = α·mood + (1-α)·emotion` con `α=0.98`; tras muchas iteraciones con emoción extrema, el mood se satura en `MOOD_FLOOR`/`MOOD_CEIL`, nunca alcanza el extremo de la emoción.
  - Aceptación: el mood se mueve lento y queda dentro de los límites de temperamento.

- [x] **`decay_drives(drives, elapsed_s)` + `satisfy_drive(drives, name, amount)`**
  - TDD: test que los drives decaen hacia 0 con el tiempo (motor de la curiosidad) y `satisfy_drive` los sube clampeado a `[0,1]`; un drive desconocido es no-op.
  - Aceptación: drives observables y saciables de forma determinista (su capacidad de DISPARAR comportamiento llega en F4; aquí son estado).

- [x] **`derive_mood_label(mood)` — etiqueta categórica SOLO-UI (ES/EN)**
  - TDD: test parametrizado con cuadrantes PAD canónicos → etiqueta esperada (p.ej. valence alto + arousal alto ⇒ "alegría"/"joy"; valence bajo + arousal bajo ⇒ "abatimiento"/"down"); idioma vía parámetro `language ∈ {es,en}`.
  - Aceptación: mapeo PAD→label determinista, bilingüe, documentado como derivado (no fuente de verdad).

- [x] **Suite de calibración (interacciones canónicas → rangos PAD esperados)**
  - TDD: crear `apps/api-server/tests/unit/test_cortex_affective_calibration.py`; tabla de ~8 escenarios canónicos (elogio del owner, crítica, pregunta curiosa que sacia `curiosity`, despedida fría que baja `bonding`, etc.) con `delta` esperado en rangos; aplica `apply_event`+`update_mood` y asserta que el estado cae en el rango esperado.
  - Aceptación: regresión que detecta cambios involuntarios en la dinámica (ADR 0075 §7).

## FASE C — Estado vivo en Redis (decay lazy en lectura)

- [x] **`CortexAffectStore` (Redis) — read aplica decay, write persiste timestamp**
  - TDD: crear `apps/api-server/tests/unit/test_cortex_affect_store.py` con `fakeredis`; test que `write(owner, state, mood, drives)` guarda en `cortex:affect:{owner}` (JSON) con `updated_at`; `read(owner)` recupera y **aplica `decay_emotion`/`decay_drives`** según el tiempo transcurrido desde `updated_at` (decay lazy, no timer); owner sin estado → devuelve el baseline neutro inicial.
  - Falla → implementa → pasa → commit.
  - Ficheros: crear `apps/api-server/src/api_server/assistant/cortex_affect_store.py`. Clave `cortex:affect:{owner_user_id}`; usa `get_redis()` de `auth/deps.py`; opcional TTL largo (el decay lazy hace que un estado viejo lea ≈baseline igualmente).
  - Aceptación: el dial PAD que verá el endpoint refleja el decay sin proceso de fondo; aislamiento por clave-por-owner.

- [x] **Frame de telemetría + stream Redis `cortex:telemetry:{owner}`**
  - TDD: añade test en el store: `publish_affect_frame(owner, frame)` hace `xadd` en `cortex:telemetry:{owner}` con `maxlen` aproximado (patrón `events.py`); `delete_affect_streams(owner)` limpia (best-effort).
  - Ficheros: extender `apps/api-server/src/api_server/events.py` con `cortex_telemetry_stream_key(owner_user_id)` + `publish_cortex_affect_event(redis, owner_user_id, *, payload)` (best-effort, nunca lanza), espejo de `publish_conversation_event`.
  - Aceptación: el WS podrá tailear el stream; publicación tolerante a fallos.

## FASE D — Distilador afectivo asíncrono (Celery, Ollama local, fail-open)

- [x] **Worker `cortex_distill_affect` — núcleo async testeable con LLM inyectable**
  - TDD: crear `apps/workers/tests/test_cortex_affect_task.py`; test del core `_distill_affect_async(turn_id, *, settings, llm_factory, affective)` con un `llm_factory` falso que devuelve un JSON `{delta:{valence,arousal,dominance,intensity}, reason, drive_satisfied}`: verifica que (a) lee el turno de `cortex_turns` filtrando `owner_user_id`; (b) llama al motor `apply_event`/`update_mood`/`satisfy_drive`; (c) escribe el estado en Redis y un snapshot en `cortex_affect_snapshots` (idempotente por `source_turn_id`); (d) publica el frame de telemetría; (e) escribe la episódica emocional en `memory_entries` vía `persist_memory_candidates` DIRECTO (metadata*.cortex=true, metadata*.emotion=…), NO vía `workers/memorizer.py`.
  - **Test fail-open**: con un `llm_factory` que lanza/timeout ⇒ delta=0, el snapshot se escribe con el estado decaído y `appraisal_reason=NULL`, y la task devuelve `ok:fail_open` (no propaga). Espejo de la tolerancia de `workers/memorizer.py`.
  - **Test idempotencia**: re-entrega del mismo `turn_id` no duplica snapshot (captura `UniqueViolation` → `ok:already_distilled`).
  - Falla → implementa → pasa → commit.
  - Ficheros: crear `apps/workers/src/workers/cortex_affect.py`. `@app.task(name="workers.cortex_distill_affect")` → `asyncio.run(_distill_affect_async(...))`; `_default_llm_factory` = `OllamaProvider(base_url=settings.cortex_affect_llm_base_url, default_model=settings.cortex_affect_llm_model)` (patrón `_default_llm_factory` de `workers/memorizer.py`); usa `create_async_engine(settings.database_url)` (BYPASSRLS) + sessionmaker; `_distill_affect_async` envuelto en `try/except` global que loguea y devuelve `error:` (nunca crashea el worker). El prompt al distilador incluye turno + drives + identidad y pide SOLO el JSON de delta+razón.
  - Aceptación: distila o falla-abierto; idempotente; sin egress (Ollama local); el catálogo LLM cerrado (ADR 0021) intacto.

- [x] **Settings del worker + registro del módulo**
  - TDD: test en `apps/workers/tests/test_cortex_affect_task.py` (o un `test_config`) que `Settings()` expone `cortex_affect_llm_base_url` (default Ollama local) y `cortex_affect_llm_model`, y que `cortex_distill_affect` está en `app.conf.imports`.
  - Ficheros: extender `apps/workers/src/workers/config.py` (dos `Field` nuevos, sección "Córtex F2"); añadir `"workers.cortex_affect"` a `imports` en `apps/workers/src/workers/celery_app.py`.
  - Aceptación: la task se registra al boot; URL/modelo operator-tunable, default local sin egress.

- [x] **Trigger post-turno `trigger_cortex_distill_affect(turn_id)`**
  - TDD: test que con un `turn_id` válido encola `cortex_distill_affect` en la cola `default`; un broker caído (mock que lanza) es swallowed y devuelve `False` (nunca rompe el turno). Espejo de `trigger_memorize` en `workers/memorizer.py`.
  - Ficheros: añadir `trigger_cortex_distill_affect` en `apps/workers/src/workers/cortex_affect.py`; **cablear la llamada en el endpoint del turno del córtex de F1** (`apps/api-server/src/api_server/routers/cortex.py`, fichero creado en F1) justo después de persistir el turno — fire-and-forget, fuera del hot-path. (Si F1 expone un seam de "post-turn hook", usarlo; si no, llamar el trigger tras el commit del turno.)
  - Aceptación: el appraisal sale del hot-path; el dial se actualiza ~1-2s tras la respuesta (aceptado por ADR 0075).

## FASE E — Lectura afectiva en el siguiente turno (mood sesga el prompt)

- [x] **`augment_system_prompt_with_affect(base, *, mood_label, drives, language)`**
  - TDD: crear `apps/api-server/tests/unit/test_cortex_affect_prompt.py`; test que con un mood "calmado" inyecta una sección honesta ("tu estado afectivo computacional actual es…") que sesga el TONO (no inventa sentimientos), bilingüe; con drives bajos añade una pista de curiosidad; sin estado, devuelve `base` intacto. Modela el patrón de `assistant/memory.py::augment_system_prompt`.
  - Ficheros: extender `apps/api-server/src/api_server/assistant/cortex_affective.py` (o un helper junto al prompt del córtex creado en F1). Devolver también el `reasoning_effort` sugerido (modulación ADR 0070) como dato, sin forzar.
  - Aceptación: el prompt del siguiente turno refleja el mood/drives leídos de Redis; copy honesto; nunca bloquea.

- [x] **Cableado de lectura afectiva en la percepción del turno del córtex (F1)**
  - TDD: test de integración (en la suite del córtex de F1) que un turno tras un snapshot afectivo guardado produce un system_prompt que contiene la sección de afecto (assert sobre el prompt construido, con `ScriptedAssistantModel`).
  - Ficheros: modificar el armado del prompt en `apps/api-server/src/api_server/routers/cortex.py` (F1): leer `CortexAffectStore.read(owner)` y aplicar `augment_system_prompt_with_affect` antes de `run_assistant_turn`.
  - Aceptación: el afecto modula el turno siguiente; degrada limpio si Redis no tiene estado (baseline neutro).

## FASE F — Endpoints REST `/owner/cortex/*` (gated, cross-owner test)

- [x] **Router `cortex_mind` + `GET /owner/cortex/mind`**
  - TDD: crear `apps/api-server/tests/integration/test_cortex_mind_endpoints.py`; test que (a) un no-owner recibe 403 (gate `require_system_owner` DB-authoritative, patrón `test_cortex_f0_ownership.py`); (b) el owner recibe 200 con `valence/arousal/dominance/intensity` (Redis con decay aplicado), `mood_label`, `drives` y el bloque `honesty`. **Test cross-owner**: con dos owners simulados, el endpoint del owner A nunca devuelve el estado de B (filtro `owner_user_id`).
  - Falla → implementa → pasa → commit.
  - Ficheros: crear `apps/api-server/src/api_server/routers/cortex_mind.py` (`APIRouter(prefix="/owner/cortex")`, `dependencies=[Depends(require_system_owner)]`); registrar en `apps/api-server/src/api_server/main.py` (`include_router`). Schemas en `apps/api-server/src/api_server/schemas/cortex_mind.py`.
  - Aceptación: 403 para no-owner; 200 con snapshot; cross-owner aislado.

- [x] **`GET /owner/cortex/affect/timeseries`**
  - TDD: test que inserta N snapshots del owner (vía sessionmaker BYPASSRLS) + 1 de otro owner; el endpoint devuelve SOLO los del owner en orden cronológico, respetando `since/until/limit`; cross-owner verificado.
  - Ficheros: añadir el handler en `cortex_mind.py` con una query que filtra `owner_user_id == principal.user_id` explícito (usa `get_admin_sessionmaker`, tenant-less BYPASSRLS).
  - Aceptación: serie temporal correcta y aislada por owner.

- [x] **`GET /owner/cortex/episodes?emotion=`**
  - TDD: test que crea memorias episódicas emocionales del owner en `memory_entries` (scope=private, user*id=owner, metadata*.cortex=true, metadata\_.emotion={…,mood_label}) y verifica el filtrado por `emotion`==`mood_label`, que incluye `appraisal_reason`, y que NUNCA devuelve memorias de otro usuario (filtro `user_id`).
  - Ficheros: añadir handler en `cortex_mind.py`; query sobre `memory_entries` filtrando `user_id=owner` + `scope='private'` + `metadata_->>'cortex'='true'` + (opcional) `metadata_->'emotion'->>'mood_label' = :emotion`.
  - Aceptación: mapa de episodios alimentado; aislamiento por usuario.

## FASE G — WS de telemetría `/ws/owner/cortex/telemetry` (gated)

- [x] **WS que tailea `cortex:telemetry:{owner}` con gate DB-authoritative**
  - TDD: crear `apps/api-server/tests/integration/test_cortex_telemetry_ws.py`; test que (a) sin token / token de no-owner ⇒ cierre 1008; (b) con token del owner ⇒ acepta y, tras un `publish_cortex_affect_event`, recibe el frame `{type:'affect',…}`. Reusa el patrón de `routers/ws.py` (`_resolve_principal`) + un check explícito de owner contra la BD (no solo el claim), espejo de `_is_db_system_owner`.
  - Falla → implementa → pasa → commit.
  - Ficheros: crear `apps/api-server/src/api_server/routers/cortex_ws.py` (`@router.websocket("/ws/owner/cortex/telemetry")`), reutilizando `_pump`/`_resolve_principal` de `routers/ws.py` y `_is_db_system_owner` de `auth/deps.py`; registrar el router en `main.py`.
  - Aceptación: solo el owner (verificado en BD) recibe su propia telemetría; cierre 1008 en cualquier fallo.

## FASE H — Panel de Mente (frontend, copy honesto)

- [x] **Hooks de datos + cliente WS**
  - TDD: crear `apps/admin-panel/lib/cortex-affect.ts` con helpers puros testeados con vitest: `moodLabelColor(label)`, `padToCanvasXY(valence, arousal)` (proyección al espacio 2D), `trailFromSnapshots(snapshots)` (estela). Tests en `apps/admin-panel/lib/cortex-affect.test.ts`.
  - Ficheros: `apps/admin-panel/lib/cortex-affect.ts` (+ test). Hook `useCortexMind` / `useCortexTimeseries` (TanStack Query sobre `/owner/cortex/mind` y `/affect/timeseries`) y `useCortexTelemetry` (WS, patrón de cualquier hook WS existente del repo).
  - Aceptación: helpers puros verdes; los hooks consumen los endpoints gated.

- [ ] **Componente `MindPanel` montado en `app/admin/cortex` (de F1)**
  - ⏳ **Pendiente (2026-07-30):** falta el test de render del panel COMPLETO (diales PAD + banner honesto con datos mockeados; el vitest que hay cubre el espacio PAD 2D y la tarjeta de curiosidad, no los diales) y el panel sigue ES-only fuera del aviso honesto y del espacio PAD, así que el requisito ES+EN no se cumple.
  - TDD: test de render con vitest/RTL (`apps/admin-panel/components/cortex/mind-panel.test.tsx`) que verifica que SIEMPRE se renderiza el copy honesto ("modelo computacional de afecto, no sentimientos reales" / EN equivalente) y que los diales reflejan datos mockeados.
  - Ficheros: crear `apps/admin-panel/components/cortex/mind-panel.tsx` (diales PAD en vivo, espacio PAD 2D con estela, gráfico de mood, mapa afectivo de episodios con hover=`appraisal_reason`, barras de drives, banner de honestidad). Montarlo en la columna derecha de `apps/admin-panel/app/admin/cortex/page.tsx` (creada en F1).
  - Aceptación: el panel muestra estado en vivo (WS) + histórico; copy honesto no removible; ES+EN.

- [x] **Nav "Córtex" `systemOwnerOnly` (si F1 no lo añadió)**
  - TDD: ajustar/añadir test del shell que oculta el grupo "Córtex" salvo `isSystemOwner`.
  - Ficheros: en `apps/admin-panel/components/layout/admin-shell.tsx` añadir el predicado `systemOwnerOnly` (espejo de `systemAdminOnly`, usando `isSystemOwner` de `use-current-user.ts`) y el grupo NAV "Córtex"; si F1 ya lo hizo, esta tarea es no-op verificatoria.
  - Aceptación: solo el system_owner ve la entrada del Panel de Mente.

## FASE I — Verificación de fase

- [ ] **Suite completa F2 en verde + lint/type**
  - ⏳ **Pendiente (2026-07-30):** cierre de fase que depende de un humano y del panel: `alembic check` no es ejecutable (la imagen de runtime no trae alembic), el copy honesto NO está en todas las superficies (el Panel de Mente sigue ES-only) y «el dial PAD se actualiza vía WS tras un turno» exige un turno real del córtex mirado en pantalla.
  - Ejecutar la suite unit (`test_cortex_affective*`, `test_cortex_affect_store`, `test_cortex_affect_prompt`) + integración (`test_cortex_affect_migration`, `test_cortex_mind_endpoints`, `test_cortex_telemetry_ws`) + workers (`test_cortex_affect_task`) + frontend (vitest cortex-affect / mind-panel).
  - Verificar `alembic upgrade head` y `downgrade -1` reversibles; `alembic check` sin drift.
  - Confirmar copy honesto presente en todas las superficies; ningún egress en el distilador.
  - Aceptación: todo verde; sin secretos en logs; el dial PAD se actualiza vía WS tras un turno; cross-owner probado en los 3 accesos a `cortex_*`/`memory_entries`.

---

## Notas de seguridad (resumen de reglas duras aplicadas)

- **RLS / Principio 1:** `cortex_affect_snapshots` es tenant-less sobre BYPASSRLS (excepción consciente, ADR 0074). Cada query filtra `owner_user_id` explícito y hay test cross-owner en FASE F y FASE D/G. `memory_entries` (episódica) sí tiene RLS y se filtra además por `user_id=owner`.
- **TDD estricto:** cada tarea lleva su test primero (rojo → verde → commit). Migración reversible (`down()` en FASE A).
- **Catálogo LLM cerrado (ADR 0021):** el distilador usa Ollama local (ya en catálogo); cero proveedor nuevo. El razonamiento profundo sigue saliendo de claude_sdk (F1), aquí solo se MODULA `reasoning_effort` (ADR 0070).
- **ES+EN:** `derive_mood_label`, el prompt afectivo y el Panel de Mente son bilingües.
- **Copy honesto:** banner "modelo computacional de afecto, no sentimientos reales" no removible en el Panel; el prompt afectivo nunca afirma sentimientos reales.
- **Coste/egress:** no hay bucles autónomos en F2 (llegan en F3/F4). El distilador es post-turno, fail-open, idempotente y sin egress. (Los budget caps + kill-switch del plan maestro aplican a los bucles de fondo de F4, no a esta fase.)
