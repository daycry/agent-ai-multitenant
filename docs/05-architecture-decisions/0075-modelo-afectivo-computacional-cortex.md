---
adr_id: "0075"
title: "Modelo afectivo computacional del Córtex (PAD + appraisal OCC + drives homeostáticos)"
status: accepted
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0074", "0070", "0021", "0056"]
supersedes: []
---

# ADR 0075 — Modelo afectivo computacional del Córtex

> **Estado: `proposed`** — define cómo el córtex "siente" y puntúa su estado. **Requiere aprobación del operador.**

## Contexto

La visión pide un córtex que **gestione emociones y estados anímicos** con **puntuaciones visibles**, simulando una mente — no un LLM básico. Hace falta un modelo afectivo riguroso, auditable y honesto (no consciencia real).

## Decisión

1. **Modelo dimensional PAD** (Mehrabian-Russell) como núcleo continuo: `valence[-1,1]`, `arousal[0,1]`, `dominance[-1,1]`, `intensity[0,1]`. La etiqueta categórica (alegría/calma/…) es **derivada solo para UI**, no fuente de verdad.
2. **Tres capas temporales:** emoción (Redis, minutos, decae al baseline), mood (EMA lento, snapshots a PostgreSQL), **drives homeostáticos** (`curiosity/bonding/coherence/competence ∈ [0,1]`, decaen y se sacian; un drive bajo motiva el bucle de fondo).
3. **Appraisal ASÍNCRONO** (decisión clave): el turno responde primero; un **Celery task** posterior (distilador, **Ollama local**, sin egress) puntúa el turno contra drives/identidad y emite `delta PAD + razón`; el motor determinista lo aplica. **Fail-open** (Ollama caído ⇒ delta=0). Descartadas: auto-evaluación del propio turno (sycophancy/drift) y Ollama síncrono en hot-path (latencia/fragilidad).
4. **Dinámica determinista, fuera del LLM, auditable:** decay lazy en lectura, update por evento, EWMA del mood, baseline = "temperamento" (cambia muy lento por reflexión, clamp duro). **Clamps** y piso/techo de mood (evitar "depresión/manía" simuladas).
5. **El afecto MODULA** (tono, `reasoning_effort`, expresión del avatar) pero **nunca bloquea** una acción ni la respuesta al owner.
6. **Honestidad:** la UI siempre rotula "modelo computacional de afecto, no sentimientos reales".
7. **Calibración:** suite de regresión con interacciones canónicas → rangos PAD esperados.

## Consecuencias

- ✅ Scores 100% graficables; coste/latencia del scoring fuera del hot-path; tolerante a fallos.
- ⚠️ El dial PAD se actualiza ~1-2s tras la respuesta (asíncrono). Aceptable.
- ⚠️ Requiere un modelo Ollama local barato siempre disponible para el distilador.

## Estado de implementación (2026-07-06 — plan "identidad real")

El punto 5 ("el afecto MODULA tono/`reasoning_effort`") estaba prometido pero
sin implementar (el PAD no se leía en el turno de texto). Implementado en el
plan [cortex-identidad-real](../roadmap/cortex-identidad-real.md):
`cortex/affect_policy.py` (guía de tono por bandas + modulación de effort ±1
paso acotada, suelo `low`, nunca `off`) cableado al self-context de chat y voz;
la decisión queda auditada en `cortex_turns.metadata_.self_context`. Además, el
decay del motor converge ahora al `mood_baseline` EVOLUTIVO de la identidad
(antes un PAD hardcodeado), con fallback de arousal "sin calibrar" → 0.3.

## Estado de implementación (2026-07-12)

IMPLEMENTADO (fases F2/F3 del cortex + tanda 2026-07-11/12): PAD continuo con decay lazy y clamps (`api_server/cortex/affect.py`), drives homeostaticos, appraisal asincrono por Celery con destilador Ollama fail-open (`workers/cortex_affect.py`), mood EMA con snapshots, baseline conectado a conducta, modulacion de tono (`tone_guidance`, incl. aburrimiento C9) y pulso de plataforma determinista (`cortex/platform_affect.py`, beat 15min). La UI del Panel de Mente muestra el estado afectivo como modelo computacional.
