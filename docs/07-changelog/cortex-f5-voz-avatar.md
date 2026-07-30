---
plan_id: cortex-f5-voz-avatar
title: "Córtex F5 — voz y avatar afectivo del system_owner + olvido de memoria"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex F5 — voz/avatar afectivo + olvido

## Resumen

Dos cosas en una fase: una **videollamada con avatar** para el owner (WS
`/ws/owner/cortex/voice`, que reutiliza el transporte STT/TTS del asistente pero
invoca el cerebro del córtex y modula la voz Kokoro por el `arousal`), y el
cierre del ciclo de memoria con un **bucle de olvido reversible**
([ADR 0077](../05-architecture-decisions/0077-politica-olvido-consolidacion-memoria-cortex.md)).
Sin tablas nuevas y sin quinto proveedor LLM: STT/TTS son medios, el cerebro
sigue siendo `cortex.default_model`.

## Cambios

### Voz y prosodia

- `HttpTextToSpeech.synthesize(..., speed=…)` — el parámetro Kokoro se expone de
  forma aditiva.
- **Mapeo puro afecto → prosodia** en
  [`cortex/voice_affect.py`](../../apps/api-server/src/api_server/cortex/voice_affect.py):
  `arousal_to_speed(arousal, *, valence=0.0)` — mapeo afín sobre la banda
  `SPEED_MIN=0.85`..`SPEED_MAX=1.25`, monótono y **recortado duro**, más
  `voice_params_from_affect`.
- **Adaptador de turno** `cortex/voice_turn.py`: `run_cortex_voice_turn`
  (resuelve tenant, crea o reusa hilo, persiste el turno `user` y el turno
  `cortex` siempre con `owner_user_id` explícito), `load_current_affect` con
  **fail-open doble** (Redis → BD → `neutral_affect_state()`) y el `affect_frame`
  puro.
- **WS** `routers/cortex_voice.py`: gate `_is_db_system_owner`
  **DB-authoritative ANTES** de construir el cerebro (un claim forjado no llega
  ni a instanciar el modelo), `speed` reenviado a la TTS, y el frame
  `{type:'affect', valence, arousal, dominance, mood_label, drives}` emitido
  entre la respuesta de texto y el binario de audio.
- **Frontend**: `components/cortex/cortex-voice-call.tsx` (+ su test vitest),
  `components/cortex/cortex-avatar.tsx`, `components/voice/realistic-avatar.tsx`
  (lip-sync por amplitud, + su test) y la integración en `/admin/cortex` gateada
  por `isSystemOwner`.

### Olvido

- **`cortex/forgetting.py`** (puro): `retention_score(*, created_at, now,
metadata, recall_frequency)` = importancia × recencia (semivida 30 d) ×
  frecuencia de recall con suelo 0.5; `recall_frequency_factor`;
  `is_protected(metadata)` con `PROTECTED_KINDS` =
  `identity`/`owner_model`/`reflection`/`learning`.
- **Aplicación**: `workers/cortex_maintenance.py::_forget_low_retention` hace
  **SOFT-DELETE auditable** (`deleted_at` + `metadata_.forgotten` con razón y
  score), en el beat `sched["cortex-maintenance"]`, **gated por el kill-switch
  `cortex.autonomy_enabled` (OFF)**. El olvido es destructivo aunque
  reversible: encenderlo es decisión del operador.
- **Consolidación** (`cortex/consolidation.py`): agrupación por similitud coseno
  ≥0.90 de embeddings ya calculados, resumen **determinista que cita los
  originales** (no prosa inventada por un LLM), memoria `kind=consolidated` con
  embedding centroide y soft-delete reversible de los originales.

## Correcciones que la auditoría del 2026-07-27 destapó y que ya están cerradas

- **El clamp del mapeo de prosodia no lo ejercitaba nadie.** Medido entonces:
  85,7 % de cobertura, las dos ramas de `_clamp` (líneas 57 y 59) nunca
  ejecutadas — y el test que decía cubrirlo,
  `test_out_of_range_arousal_is_clamped`, en realidad probaba el clamp de
  `PADState.__post_init__`, porque el valor ya llegaba recortado. Hoy
  `tests/unit/test_cortex_voice_affect.py` llama a `arousal_to_speed` **sin
  intermediario** y alcanza las dos ramas con entradas legales en producción
  (`arousal=0, valence=-1` → crudo 0,80 → suelo; `arousal=1, valence=1` → crudo
  1,30 → techo), con el crudo asertado explícitamente antes del recorte.
- **El testid roto de la e2e**: `apps/admin-panel/e2e/cortex-voice.spec.ts`
  apuntaba a `cortex-voice-card`, que no existía en la app (solo en la propia
  spec) — el caso del owner no podía pasar. Corregido a `cortex-voice-call`, el
  testid que emite de verdad `VoiceCallShell`.
- **Componentes sin test de render**: hoy existen `cortex-voice-call.test.tsx` y
  `realistic-avatar.test.tsx`.

## Lo que sigue abierto

> **Re-verificado el 2026-07-30.** Siguen abiertos a esa fecha, comprobados uno a uno:
> `tests/integration/test_cortex_voice_ws.py` asserta la banda `0.85 <= speed <= 1.25` y **no
> consulta `cortex_turns`** (cero `SELECT`, cero `COUNT`); `routers/cortex_voice.py:230` llama
> `affect_frame(affect)` **sin** `language`; `forgetting.py` **no menciona** `last_recalled_at`.
>
> **Aviso de concurrencia:** `routers/cortex_voice.py` estaba siendo modificado ese mismo día por
> otra línea de trabajo. La autoridad sobre el estado de cada casilla son el plan de la fase y sus
> tests, no esta sección.

- **El test del WS no puede fallar ante la regresión que dice cubrir.**
  `tests/integration/test_cortex_voice_ws.py:287` asserta
  `0.85 <= speed <= 1.25`, que es **la banda entera del clamp**: cualquier
  salida de la función la cumple, y el default `speed=1.0` de la TTS también.
  Si se borrase el cableado afecto→prosodia completo, el test seguiría verde. El
  criterio del plan era `speed == arousal_to_speed(arousal_de_Redis)`, y eso no
  se asserta. Además el fixture vacía Redis, así que el camino
  Redis→prosodia ni se recorre: lo que se ejercita es el fail-open.
- **`cortex_respond` persiste exactamente un turno**: sin test. El de
  integración hace `TRUNCATE cortex_turns` y nunca vuelve a consultar la tabla
  (cero `SELECT`, cero `COUNT`); el unitario no toca `run_cortex_voice_turn`. Y
  hay un detalle semántico sin resolver: la llamada persiste **dos** filas
  (turno `user` + turno `cortex`), que es lo correcto y espeja el chat de F1,
  pero contradice la letra del criterio y nadie ha fijado cuál es la lectura
  buena.
- **El `mood_label` sale siempre en castellano.** `routers/cortex_voice.py:230`
  llama `affect_frame(affect)` sin `language`, aunque `affect_frame` lo soporta y
  el WS ya conoce el idioma de la voz (`voice_language_instruction(state.voice)`
  se usa dos líneas más arriba). El disclaimer del componente también es ES
  fijo, así que el "ES+EN cubiertos" del plan no se cumple.
- **D3 (migración de columnas de olvido) no existe, y el diseño pivotó sin
  dejarlo escrito.** Los contadores viven en `metadata_` JSONB
  (`metadata_.recall_count`, `metadata_.last_recalled_at`, escritos por
  `_bump_recall_counters`), no en columnas: verificado que
  `last_recalled_at`/`recall_count` no aparecen en ninguna migración ni modelo.
  Dos consecuencias reales: (a) no hay índice que soporte el barrido, que se
  acota a mano con `_FORGET_SCAN_LIMIT = 500`; (b) `last_recalled_at` **se
  escribe y nadie lo lee** — `forgetting.py` calcula la recencia sobre
  `created_at`, así que una memoria de hace dos años recordada ayer sigue
  puntuando bajo. Hay que decidir: escribir la migración o cerrar la casilla
  documentando el diseño JSONB y añadiendo al menos un índice parcial.
- **La intensidad emocional del score de retención no está implementada**: el
  plan pedía `metadata_.emotion.intensity`; el módulo usa
  `metadata_.importance`, que es otro dato con otro productor. Y ningún test
  varía la importancia dejando el resto fijo, así que la monotonía de ese
  factor no está probada.
- **El gate del mantenimiento no es el del plan**: solo comprueba el kill-switch
  global; no consulta el budget por owner ni el circuit-breaker de F4, que
  existen y sí usa la curiosidad. Defendible (el barrido no gasta LLM), pero el
  criterio literal no está cumplido ni testeado.
- **`affectToVisual`**: la función pura no gobierna el avatar vivo (hay
  duplicación inline en `realistic-avatar.tsx`), y falta el `blinkRate` que
  pedía el criterio — el parpadeo es un intervalo aleatorio fijo, no depende del
  arousal. Tampoco están `mouthBias` ni `label` en el retorno.

Detalle por casilla en
[gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md).

## Tests

`test_cortex_voice_affect.py`, `test_cortex_voice_turn.py`,
`test_cortex_voice_prompt.py`, `test_cortex_forgetting.py`,
`test_cortex_consolidation.py` (unidad); `test_cortex_voice_ws.py`,
`test_cortex_maintenance_task.py` (integración);
`cortex-voice-call.test.tsx`, `realistic-avatar.test.tsx` (vitest);
`e2e/cortex-voice.spec.ts` (Playwright, requiere navegador).

## Estado de cierre

No cerrable. Además del QA visual humano (avatar en ES+EN, latencia de Kokoro,
`speed` confirmado contra la imagen pineada) y de la e2e Playwright, quedan dos
huecos que son decisiones, no trabajo mecánico: qué se hace con D3 (migración
frente a diseño JSONB documentado) y qué lectura del criterio "un turno por
llamada" se fija con un test.

## PR

- _pendiente_
