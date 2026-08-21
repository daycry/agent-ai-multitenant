---
plan_id: cortex-f2-afectivo
title: "Córtex F2 — modelo afectivo computacional (PAD + drives) + Panel de Mente"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Córtex F2 — modelo afectivo computacional + Panel de Mente

## Resumen

Da al córtex un **estado afectivo continuo, determinista y auditable** (PAD +
drives homeostáticos) que evoluciona turno a turno mediante un distilador
asíncrono, se persiste como snapshots y se sirve en vivo a un Panel de Mente
con copy honesto. Gobierno: [ADR 0075](../05-architecture-decisions/0075-modelo-afectivo-computacional-cortex.md).

El principio que sostiene el diseño y que sí se cumple: **el afecto nunca
bloquea**. Toda la cadena es fail-open — si Ollama no responde, el delta es 0 y
el turno ya había contestado.

## Cambios

- **Motor puro** [`cortex/affective.py`](../../apps/api-server/src/api_server/cortex/affective.py):
  `PADState`, `Drives`, `AffectState`, `neutral_affect_state()`,
  `decay_emotion` (decay lazy hacia el baseline, no por timer),
  `apply_event`, `update_mood` (EWMA lento), `decay_drives`, `satisfy_drive` y
  `derive_mood_label(mood, *, language)` — etiqueta categórica solo-UI, **ES y
  EN**. Los clamps duros viven en `__post_init__` de `PADState`.
- **Estado vivo en Redis**: `cortex/affect_store.py` (lectura aplica decay,
  escritura persiste el timestamp) + `cortex/affect_cache.py`.
- **Tabla** `cortex_affect_snapshots` (migración
  `20260623_0093_cortex_affect.py`), tenant-less sobre BYPASSRLS con
  aislamiento por `owner_user_id` en SQL.
- **Distilador post-turno**: `workers/cortex_affect.py` puntúa
  `turno + drives + identidad → delta PAD + razón` con Ollama local (sin
  egress), disparado por `trigger_cortex_distill_affect(turn_id)` tras
  persistir el turno. **Fail-open** por diseño.
- **Telemetría**: frame publicado en el stream Redis
  `cortex:telemetry:{owner}` y WS `/ws/owner/cortex/telemetry`
  (`routers/cortex_ws.py`) con gate DB-authoritative.
- **Endpoints** en `routers/cortex_mind.py`: `GET /owner/cortex/mind` (:83),
  `/affect/timeseries` (:117, con `since`/`until`/`limit` y orden ascendente),
  `/episodes?emotion=` (:163).
- **El mood sesga el prompt del turno siguiente**:
  `augment_system_prompt_with_affect` + el cableado de lectura afectiva en la
  percepción del turno de F1. El lazo completo "el afecto modula el texto" se
  cerró después, en [cortex-identidad-real](cortex-identidad-real.md)
  (`cortex/affect_policy.py`).
- **Panel de Mente**: `app/admin/cortex/mind/page.tsx` (941 líneas) con diales
  PAD en vivo por WS, gráfico de mood, mapa de episodios, barras de drives y el
  rótulo honesto "modelo computacional de afecto, no sentimientos reales".

## Correcciones que la auditoría del 2026-07-27 destapó y que ya están cerradas

Del inventario [gaps-cortex-2026-07-27.md](../roadmap/gaps-cortex-2026-07-27.md),
verificado de nuevo contra el código al escribir esta entrada:

- **`GET /episodes` filtraba mal.** El contrato pedía que un episodio tuviera
  `metadata_.emotion` presente; ese filtro no existía, así que las memorias que
  otros productores escriben con `cortex=true` (`cortex_remember`,
  `learning`, `reflection`, `owner_model`) salían como "episodios" con
  valence/arousal/dominance a `null` y **contaminaban el mapa afectivo**. Hoy el
  handler exige `MemoryEntry.metadata_.has_key("emotion")` (operador JSONB `?`)
  además del filtro opcional por `mood_label`.
- **Drift del modelo ORM sin red**: existe `tests/unit/test_cortex_affect_model.py`.
- **Parametrización del timeseries sin test**: `test_cortex_mind_endpoints.py`
  se amplió (352 líneas añadidas) para ejercer `since`/`until`/`limit`.
- **Settings y registro del worker sin test**:
  `tests/unit/test_cortex_affect_worker_settings.py`.
- **Panel sin test de render**: `app/admin/cortex/mind/page.test.tsx` (jsdom).

## Lo que sigue abierto (verificado el 2026-07-29)

- **El "espacio PAD 2D con estela" no existe.** No hay `<canvas>` ni scatter en
  el panel (el único `<svg>` es la línea de mood), y los tres helpers puros que
  el plan pedía por nombre —`moodLabelColor`, `padToCanvasXY`,
  `trailFromSnapshots`— **no aparecen en ningún fichero** del admin-panel.
- **El panel es ES-only.** El backend devuelve `note_es` y `note_en`, y
  `derive_mood_label` acepta idioma; la página renderiza `autonomy.note_es`
  (:516) y etiquetas fijas en castellano. El requisito "copy honesto en ES+EN"
  no se cumple en la superficie.
- **Suite de calibración incompleta**: el plan pedía ~8 escenarios canónicos
  (interacción → rango PAD esperado); hay 3 que realmente ejercitan
  `apply_event`+`update_mood`.

## Divergencias menores de firma

`apply_event(state, delta)` — el plan la enumeraba como
`apply_event(state, delta, baseline)`. El baseline entra por `decay_emotion`,
que es donde la homeostasis tiene sentido; el comportamiento es el diseñado, la
firma no es la escrita.

## Tests

`test_cortex_affective.py`, `test_cortex_affect_model.py`,
`test_cortex_affect_policy.py`, `test_cortex_affect_enqueue.py`,
`test_cortex_affect_redis_url.py`, `test_cortex_affect_worker_settings.py`
(unidad); `test_cortex_affect_store.py`, `test_cortex_affect_cache.py`,
`test_cortex_affect_task.py`, `test_cortex_mind_endpoints.py`,
`test_cortex_telemetry_ws.py` (integración); `mind/page.test.tsx` (vitest).

## Estado de cierre

No cerrable todavía: falta el espacio PAD 2D y el ES+EN del panel (dos
casillas del propio plan), más el QA visual humano del panel en vivo.

## PR

- _pendiente_
