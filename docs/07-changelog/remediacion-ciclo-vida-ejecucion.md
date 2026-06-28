---
title: "Remediación del ciclo de vida de ejecuciones (runs) de tareas y reviews"
date: 2026-06-28
adr: ["0088", "0087", "0086", "0063"]
status: implemented
docs_language: es
---

# Remediación del ciclo de vida de ejecuciones de tareas y reviews

Cierre de una **auditoría exhaustiva** del pipeline de ejecución
(orchestrator → worker → contenedor agent-runtime → providers → review-runtime →
persistencia → UI), motivada por "runs de tareas con muchos errores". La auditoría
(workflow multi-agente: 9 finders por etapa → dedup → verificación adversaria →
síntesis) encontró **41 defectos confirmados** en **8 clusters de causa raíz**.
Remediados por fases con TDD; cada fase verificada (unit + container + integración
con BD/Docker) antes de la siguiente. Decisiones de producto en ADR 0088.

## Cambios por cluster de causa raíz

- **C1 — El AI reviewer corría a ciegas** (F51/F03/F37/F34): el worker ahora inyecta
  `review_context` (criterios + output del implementer + test-report) y la instrucción
  de `<verdict>` obligatorio al contenedor del reviewer (`build_review_preamble`); el
  parser de veredicto es tolerante a deriva de formato; un fallo de INFRA del reviewer
  (crash/timeout/model_unresolved) ya **no** se trata como reject (no rebota tareas
  correctas a `blocked`); el veredicto estructurado se fuerza vía `tool_choice` en HTTP.
- **C2 — Self-review autoritativa sobre-escalaba** (F24/F29/F33): la clasificación
  producing/research es namespace-aware (producción vía MCP/`shell_exec`/custom ya
  cuenta); marcadores de prosa ambiguos retirados; un `finish_status=failed`
  auto-declarado ya no puede aprobarse como `done` (escala a `needs_human_review`,
  ADR 0088 D1).
- **C3 — Pérdida de eventos sin reconciler** (F01/F02/F04/F05/F07/F08/F09 + P0.6):
  el dispatch del orchestrator es crash-safe (lecturas de BD dentro del try con revert;
  `in_progress` solo tras enqueue OK; revert re-publica `ready`); claim atómico
  `UPDATE…WHERE status='ready'`; errores transitorios de BD se reintentan (NACK) en
  vez de dead-letter+ACK; recarga de Agent con filtro tenant/deleted_at; review
  dispatch idempotente. **Nuevo beat reconciler** (`reconcile_pipeline_state`, 90s):
  tareas `in_progress` con execution terminal → transicionan; `in_review` huérfanas →
  re-anuncio; planes completos aún `in_progress` → `pending_human_validation` (+
  autostart del review-runtime, fuente única `api_server.review_autostart`).
- **C4 — Post-run del worker no atómico** (P0.5/F10/F12/F46/F52): la transición de la
  tarea es **atómica con `finalize_execution`** (se acabó la ventana de crash que
  dejaba la tarea `in_progress` con execution terminal + re-ejecución); `finalize` es
  idempotente y preserva `cancelled`/`superseded`; `awaiting_human_approval` sin payload
  de aprobación ya no cae en silencio al implementer-path.
- **C5 — Contrato contenedor↔worker frágil** (F16/F17/F18/F19/F21/F22/F23): el worker
  re-parsea `container_result.logs` como fallback cuando el stream en vivo pierde la
  línea final (se acabó el "exited 0 with no result" espurio); el contenedor se drena a
  EOF antes de `remove`; el pump en vivo sigue **solo stdout** (canal estructurado, sin
  demux — fix de una regresión que dejaba el stream vacío); el contenedor recibe un
  **grace** sobre el wall-clock interno (el aborto limpio gana al kill duro); el boot
  emite `execution.error` ante spec inválido; `ensure_reachable` reintenta; el connect
  MCP no cuelga el teardown.
- **C6 — Adaptadores de proveedor poco robustos** (F25/F30/F31/F32/F35/F36): timeout
  por llamada + reintento con backoff (429/5xx/timeout) → el grafo captura la excepción
  tipada y aborta limpio (`provider_timeout`/`provider_error`) preservando steps en vez
  de crashear a `blocked`; `claude_sdk` ya no auto-ejecuta tools nativas sin host-tools;
  args de tool malformados/truncados ya no degradan a deliverable vacío/inconclusive
  silencioso; el system prompt de `decide` ya no contradice `submit_result`; precedencia
  explícita con múltiples tool_calls.
- **C7 — Taxonomía estado/finish/UI incoherente** (F11/F13/F14/F26/F27/F43/F44/F45/
  F47/F48/F49/F50): `cancelled`→`cancelled` (no `blocked`); commit del worktree también
  en escalado + `commit_failed` visible; `abort_code` de escalado unificados en
  `SafeguardCode`; status huérfano `awaiting_human`→`blocked` (+ migración con CHECK
  `ck_tasks_status_valid` y data-migration); el panel de escaladas incluye `blocked`+
  abort_code de review; `completed_at` coherente; **bug latente corregido**:
  `_OPEN_TASK_STATUSES` ahora incluye `ready`/`assigned_to_human`/`awaiting_human_approval`
  (un plan ya no se cierra con tareas pendientes); la UI de Runs refleja el estado
  terminal en vivo (polling + frames `execution.finished`/`error`), el timeline ya no se
  congela (el step se lee de `payload.step`), y colorea/filtra `needs_human_review`/
  `awaiting_human_approval`/`cancelled`.
- **C8 — Lifecycle del review-runtime** (F39/F40/F41, ADR 0088 D2 / ADR 0063
  des-diferido): autostart de la validación humana al cerrar el plan (idempotente,
  disparado por orchestrator **y** reconciler); la expiración transiciona el plan
  (`pending_human_validation`→`blocked`) + notifica; los contenedores de sesiones
  terminales se destruyen; el `DEFAULT_TENANT_CAP` se aplica en el spawn de producción.

## Verificación

Suite unit **1797** verde · contenedor agent-runtime **114** · shared-llm **82** ·
integración con BD/Docker (ejecución, orchestrator, reconciler, autostart/expiración del
review-runtime, panel de escaladas, migraciones reversibles) verde · `ruff` limpio en
todos los ficheros tocados · migración Alembic `0101` reversible (round-trip).

No verificable sin el stack completo (best-effort, guardado con try/except): el spawn
real del contenedor review-runtime y el `docker rm -f` del reaping. Gate de `mypy
--strict` (pre-commit) y QA visual de la UI quedan para el despliegue del operador.
