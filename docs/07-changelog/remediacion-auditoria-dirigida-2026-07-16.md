---
title: Remediación de la auditoría dirigida 2026-07-16 (AUD16)
plan_id: remediacion-auditoria-dirigida-2026-07-16
date: 2026-07-16
docs_language: es
---

# Remediación AUD16 — tools por proveedor, notificaciones visibles, contabilidad, monitorización

Implementación completa (TDD, commits atómicos, orden directa del operador) del
delta confirmado por [`auditoria-dirigida-2026-07-16`](../roadmap/auditoria-dirigida-2026-07-16.md).
22 commits en `plan/runs-visor-trabajo`.

## Fase A — Camino HTTP de tools por proveedor

- **AUD16-01 (crítico)**: `submit_result`/`submit_verdict` viajan con el
  envelope OpenAI a ollama/copilot/azure (antes: 400 inmediato en endpoints
  estrictos, tool fantasma en ollama; un test de integración llevaba rojo
  desde el 27-06 documentándolo). Tests de wire-format decide+review para los
  3 kinds.
- **AUD16-04**: `search_code` retirado de los system prompts (no tiene
  executor; 7/7 llamadas fallidas).
- **AUD16-05**: claude_sdk anuncia el JSON Schema ÍNTEGRO de cada tool (antes
  degradaba a `{campo: tipo}` perdiendo `required`/`enum`/anidados).
- **AUD16-06**: el streaming OpenAI-compat acumula los deltas de `tool_calls`
  y los entrega parseados en el chunk final (antes se descartaban en silencio).

## Fase B — Notificaciones visibles para humanos

- **AUD16-10**: inbox de PLATAFORMA (`GET/POST /notifications/platform/logs*`,
  System Admin, BYPASSRLS, solo `tenant_id IS NULL`) + selector de scope en el
  admin-panel — ninguna notificación llegaba de facto a un ojo humano.
- **AUD16-11**: `notification_logs.subject/body` (migración 0113, truncados
  200/2000) persistidos para in_app y renderizados en el inbox.

## Fase C — Contabilidad y memoria

- **AUD16-15**: el price-snapshot resuelve la clave REAL del runtime (kind en
  cada step `model_call` + alias de familia + fallback por model_id único) —
  el coste facturable estaba ciego en 128/128 executions.
- **AUD16-14**: el destilador de memorias resuelve el modelo HEREDADO del
  agente (misma cadena que el dispatch); todo fallback queda logueado — el
  100% de las memorias vivas las había destilado `llama3.2:1b`.
- **AUD16-17**: el default operativo de estados memorizables incluye fracasos
  (invariante con el default de la política).
- **AUD16-18**: dedup por contenido en `recall()` (sobre-muestreo 4x) +
  consolidación idempotente de duplicados exactos
  (`workers/maintenance/memory_dedup.py`, ejecutada en el deploy).

## Fase D — Robustez de runs

- **AUD16-02**: fin del éxito falso de las tools de orquestación —
  `task_comment` se drena de verdad a `PlanComment` post-run;
  `kanban_update`/`agent_invoke`/`notify_user` devuelven error honesto y salen
  del anuncio hasta tener consumidor.
- **AUD16-20**: `stack_exec_unavailable` tras 3 fallos de transporte
  consecutivos (una cascada 502 quemó 50 iteraciones el 07-02).
- **AUD16-21**: los cierres administrativos dejan rastro — `task_audit_events`
  en sweeper/supersede + `memorize_skip_reason='administrative_finalize'` en
  el primitivo de sellado.
- **AUD16-22**: el `what_to_fix` del reviewer queda acotado a acciones
  ejecutables por el agente (nunca git/commit/push).
- **AUD16-23**: evento `provider_credential_invalid` (platform, PRIORITY) en
  el PRIMER abort con marcadores de credencial/cuota — 17 aborts pasaron sin
  que nadie se enterara.

## Fase E — Monitorización

- **AUD16-07**: regla `CadvisorDegraded` + gotcha
  `cadvisor-containerd-snapshotter` (cAdvisor ciego en Docker Desktop con
  healthcheck verde).
- **AUD16-09**: heartbeat del sampler + `collector_up` por colector + reglas
  `MetricsSamplerStale`/`MetricsCollectorDown` — «No data» deja de ser
  indistinguible de «sampler muerto».
- **AUD16-19**: métricas offsite del backup + `BackupOffsiteStale` (gated a
  ts>0) + runbook.
- **AUD16-08 + menores**: runbook de degradaciones Windows-dev, healthcheck de
  node-exporter, `GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false`.

## Fase F — Pulido

- **F1**: colas del dispatcher con exchange/routing propios por lane.
- **F2**: verificado SIN cambio (la bandeja de escaladas ya filtra por estado
  de la task).
- **F3**: `COMPOSER_HOME` explícito en el template php-phpunit.
- **F4**: errores de transporte de `complete()` tipados en los 3 providers
  HTTP (el córtex devolvía un 500 crudo por un ReadTimeout).
- **F5**: el bus de eventos/DLQ del dispatcher comparte DB de Redis con sus
  consumidores en el compose generado (antes DB 3 vs DB 0: la alerta de DLQ no
  podía disparar en prod).
- **F6**: el login escribe `audit_log` (success/failure/mfa_challenge) —
  llevaba 0 filas en toda la historia.

## Gated (operador)

Activar neonize; canal externo del operador + preferencias; smoke e2e
copilot/azure con credenciales; destino offsite real; plan demo «MVP Hello
World PHP» varado; decisión ADR 0108; política de guardrails/aprobaciones a
activar (con prod-03).

## Validación

`pytest tests/unit` 2289 ✓ (cobertura 55,1%, ratchet 31%); `tests/docs` +
runtime + shared-llm 612 ✓; `tests/migrations` 33 ✓ (0113 reversible);
integración dirigida de cada fase contra Postgres/Redis reales; Vitest
admin-panel 221 ✓ + tsc limpio. `tests/security` mantiene los 4 fallos
preexistentes de AUD14-01/02 (dueño: plan 07-14).
