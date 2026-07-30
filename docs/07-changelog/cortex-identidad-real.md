---
title: "Córtex: identidad real (self-model unificado)"
date: 2026-07-06
plan: cortex-identidad-real
docs_language: es
---

# Córtex: identidad real (self-model unificado) — 2026-07-06

Cierra la brecha "computado pero decorado" del córtex F0-F5: la identidad, el
afecto y la memoria ahora **gobiernan la conducta** en cada superficie, la
identidad **emerge de la experiencia** y el "yo" es un **self-model unificado**.
Plan y auditoría: [cortex-identidad-real](../roadmap/cortex-identidad-real.md).

## Cambios

- **Self-context unificado** (`cortex/self_context.py`): un solo prompt del
  self-model — identidad (nombre/valores/narrativa), "lo que sé de mi owner"
  (`relationship_model`) y temas de curiosidad pendientes dentro de
  `<<<DATOS>>>` (blindados); guía de tono (afecto) y de estilo (Big-Five) fuera
  de los marcadores (copy generado por código puro). Chat
  (`routers/cortex.py`) y voz (`voice_turn.py`) dejan de duplicar la
  composición; contexto neutro degrada exactamente al comportamiento previo.
- **El afecto modula el texto** (`cortex/affect_policy.py`, ADR 0075 §5 por fin
  real): guía de tono por bandas PAD/drives + modulación del
  `reasoning_effort` ±1 paso (escalera del kind sin `off`, suelo `low`),
  auditada en `cortex_turns.metadata_.self_context`. Arreglado de paso que el
  turno persistía `reasoning_effort=NULL` siempre (`LLMAssistantModel` gana
  `reasoning_effort`/`provider_kind`).
- **Los rasgos Big-Five gobiernan el estilo**: bandas <0.35 / >0.65 emiten guía
  de conducta; la banda neutra no finge nada.
- **Baseline evolutivo conectado**: el decay del motor afectivo (BD y caché
  Redis, con baseline embebido retrocompatible) converge al
  `identity.mood_baseline` que la reflexión deriva — antes, a un PAD
  hardcodeado. Arousal ≤0 se trata como "sin calibrar" (→0.3).
- **Surfacing de curiosidad** (ADR 0078 por fin real, migración 0103): un
  pursuit `digested` se inyecta al siguiente turno y pasa a `surfaced` en la
  misma transacción (rollback ⇒ sigue pendiente); endpoint
  `GET /owner/cortex/curiosity/pursuits` + tarjeta "Lo que está aprendiendo"
  en el Panel de Mente (copy honesto).
- **"Aprender DE MÍ" con productor**: la reflexión pide `owner_model` (delta
  acotado del `relationship_model`, con des-aprender vía `""`) y
  `owner_facts` (0-3, memorias `kind='owner_model'` protegidas del olvido),
  con parse granular fail-open. El self-context lo consume en cada turno.
- **`recall_frequency` real** (ADR 0077): `cortex_recall` cuenta el uso
  (`recall_count`/`last_recalled_at`, solo memorias devueltas del owner) y la
  retención aplica `0.5 + 0.5·min(1, count/5)` — lo usado se retiene, el
  long-tail nuevo queda protegido por el suelo.

## Seguridad / invariantes

- Cross-owner cubierto con tests en todos los accesos nuevos (identidad,
  afecto, pursuits, contadores de recall).
- El afecto modula, nunca bloquea (fail-open en toda la cadena).
- `cortex.autonomy_enabled` sigue OFF: nada de este plan lo enciende.
- Copy honesto en todo lo nuevo ("modelo computacional, no consciencia").

## Migraciones

- `0103_cortex_pursuit_surfaced` (reversible: el downgrade reconvierte
  `surfaced`→`digested` antes de reponer el CHECK).
