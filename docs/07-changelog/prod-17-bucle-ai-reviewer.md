---
plan_id: prod-17-bucle-ai-reviewer
title: Bucle del AI reviewer — in_review → veredicto → done/backlog
completed_at: null
docs_language: es
---

# Plan prod-17 — Bucle del AI reviewer (progreso)

## Resumen

El plan 06 construyó la **biblioteca** del AI reviewer (`reviewer_bridge`) pero nunca
cableó el productor: `apply_reviewer_verdict` tenía **0 callers** (workers-1), y una
tarea que entraba en `in_review` se quedaba ahí para siempre (nadie consumía el
estado). Este plan —la parte A de `task_prod06_dag_03` que prod-06 difirió por ADR 0084
Opción B— cierra el **ciclo autónomo de revisión**.

**Corrección clave (ADR 0084):** NO depende de ADR 0063 (el contenedor review-runtime
de preview HUMANO); el AI reviewer es una ejecución de agente normal.

## Cambios

### Fase A — Reconciliación del bridge (`bridge_01`)

`apply_reviewer_verdict` reconciliado con la state machine §7.2:

- `approve` → `in_review → done` (+ `completed_at`) — antes era no-op y dejaba la tarea
  colgada;
- `reject` con `retry_count < max_retries` → `backlog` + `retry_count++` + audit
  `review_comment`;
- `reject` al alcanzar `max_retries` → **`blocked`** (salida DB-legal desde `in_review`;
  `awaiting_human_approval` no es alcanzable desde ahí — es estado de la approval-engine
  de ADR 0020); audit con `reason=max_retries`;
- `unknown` → no-op; **guard de idempotencia** (veredicto sobre tarea que ya no está en
  `in_review` → no-op); predicado `tenant_id` explícito.

### Fase B — Bucle de ejecución (`loop_01/02/03`)

- **Trigger** (`loop_01`): el orchestrator reacciona a `task.status_changed → in_review`
  (`_is_in_review_trigger` / `_on_task_in_review`); routing por `agent_type` — un reviewer
  AI dispara una ejecución de review, un reviewer humano (o ninguno) es no-op (peer-review
  intacto).
- **Builder del request** (`loop_02`): `_build_review_request` arma el payload del reviewer
  reusando la herencia de modelo + tools/skills + el envelope de budget (prod-06 budget_02),
  lo marca `review=True` y adjunta `review_context` (criterios + salida del implementador).
- **Aplicación del veredicto** (`loop_03`): `ExecutionRequest` gana `review`/`review_context`;
  al terminar una ejecución de review, el worker (`_apply_review_verdict`) parsea la salida y
  aplica el veredicto en vez de la transición normal de `dag_01`. Veredicto no parseable →
  reject defensivo (la tarea converge).

### Fase C (consumidor) — `<test-report>` en el prompt (`test_02`)

`_build_review_request` lee los `test_run_completed` que el test-runtime persiste y
`_format_test_report_block` arma el bloque `<test-report>` en `review_context`. Sin
eventos → bloque vacío (el reviewer revisa el diff solo; degradación elegante).

## Tests (TDD)

- `test_reviewer_bridge_wiring.py` (6) — el que `dag_03` pidió y nunca existió.
- `test_in_review_dispatch.py` (4) — trigger + routing + test-report.
- `test_review_execution_applies_verdict.py` (5) — aplicación del veredicto en el worker.
- 0 regresión: dispatch (17), `ExecutionRequest` (capture 6). mypy limpio.

## Pendiente (bloqueado)

- **`task_prod17_test_01`** (productor del test-report): **BLOQUEADO**. `conduct_execution`
  no monta el repo del proyecto en la ejecución del implementador
  (`ContainerSpec.workspace_host_path` sin fijar — "la pool con reuso de worktree llega en
  Plan 06"). Sin worktree con el código no hay qué testear. Es el subsistema de
  **git-worktrees en la ejecución** (CLAUDE.md 4/5) — candidato a plan dedicado. El
  consumidor (test_02) ya está listo: en cuanto el productor persista los eventos, el
  reviewer los usa automáticamente.
- **`task_prod17_e2e_01`** (Fase D, e2e): **CERRADA el 2026-08-27**, y sin escribir nada
  nuevo. Este párrafo decía «BLOQUEADO — corre contenedores reales (Docker)», y esa
  última frase —«el bucle sin test-report ya está cubierto por tests de integración»—
  era la pista: la cobertura estaba, con otro nombre.
  `tests/e2e/test_autonomous_review_loop.py`, que es lo que nombraba el enunciado, no
  existe en el repo; `tests/integration/test_autonomous_cycle.py` sí, y cubre el ciclo
  entero sobre contenedores reales. Corre en el shard 3 del job de integración de
  master (run 33063384295) con 2 casos PASSED y 0 fallidos ni saltados.
  Sigue pendiente el tramo con `test-runtime`, que depende de `test_01`.

Por estos dos ítems el plan permanece en `in_progress`. El **bucle autónomo de revisión
es funcional** (in_review → reviewer → veredicto → done/backlog/blocked); falta el
test-report real (productor) y el e2e con Docker.
