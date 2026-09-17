---
plan_id: harness-06-assurance-replay-caos-2026-09-17
title: Assurance del harness — tracing extremo a extremo, replay, evals honestos, caos y evidence gate
status: pending_approval
blocking_plan:
  [
    harness-03-efectos-tool-gateway-2026-09-17,
    harness-04-kb-reproducible-2026-09-17,
    harness-05-memoria-gobernada-2026-09-17,
  ]
started_at: null
completed_at: null
estimated_duration_calendar: 2 semanas
estimated_effort_person_days: 7-8
estimated_cost_human_eur: 3.200 € – 5.000 €
estimated_cost_ai_eur: 40 € – 100 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P1
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-06 — Assurance: tracing, replay, evals, caos y evidence gate

## Cabecera

| Campo                 | Valor                                                                                 |
| --------------------- | ------------------------------------------------------------------------------------- |
| **ID del Plan**       | `harness-06-assurance-replay-caos-2026-09-17`                                         |
| **Prioridad**         | P1                                                                                    |
| **Bloqueado por**     | `harness-03`, `harness-04`, `harness-05`                                              |
| **Coordinar con**     | `prod-08-observabilidad-alertas` (`pending_approval`), `gov-01` (eval gate), ADR 0151 |
| **Rama git sugerida** | `plan/harness-06-assurance`                                                           |

> **Estado**: reutiliza las métricas y el gate de evals existentes. **Recorte
> respecto al borrador**: gov-01 ya separó dry-run de gate vivo y ya devuelve
> `INCONCLUSIVE` (código 2); aquí sólo se cierra que el dry-run sin secreto deje el
> job en verde, y se añade el digest de comportamiento.

## Resumen

El harness necesita **demostrar** sus garantías. Hoy OpenTelemetry instrumenta la
api-server (`telemetry/setup.py`) y nada más: ni el orchestrator, ni el worker, ni
el runtime, ni Celery propagan `traceparent`, así que una ejecución no tiene árbol
de spans. No hay manifiesto que reúna envelope, digests, checkpoints, ledger y
snapshots para reproducir un run. Y las matrices de fallo de harness-02/03/04 no
tienen aún una suite común ni un gate de release que las exija.

## Tareas

### Fase A — Tracing causal

#### `task_h06_01` — `traceparent` de extremo a extremo

- [ ] **Título**: el orchestrator inicia el span `plan.dispatch` y pone `traceparent`
      en el envelope (harness-01) y en los headers Celery; el worker lo restaura
      (`opentelemetry-instrumentation-celery` + propagación manual en `send_task`),
      lo pasa al contenedor por variable de entorno; el runtime lo restaura y lo
      añade a cada llamada a la API interna y a cada MCP. Atributos seguros:
      `execution_id`, `attempt_id`, `task_id`, `tenant_id`. Sin contenido en baggage.
      Logs structlog con `trace_id`/`span_id`. `telemetry/setup.py` se extrae a
      `packages/shared-domain/telemetry.py` (o paquete propio) para que los tres
      procesos compartan configuración.
      **Test**: `tests/integration/test_trace_context_propagation.py`. **Coste**: 1,5 d.

#### `task_h06_02` — Spans del harness y GenAI

- [ ] **Título**: spans `plan.dispatch`, `task.claim`, `execution.prepare`,
      `runtime.boot`, `agent.node.<name>`, `llm.call`, `tool.prepare`, `tool.execute`,
      `mcp.call`, `checkpoint.save`, `execution.finalize`. Atributos según las
      convenciones semánticas GenAI de OpenTelemetry **de la versión pineada**
      (`gen_ai.request.model`, tokens, latencia, `tool.name`/`tool.version`, códigos
      tipados del `ToolResult`). Prompts sólo como hash; contenido únicamente bajo
      `API_SERVER_TELEMETRY_DEBUG_CONTENT=1` y redactado.
      **Test**: `tests/integration/test_harness_span_tree.py`;
      `tests/security/test_telemetry_redaction.py`. **Coste**: 1,5 d.
      **Depende de**: task_h06_01.

### Fase B — Replay

#### `task_h06_03` — Manifiesto de replay

- [ ] **Título**: `GET /api/v1/executions/{id}/replay-manifest` (RBAC: operador del
      tenant) reúne envelope sellado, `image_digest`, modelo, `prompt_version`,
      `tool_catalog_digest`, `guardrail_version`, checkpoints (ids y digests), effect
      ledger, snapshots de KB y memoria, `dependency_report`. Marca las referencias
      no reproducibles (secretos, artefactos purgados). Digest del manifiesto
      firmado con la clave Fernet de plataforma.
      **Test**: `tests/integration/test_replay_manifest.py`. **Coste**: 1 d.

#### `task_h06_04` — Replay exacto y comparativo

- [ ] **Título**: `python -m api_server.cli.replay <execution_id> --mode exact|compare`.
      **Exacto**: el runtime arranca con `REPLAY_MODE=exact`, el Tool Gateway sirve
      los resultados grabados del ledger y **bloquea** cualquier efecto no
      `READ_ONLY`; KB y memoria vienen de los snapshots. **Comparativo**: permite
      otro modelo o prompt, conserva inputs, graba una ejecución nueva marcada
      `replay_of` y produce un diff por nodo (decisión, tool elegida, conocimiento
      usado). `PARTIAL` cuando falte un artefacto legítimamente purgado.
      **Test**: `tests/integration/test_exact_replay_no_effects.py`;
      `tests/integration/test_comparative_replay_diff.py`. **Coste**: 2 d.
      **Depende de**: task_h06_03.

### Fase C — Evals honestos

#### `task_h06_05` — El dry-run no es verde

- [ ] **Título**: en `eval-on-prompt-change.yml`, el paso dry-run (sin secreto)
      termina con código 2 y anotación `::notice::NOT_EVALUATED` en vez de 0; el
      job se marca `neutral` (no `success`) y el `merge-gate` que exige calidad
      conductual sólo acepta `PASS`. Un caso obligatorio saltado → `FAIL`. Estados
      explícitos `PASS | FAIL | INCONCLUSIVE | NOT_EVALUATED` en la salida JSON de
      `api_server.evals.ci_run`.
      **Test**: `tests/unit/test_behavioral_eval_gate_semantics.py`. **Coste**: 0,5 d.

#### `task_h06_06` — Digest de comportamiento

- [ ] **Título**: `behavior_digest()` canónico sobre prompts de runtime, personas
      built-in, schemas de tools, guardrails de plataforma, routing de modelos,
      políticas de KB/memoria, presupuestos y `graph_version`. El workflow dispara la
      eval cuando cambia el digest (hoy sólo cuando cambian paths de prompts) y
      explica qué componente cambió.
      **Test**: `tests/unit/test_behavior_digest.py`. **Coste**: 0,5 d.

### Fase D — Caos, SLO y evidencia

#### `task_h06_07` — Suite común de fault injection

- [ ] **Título**: `tests/e2e/chaos/` agrupa las matrices de harness-02 (kill por
      nodo), harness-03 (crash por efecto) y harness-04 (backend KB) y añade Redis,
      PostgreSQL, API interna, proveedor y MCP con latencia, timeout, respuesta
      truncada y payload malformado. Cada caso declara el resultado esperado según
      el `dependency_report`. Runner Docker, feature flag `CHAOS_ENABLED`, JUnit
      adjunto al PR de release.
      **Test**: `tests/e2e/chaos/test_harness_fault_matrix.py`. **Coste**: 1,5 d.

#### `task_h06_08` — SLO y alertas del harness

- [ ] **Título**: métricas `harness_dispatch_to_start_seconds`,
      `harness_run_outcome_total{outcome=done|failed|cancelled|rejected|degraded|inconclusive}`,
      `harness_checkpoint_save_seconds`, `harness_resumed_runs_total`,
      `harness_uncertain_effects`, `harness_degraded_dependencies_total{dep}`,
      `harness_outbox_lag_seconds`. Alertas con runbook en `docs/06-runbooks/` y
      anti-flapping. Sin ids como labels. Se añaden al overlay
      `docker-compose.monitoring.yml` existente (prod-08 las incorpora cuando arranque).
      **Test**: `tests/unit/test_harness_metrics_contract.py`. **Coste**: 0,5 d.

#### `task_h06_09` — Evidence gate de release

- [ ] **Título**: `python -m api_server.cli.release_evidence` genera un manifiesto
      con commit, digests de imágenes, head de migración, `contract_version`,
      JUnit de suites obligatorias (con conteo de casos ejecutados), resultado de la
      eval conductual, resultado de caos y validaciones humanas de los planes
      `harness-*`. Falta algo o aparece `NOT_EVALUATED` → código 1. Manifiesto
      inmutable, firmado, guardado en MinIO.
      **Test**: `tests/unit/test_release_evidence_gate.py`. **Coste**: 1 d.

#### `task_h06_10` — Diagnóstico por `execution_id`

- [ ] **Título**: `GET /api/v1/executions/{id}/diagnostics` y su vista en el visor de
      la ejecución: attempts, checkpoints, `dependency_report`, snapshots, efectos
      (con estado), outbox, spans enlazados y acción recomendada para checkpoint
      corrupto, efecto `UNCERTAIN` u outbox `failed`. Exporta JSON para soporte. Sin
      secretos.
      **Test**: `tests/integration/test_execution_diagnostics_api.py`;
      vitest del panel. **Coste**: 1 d.

## Criterios de cierre

- [ ] Una ejecución tiene árbol de spans desde `plan.dispatch` hasta `execution.finalize`.
- [ ] La telemetría no contiene secretos en configuración normal.
- [ ] El replay exacto no produce efectos reales.
- [ ] El gate de evals distingue `NOT_EVALUATED` de `PASS` y el job no sale verde sin evaluar.
- [ ] Un cambio en cualquier componente behavior-critical dispara la eval.
- [ ] La suite de caos cubre los límites definidos sin skips.
- [ ] Las métricas separan éxito, degradación e inconcluso.
- [ ] El release gate consume evidencia firmada.
- [ ] Un operador diagnostica un run desde un solo `execution_id`.

## Riesgos

1. **Telemetría con PII o secretos**: hash por defecto y test de redacción.
2. **El replay ejecuta una tool real**: deny-by-default en el gateway bajo `REPLAY_MODE`.
3. **Evals inestables**: datasets versionados y tolerancias justificadas (gov-01).
4. **El caos rompe entornos compartidos**: flag y runner aislado.
5. **Cardinalidad**: ids en traces y logs, nunca en labels.

## Pruebas humanas

```yaml
- id: human_h06_01
  title: Diagnóstico de un run degradado
  steps:
    - Ejecutar una tarea con la memoria opcional caída.
    - Abrir el diagnóstico por execution_id.
    - Localizar dependencia, checkpoint, contexto KB y resultado.
  expected: Causa e impacto comprensibles sin correlacionar logs a mano.

- id: human_h06_02
  title: Replay comparativo seguro
  steps:
    - Elegir un run con una tool de escritura.
    - Ejecutar replay comparativo con otro modelo.
    - Comprobar el recurso externo y el diff.
  expected: No se repite la escritura; el diff muestra dónde divergieron los modelos.
```
