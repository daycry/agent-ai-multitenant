---
adr_id: "0077"
title: "Política de olvido y consolidación de la memoria del Córtex"
status: proposed
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
