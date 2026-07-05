---
title: Remediación de la auditoría de plataforma 2026-07-03 — fase 2
date: 2026-07-05
phase: auditoria-plataforma-2026-07-03
status: en_progreso
docs_language: es
related: ["0102", "0089", "0092"]
---

# Remediación de la auditoría de plataforma 2026-07-03 — fase 2

## Resumen

Continúa la remediación tras cerrar la fase 1 (13 fixes + deploy verificado). La
fase 2 aborda las guardas del runtime («produce output») y **la otra mitad del P0
de guardrails (g1)** — el motor solo corría en el chat de planning; el bucle del
agente sandboxed nunca lo invocaba, así que las salidas de MCP/HTTP/RAG reentraban
**crudas** (inyección indirecta sin defensa, principio 10).

## Cambios

### Guardas del runtime (Fase G)

- **G3/r4** — `has_produced` solo se latchea con una producing tool **exitosa**
  (`_track_research` exige `observation.ok`). Antes un `shell_exec` denegado o un
  write fallido latcheaba `has_produced`, desviando cada trip de safeguard de
  ABORTED a `needs_human_review` (ensuciando la cola humana) y cambiando el nudge
  a «FINISH». `graph.py`; suite runtime verde.
- **G6a/r1** — el allowlist base de `shell_exec` (`_SDK_BASE_SHELL_COMMANDS`)
  incluye las utilidades de LECTURA (`sed`/`awk`/`sort`/`uniq`/`cut`/`tr`/`echo`).
  El `sed -n` que los modelos usan para paginar estaba denegado (visto en vivo),
  forzando reintentos estériles. Como el allowlist ya concede `rm`/`mv`/`cp`,
  añadir tools de lectura no aporta superficie — el sandbox es la frontera real.
- **G8/r7 descartado**: el conteo acumulativo del `LoopDetector` es intencional
  (ADR 0089) y un test lo pinea; recalibrarlo exige revisar el ADR, no un parche.

### g1 — cableado del motor de guardrails en el runtime (P0, mitad 2)

Slice mínimo de ADR 0102 (D1): `post_tool` en modo LOG (no bloquea) con el baseline
de plataforma (`prompt_injection`), acumulando eventos para persistir.

- **Seam** `agent_runtime/guardrails.py`: `build_pipeline` (config resuelta del
  spec o baseline) + `run_hook` (corre el hook, serializa eventos JSON-safe SIN el
  span crudo, best-effort — nunca rompe un run).
- **Infra**: `shared-guardrails` entra en la imagen `agent-runtime` (pyproject +
  Dockerfile) — antes no estaba.
- **Cableado runtime**: el nodo `act` corre `run_hook(post_tool)` sobre
  `result.output`; `AgentState` acumula `guardrail_events` (reducer `operator.add`);
  `ExecutionResult` los transporta al envelope; el boot construye el pipeline.
- **Persistencia**: el worker persiste los eventos tenant-scoped tras
  `finalize_execution`, en un SAVEPOINT best-effort (un fallo de persistencia nunca
  tumba un run ya terminado). Enmascara el payload (`record_guardrail_event`).

Con esto el **principio 10 deja de estar incumplido**: el motor corre en la
ejecución de agentes y sus eventos son consultables. Una página HTTP/MCP/RAG con
«ignore previous instructions» queda registrada antes de reentrar al contexto.

## Verificación

- **Tests**: seam 4, cableado de `act` 3, persistencia 3; suite del agent-runtime
  **220/220**; ~190 tests unit de workers verde. TDD (el bug de `triggered_outcomes`
  —property, no método— se cazó por test).
- **Revisión adversarial** del slice de g1 (workflow multi-lente: correctitud /
  seguridad-bypass / integración) — 2026-07-05.
- **Deploy realizado y verificado** (2026-07-05, ventana idle, 0 runs en vuelo):
  reconstruidos `agent-runtime:v1` (`WITH_CLAUDE=1`) y `workers:ci` (FROM
  `api-server:manuals`); recreados workers/workers-aux/workers-backup/cortex-beat.
  Sin migración (g1 usa la tabla `guardrail_events` de la 0052 ya existente).
  Verificación en vivo: workers healthy con G6a (`sed`/`awk` permitidos) y el helper
  de persistencia; `agent-runtime:v1` con `shared_guardrails` importable, el seam
  detecta inyección real (`prompt_injection`, action `warn` = modo LOG), G3, el
  screening de `recall` (memory_recall) y el cableado de `act` presentes.
- **Revisión adversarial** del slice de g1 (3 hallazgos P2, todos arreglados:
  SAVEPOINT del `_load_project`, observabilidad del fail-open, screening de `recall`).

## Trazabilidad

Auditoría: `docs/roadmap/auditoria-plataforma-2026-07-03.md`. Diseño de g1:
`docs/05-architecture-decisions/0102-cableado-motor-guardrails-runtime.md` (proposed,
blueprint de 11 pasos; hechos los 7 del slice mínimo). Rama `plan/runs-visor-trabajo`
(commits `60d1c87`…`68636bf`).
