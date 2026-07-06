---
title: "Córtex: identidad real (self-model unificado)"
type: plan
status: pending_human_validation
date: 2026-07-06
started_at: 2026-07-06
completed_at: null
author: claude (auditoría multi-agente + panel de diseño + plan aprobado por el operador)
blocking_plan: null
related_adrs: ["0074", "0075", "0076", "0077", "0078", "0021", "0070"]
docs_language: es
---

# Córtex: identidad real (self-model unificado)

> Análisis y plan aprobados por el operador el 2026-07-06 (modo plan, enfoque
> "Self-model unificado" elegido entre tres). Objetivo declarado del operador:
> hacer el córtex/memoria cognitiva **real** — que la identidad (a) **gobierne la
> conducta** en cada superficie, (b) **emerja de la experiencia**, y (c) sea un
> **self-model unificado** en vez de piezas que no se hablan entre sí.

## 1. Auditoría: qué es real y qué es decorado (2026-07-06)

Mapeo del código vivo (no de los planes) del córtex F0-F5 en la rama
`plan/runs-visor-trabajo`.

### 1.1 Lo que YA es real (se reutiliza, no se toca)

| Lazo                                                                                                                                         | Evidencia                                                                                                                                                       |
| -------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recall híbrido (BM25+vector+entidad, RRF) alimenta el system prompt del chat y de la voz, con blindaje anti-inyección `<<<DATOS>>>`          | `cortex/memory.py` (`cortex_recall` → `memorizer.recall`, re-filtro `metadata_.cortex=true`); `routers/cortex.py:242-253`; `cortex/voice_turn.py:125-136`       |
| Bucles de fondo escriben memoria que luego se recuerda (lazo F4→F1→chat CERRADO)                                                             | curiosidad → `kind='learning'`; reflexión → `kind='reflection'`; afecto → episódicas con `metadata_.emotion`; todas con `cortex=true`, elegibles para el recall |
| Identidad persistida y versionada; la reflexión reescribe narrativa y deriva traits/baseline acotados (Δ≤0.05/ciclo) con history append-only | `cortex_identity`/`cortex_identity_history`; `workers/cortex_reflection.py:155-216,361-401`; `identity.py` (`apply_reflection_delta`, `bounded_update`)         |
| `name/core_values/narrative` entran en el prompt (preámbulo blindado)                                                                        | `identity.py:346-384`; cableado en `routers/cortex.py:235-239` y `voice_turn.py:119-123`                                                                        |
| `learning_goals` sesgan la elección de tema de la curiosidad                                                                                 | `cortex/curiosity.py` (`pick_topic`)                                                                                                                            |
| Olvido ADR 0077: `retention_score` + protección de kinds + soft-delete, cableado al mantenimiento diario                                     | `cortex/forgetting.py`; `workers/cortex_maintenance.py:178-233`                                                                                                 |
| Motor PAD determinista + distilador afectivo post-turno (Ollama local, fail-open, snapshots append-only, caché Redis, telemetría WS)         | `cortex/affective.py`; `workers/cortex_affect.py:150-205,422-474`                                                                                               |
| Afecto → velocidad de la voz (arousal → banda [0.85,1.25] de Kokoro) + frame de avatar                                                       | `cortex/voice_affect.py:63-84`; `routers/cortex_voice.py:170-217`                                                                                               |

### 1.2 La brecha: decorado / computado-pero-no-usado / escrito-pero-nunca-leído

1. **El afecto NO modula el texto.** El estado PAD no se lee en el turno de chat;
   `reasoning_effort` es estático. El docstring de `affective.py:8` promete
   modular "tono / reasoning_effort" — no está implementado. Peor: el turno
   persiste `reasoning_effort=NULL` SIEMPRE, porque `routers/cortex.py:297` hace
   `getattr(model, "reasoning_effort", None)` y `LLMAssistantModel`
   (`assistant/llm.py:27-48`) no tiene ese atributo (el effort va horneado en
   `extra_call_kwargs`).
2. **`traits` (Big-Five) y `mood_baseline`: bucle autorreferencial sin efecto.**
   La reflexión los deriva y versiona, la UI los muestra read-only, y se
   re-alimentan al prompt de la _siguiente_ reflexión — pero NO entran en
   `identity_preamble` (solo name/values/narrative, `identity.py:360-374`) ni en
   ninguna otra decisión.
3. **El baseline evolutivo está desconectado del motor afectivo.**
   `affect_store.load_affect_state` decae hacia `BASELINE_PAD` **hardcodeado**
   (`affect_store.py:100`, `affective.py:150`), nunca hacia
   `identity.mood_baseline` — pese a que `identity.py:44` afirma lo contrario.
   Matiz de calibración: `identity._NEUTRAL_BASELINE.arousal=0.0` vs
   `BASELINE_PAD.arousal=0.3` ("calma despierta"); conectar sin tratar el caso
   dejaría al córtex convergiendo a arousal 0 (catatónico).
4. **La curiosidad aprende pero nunca lo cuenta.**
   `cortex_curiosity_pursuits.surfaced_at` (`db/cortex_curiosity.py:107`) no se
   escribe jamás; ningún router lee pursuits; el "abrir el tema en el próximo
   encuentro" (ADR 0078) no existe. Además el CHECK
   `ck_cortex_pursuits_status` NO admite `'surfaced'` (exige migración).
5. **"Aprender DE MÍ" sin productor.** `identity_state.relationship_model`
   siempre `{}` (`identity.py:122`); nadie escribe memorias `kind='owner_model'`
   (protegidas contra el olvido en `forgetting.py:42`… pero vacías). El núcleo de
   la visión original ("curioso, con ganas de aprender DE MÍ") es un hueco.
6. **`recall_frequency` hardcodeado a 1.0** (`forgetting.py:90-95`): la retención
   es de facto `importance × recency`, ignora el uso real.
7. **Duplicación chat/voz.** `routers/cortex.py:232-253` y
   `voice_turn.py:119-136` duplican el mismo bloque de composición de prompt
   (identidad → preámbulo → recall → augment): el punto natural para unificar.

Menores confirmados: `cost_usd` de pursuits nunca se escribe;
`affect_params` declarado y nunca usado.

### 1.3 Notas verificadas para la implementación

- Head de migraciones al aprobar el plan: `0102_plan_pr_url` (verificar
  `alembic heads` al implementar; otras ramas pueden fusionar antes).
- La voz YA carga el afecto ANTES del turno para la prosodia
  (`routers/cortex_voice.py:170` → `load_current_affect`): se le puede pasar al
  turno sin doble lectura.
- `decay_emotion` ya acepta `baseline` como parámetro — solo hay que cambiar qué
  baseline le pasan los dos lectores (`affect_store`, `affect_cache`).
- `LLMAssistantModel` es dataclass con `extra_call_kwargs` — se puede
  `dataclasses.replace` por-request sin estado compartido.

## 2. Diseño aprobado

### 2.1 `cortex/affect_policy.py` (nuevo, 100 % puro)

- `modulate_reasoning_effort(base_effort, kind, affect) -> EffortDecision{base,
effective, reasons}`: escalera = `REASONING_OPTIONS_BY_KIND[kind]` **sin**
  `"off"` (el afecto no puede apagar ni encender el razonamiento); **sube 1
  paso** si `arousal≥0.65 ∧ intensity≥0.25`; **baja 1 paso** si
  `arousal≤0.15 ∧ curiosity≤0.20`; suelo duro `low`; `|Δ|≤1` por turno; kind
  desconocido o base fuera de escalera ⇒ no-op auditable
  (`reasons=("no_ladder",)` — los dobles de test caen aquí limpiamente). El
  afecto **modula, nunca bloquea** (ADR 0075).
- `tone_guidance(affect, language)`: bandas (valence ±0.25, arousal 0.5/0.3,
  dominance ±0.3, drives 0.7) → guía de tono; banda neutra no emite nada. Copy
  honesto: rotulado como derivado de estado afectivo **simulado**.

### 2.2 `cortex/self_context.py` (nuevo: composición pura + loader I/O separado)

- `SelfContext{identity_state, affect, known_facts, pending_learnings}`.
- **Puro**: `trait_style_guidance(traits)` (bandas <0.35 / >0.65; banda neutra
  no emite) y `compose_self_context_prompt(base_prompt, ctx, remember_enabled)`.
  **Decisión de seguridad**: dentro de `<<<DATOS>>>` va todo lo derivable de
  entradas del owner/web vía LLM (nombre, valores, narrativa,
  `relationship_model`, digests de learnings) — dato, nunca instrucción; fuera
  de los marcadores va SOLO el copy generado por nuestro código puro desde
  floats clampeados (guía de tono + estilo de traits). Cierra con
  `augment_cortex_prompt` — el recall se compone UNA vez. Ctx vacío ⇒ degrada
  exactamente al comportamiento actual.
- **I/O**: `load_self_context(session, redis, ..., affect=None)` — identidad
  (`ensure_identity`), afecto (parámetro si la voz lo pasa; si no
  `read_affect_state` → `load_affect_state` → neutro, fail-open), recall (el
  router elimina su llamada directa), 1 pursuit `digested AND surfaced_at IS
NULL` por turno re-filtrando su memoria por owner.
  `mark_pursuits_surfaced(...)` lo llama el caller en la MISMA transacción del
  turno: si el LLM falla, rollback ⇒ el pursuit queda pendiente (gratis).

### 2.3 Integración chat/voz + effort

- `assistant/llm.py`: `LLMAssistantModel` gana dos campos opcionales
  `reasoning_effort: str | None = None` y `provider_kind: str | None = None`
  (default None ⇒ asistente intacto); `cortex/model_config.py` los estampa.
  Arregla de paso el `reasoning_effort=NULL` persistido (brecha 1).
- `routers/cortex.py`: el bloque identidad+recall se sustituye por
  `load_self_context` + `modulate_reasoning_effort` (si cambia el effort:
  `dataclasses.replace` del modelo con `reasoning_call_kwargs` regenerados,
  preservando el resto de `extra_call_kwargs`) + `compose_self_context_prompt` +
  `mark_pursuits_surfaced` + metadata del turno `self_context`
  `{mood_label, valence, arousal, effort_base, effort_effective, effort_reasons,
surfaced_pursuits}`.
- `voice_turn.run_cortex_voice_turn`: parámetro nuevo
  `affect: AffectState | None`; el WS le pasa el que ya cargó para la prosodia.
  La base de voz sigue siendo `_cortex_voice_base_prompt()`.

### 2.4 Baseline evolutivo conectado

- `cortex/identity.py`: `effective_mood_baseline(identity_state) -> PADState` —
  clamp + fallback por-eje (`arousal≤0.0` ⇒ 0.3 de `BASELINE_PAD`, "sin
  calibrar"); sin migración de datos; desviación calibrable documentada.
- `affect_store.load_affect_state(..., baseline=None)`: si `None`, carga la
  identidad en la MISMA sesión (SELECT por UNIQUE, coste ~0) y deriva;
  excepción ⇒ `BASELINE_PAD` (fail-open). Los 6 call-sites de BD quedan
  conectados sin tocar firmas.
- `affect_cache`: baseline embebido opcional en la clave Redis (ausente ⇒
  neutro — retrocompatible con claves existentes); los escritores
  (`workers/cortex_affect.py`, `workers/cortex_curiosity._satisfy_curiosity`)
  lo escriben.

### 2.5 Productor del owner_model (dentro de la reflexión existente)

Reutiliza el gobierno de la reflexión (kill-switch, fail-open, versionado);
funciona con autonomía OFF vía `POST /owner/cortex/reflect`:

- El prompt de reflexión pide además `owner_model` (dict breve sobre el OWNER:
  preferencias, estilo, metas, contexto) y `owner_facts` (0-3 hechos duraderos);
  `max_tokens` 512→768; parse tolerante (ausentes ⇒ no invalidan
  narrative/traits — fail-open granular).
- `cortex/identity.py`: `apply_owner_model_delta(current_state, proposed,
max_keys=12, max_value_len=280)` — merge acotado sobre `relationship_model`;
  valor `""` elimina la clave (des-aprender); versionado gratis vía
  `update_identity` (diff en history).
- `_persist_owner_model_memories`: `persist_memory_candidates` directo,
  `kind='owner_model'` (ya en `PROTECTED_KINDS`), dedup por contenido.
- El self-context renderiza "lo que sé de mi owner" dentro de `<<<DATOS>>>`.

### 2.6 Surfacing de curiosidad

- Migración `20260706_0103_cortex_pursuit_surfaced.py`: recrear
  `ck_cortex_pursuits_status` incluyendo `'surfaced'`; downgrade reversible
  (reconvierte `surfaced`→`digested` antes de reponer el CHECK antiguo).
- Flujo del turno: query de pendientes → inyección al self-context ("tema que
  quiero sacar") → `mark_pursuits_surfaced` en la misma transacción →
  `metadata.self_context.surfaced_pursuits`.
- Endpoint `GET /owner/cortex/curiosity/pursuits` en `routers/cortex_mind.py`
  (gated `require_system_owner`, filtro `owner_user_id` explícito, `limit≤200`)
  - `schemas/cortex_curiosity.py`.
- UI: tarjeta "Lo que está aprendiendo" en
  `apps/admin-panel/app/admin/cortex/mind/page.tsx` + fetcher en
  `apps/admin-panel/lib/cortex.ts`. Copy honesto ES/EN.

### 2.7 recall_frequency real

- En `cortex_recall`: un único UPDATE (jsonb*set) de
  `metadata*.recall_count += 1`+`last_recalled_at` sobre los ids devueltos
(`WHERE id=ANY(:ids) AND user_id=:owner AND scope='private'`), en try/except
  con warning (un fallo del contador jamás rompe el recall). Instrumenta chat y
  voz a la vez.
- `forgetting.py`: `recall_frequency_factor(count) = 0.5 + 0.5·min(1, count/5)`
  (suelo 0.5 protege el long-tail nuevo); `cortex_maintenance` lo pasa.
  Calibración documentada de ADR 0077.

## 3. Fases de implementación (TDD estricto)

> **Estado 2026-07-06**: fases 1-6 IMPLEMENTADAS con TDD (commits `c2205db`,
> `d7b055a`, `2ce368b`, `cba1f78`, `110407c`, `d189a1f` en
> `plan/runs-visor-trabajo`); fase 7 (docs: este doc + ADR 0075/0077/0078
> anotados + changelog) en el commit de cierre. Verificación final + deploy a
> dev al cierre del plan.

| Fase | Contenido                                                                                                                                                 | Tests                                                                                                                                |
| ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| 1    | Núcleo puro: `affect_policy.py`, `self_context.py`, `effective_mood_baseline` + `apply_owner_model_delta` en `identity.py`                                | `tests/unit/test_cortex_affect_policy.py`, `tests/unit/test_cortex_self_context.py`, extensión de `test_cortex_identity_dynamics.py` |
| 2    | Decay → baseline evolutivo: `affect_store.py`, `affect_cache.py`, `workers/cortex_affect.py`, `workers/cortex_curiosity.py`                               | extensiones de `test_cortex_affect_store.py` (cross-owner), `test_cortex_affect_cache.py`, `test_cortex_affect_task.py`              |
| 3    | Self-context cableado en chat/voz + afecto→effort: `assistant/llm.py`, `model_config.py`, `routers/cortex.py`, `voice_turn.py`, `routers/cortex_voice.py` | nuevo `test_cortex_self_context_in_chat.py`; extensiones voice/endpoint/cross-owner                                                  |
| 4    | Surfacing: migración 0103, loader pursuits, endpoint, UI                                                                                                  | nuevos `test_cortex_surfacing.py`, `test_cortex_pursuits_endpoint.py`; extensión migración                                           |
| 5    | Owner_model en la reflexión: `workers/cortex_reflection.py`                                                                                               | nuevo `test_cortex_owner_model.py`; extensión `test_cortex_f3_reflection.py`                                                         |
| 6    | recall_frequency: `memory.py`, `forgetting.py`, `cortex_maintenance.py`                                                                                   | extensiones forgetting/recall/maintenance                                                                                            |
| 7    | Docs: este documento, anotar ADR 0075/0077/0078, changelog                                                                                                | —                                                                                                                                    |

## 4. Riesgos y decisiones

- **Migración 0103**: encadenar al head vigente al implementar (`alembic
heads`).
- **Tests que asertan sobre el system prompt** (`test_cortex_recall_in_chat.py`,
  `test_cortex_identity_preamble.py`): necesitarán ajuste; el blindaje y el
  orden (identidad → recall) se preservan a propósito.
- **Crecimiento del prompt**: digests y valores del relationship_model
  truncados (≤280 chars).
- **Cambio de dinámica observable del afecto** (fase 2): mitigado — identidades
  no reflexionadas ⇒ baseline efectivo == comportamiento actual.
- **`cortex.autonomy_enabled` sigue OFF**: nada de este plan lo enciende
  (decisión del operador). El surfacing y el owner_model funcionan sin
  autonomía (lectura de BD + reflexión manual).
- **Honestidad de producto** (ADR 0075 §6): todo el copy nuevo mantiene "modelo
  computacional, no consciencia"; la guía de tono se rotula como derivada de
  estado afectivo simulado.

## 5. Verificación

1. Por fase: pytest unit + integration locales + `mypy` strict + `ruff` +
   `black`.
2. E2e local: sembrar afecto en Redis + identidad con traits/narrativa + un
   pursuit `digested` → `POST /owner/cortex/turns` → verificar prompt con
   self-context, `metadata.self_context` completo, pursuit `surfaced`,
   `reasoning_effort` efectivo persistido.
3. Deploy a dev + smoke visual (chat, Panel de Mente con "lo que está
   aprendiendo", reflexión manual actualiza `relationship_model`). Kill-switch
   de autonomía queda OFF.
