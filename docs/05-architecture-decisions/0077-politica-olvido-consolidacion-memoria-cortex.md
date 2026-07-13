---
adr_id: "0077"
title: "Política de olvido y consolidación de la memoria del Córtex"
status: accepted
date: 2026-06-22
authors: [claude-opus, workflow-diseno-cortex]
plan_referenced: cortex-system-owner
docs_language: es
related: ["0074", "0059", "0071"]
supersedes: []
---

# ADR 0077 — Política de olvido y consolidación de la memoria del Córtex

> **Estado: `proposed`** — el olvido es destructivo (aunque reversible). **Requiere aprobación explícita del owner antes de activar** (fase final del plan).

## Contexto

La memoria del córtex (`memory_entries`, scope private del owner) crece de forma monótona (mismo gap que la auditoría señala para la memoria de agentes). Una "mente" humana-like consolida y olvida. Pero olvidar lo equivocado destruye justo el long-tail que hace rico el modelo del owner ("aprender de MÍ").

## Decisión

1. **`retention_score` = importance × recency × recall_frequency**, recalculado por **Celery beat**.
2. **Olvido reversible** únicamente: **soft-delete** (`deleted_at`, ya existe — nunca delete físico, postura ADR 0059) o **consolidación** (merge-into: varias memorias similares → una resumida que las referencia).
3. **Protección explícita:** `metadata_.kind ∈ {identity, owner_model}` **NUNCA** se auto-olvida — es el núcleo del autoconcepto y del modelo del owner.
4. **Gobernanza:** cadencia configurable; activación gated por aprobación del owner; observable.

## Consecuencias

- ✅ Mantiene la memoria acotada sin perder el núcleo valioso; todo recuperable.
- ⚠️ La fórmula puede enterrar long-tail útil → empezar conservador, medir, ajustar; el owner puede inspeccionar lo olvidado (soft-delete) y restaurarlo.
- ➡️ Sienta el patrón que la memoria multi-tenant de agentes podría adoptar después (con su propio ADR).

## Estado de implementación (2026-07-06 — plan "identidad real")

`recall_frequency` dejó de ser el placeholder 1.0: `cortex_recall` incrementa
`metadata_.recall_count`/`last_recalled_at` de las memorias devueltas (solo
owner) y el mantenimiento aplica
`recall_frequency_factor(count) = 0.5 + 0.5·min(1, count/5)` — el suelo 0.5
protege el long-tail nuevo (calibración conservadora de este ADR; ver
[cortex-identidad-real](../roadmap/cortex-identidad-real.md)). La protección de
`kind='owner_model'` tiene por fin PRODUCTOR: la reflexión escribe esas
memorias (antes era protección sin datos).

## Estado de implementación (2026-07-12)

OLVIDO IMPLEMENTADO (verificado 2026-07-12): `api_server/cortex/forgetting.py` (retention*score = importance x recency(half-life 30d) x recall_frequency con suelo 0.5; PROTECTED_KINDS identity/owner_model/reflection/learning; umbral decide_forget 0.1) aplicado por `workers/cortex_maintenance.py::_forget_low_retention` como SOFT-DELETE auditable (deleted_at + metadata*.forgotten con reason/score), beat diario 04:45 gated por el kill-switch `cortex.autonomy_enabled` (OFF: encenderlo es decision del operador). PENDIENTE: la CONSOLIDACION merge-into (fusionar memorias similares en una resumida que las referencie) y una vista de inspeccion/restauracion de lo olvidado para el owner.

## Consolidación implementada (2026-07-13)

La pieza pendiente (merge-into) existe: `api_server/cortex/consolidation.py` (logica PURA, determinista, sin LLM — el resumen CITA los originales con fecha+extracto, no inventa prosa) agrupa la episodica del cortex >14 dias por similitud coseno de los embeddings ya calculados (umbral 0.90, greedy, minimo 3 por grupo, protegidos y ya-consolidados excluidos) y `cortex_maintenance._consolidate_similar` crea la memoria consolidada (kind=consolidated, embedding=centroide — sigue recuperable por semantica, consolidated*from=[ids]) y soft-borra los originales con `metadata*.consolidated_into`(reversible, mismo contrato que el olvido). Best-effort, gated por el kill-switch`cortex.autonomy_enabled` (OFF) como el resto del beat. El destilador LLM para prosa sintetizada queda como mejora aparte si algun dia se quiere (y se mide).
