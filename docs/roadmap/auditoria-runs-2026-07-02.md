---
title: Auditoría del sistema de ejecuciones (runs, memoria, workers, review) + remediación
date: 2026-07-02
status: remediation_implemented
scope: 51 executions del tenant Demo (2026-06-29 → 07-02, proyecto CI4), BD viva + logs + código
method: workflow de 6 analistas forenses + verificación adversarial por hallazgo + revisión de workflow/prompts
docs_language: es
related_adrs: ["0087", "0089", "0090", "0091", "0092", "0093", "0094", "0095", "0096"]
---

# Auditoría de ejecuciones 2026-07-02 — informe y remediación

Petición del operador: analizar las tareas ejecutadas en BD y revisar ejecuciones, memoria y
workers para ver qué está corregido y qué queda por corregir. Este doc persiste el informe
(convención: entregables en `docs/roadmap/`) y el estado de la remediación implementada el mismo día.

## 1. Corregido y VERIFICADO con datos reales (pre-auditoría)

| Fix                                                                                  | Evidencia                                                                                                                                                                                                            |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Convergencia núcleo (ADR 0087/0089/0090/0091/0092 + acceptance_criteria del planner) | 07-01: 8 runs `done` en 2–17 iteraciones; `max_iterations_exceeded` (siempre iter=50) desapareció tras el deploy del 06-30                                                                                           |
| ADR 0095 — reviewer con worktree read-only                                           | **Par de regresión de referencia**: 019f139b (06-29, ciego: `read_file` "not a file", 205 steps, aborted) vs 019f184f (06-30 11:34: read_file OK, 13 iter, approve). 0 reviews abortadas desde el deploy             |
| Ciclo work→review→retry con feedback                                                 | dispatch.py→execution.py→**main**.py; re-dispatch automático en ~26 s tras un reject                                                                                                                                 |
| Asignación por rol (ADR 0091), colas, orquestador                                    | correcta en BD; Redis/Celery sin backlog ni zombis                                                                                                                                                                   |
| Recovery post-apagado                                                                | los 2 runs colgados la noche 07-01→07-02 fueron `superseded` + re-encolados sin perder mensajes                                                                                                                      |
| Memorizer mecánicamente sano                                                         | 26/27 dones memorizados; 74/74 embeddings vector(768)+HNSW; recall RRF verificado en vivo                                                                                                                            |
| Aclarados como POR DISEÑO (no bugs)                                                  | `review_sessions`=0 (review de PLAN, ADR 0063, nunca disparado); `done`+`commit_failed` (marcador; el race de fondo se arregló el 06-30); iteraciones=0 en BD durante el run (finalize-only, progreso vivo en Redis) |

## 2. Incidente crítico del 2026-07-02 (causa de los fallos "nuevos")

`/data/agent-platform` (bind en el rootfs del VM de Docker Desktop) reapareció **vacío y root:root**
en el arranque del engine (07:32). Consecuencias: EACCES permanente al provisionar worktrees (el
one-shot `worktrees-init` no se re-ejecuta en un engine-restart), runs "a ciegas" en tmpfs quemando
50 iteraciones (019f21be-e4ec/e5c0), `stack_exec` con docker 400 y 502 engañoso, y **pérdida de los
bare repos** con el trabajo de las 8 tareas done (sin backup). El `max_iterations_exceeded` de hoy
NO es regresión de convergencia: es este incidente. Segunda recreación observada (07-01 y 07-02).

## 3. Remediación IMPLEMENTADA (2026-07-02, esta rama)

Fase 0 — infra:

- **F0.1** Entrypoint self-heal del worker (`apps/workers/docker-entrypoint.sh`): mkdir+chown de
  `/data/agent-platform` en CADA arranque (root → setpriv uid 1000); el one-shot queda de red de seguridad.
- **F0.2** Fail-fast `workspace_unavailable`: la provisión fallida de worktree ya NO degrada a tmpfs
  "a ciegas" para implementadores — aborta sin lanzar contenedor (fallback tmpfs solo reviews/sin-plan).
- **F0.3** `stack_exec` accionable: valida el bind-source antes de `containers/create` y captura
  `docker.errors.APIError` → error estructurado (adiós al 502 engañoso).
- **F0.4** Backup: `WORKERS_BACKUP_BIND_PATHS` (default `/data/agent-platform`) entra en el bundle
  (`bind_tar`); runbook de durabilidad corregido (afirmaba persistencia que los datos desmienten).
  OJO dev: el backup diario corre en la cola `privileged` que los workers dev no consumen — usar
  `scripts/backup-data.ps1` programado.
- **F0.6** Reaper: el sweep elimina contenedores agent-runtime `exited` cuya execution es terminal.

Fase 1 — bugs:

- **F1.1** Panel de escaladas por ESTADO del último run (`needs_human_review`), robusto a
  abort_codes nuevos — la task blocked de hoy era invisible.
- **F1.2** ADR 0096: un review no-`done` no puede CERRAR la task con su approve (→ blocked +
  anotación para `approve_manual`); su reject SÍ fluye (dirección conservadora).
- **F1.3** `needs_human_review` es terminal para el memorizer (12 runs sin intento ni skip_reason).
- **F1.4** tokens=0: el turno con tool call interrumpido pierde el `usage` del ResultMessage —
  `_harvest` ahora suma el usage por-AssistantMessage y cae a `model_usage` (el coste nunca se
  perdió: MAX_COST seguía armado; MAX_TOKENS estaba desarmado).
- **F1.5** `<finish status="…"/>` parseado de la prosa de claude_sdk → `finish_status` y la
  escalación `agent_reported_failure` (D19) dejan de ser código muerto en producción.
- **F1.6** Contratos de prompt: (a) el reviewer certifica contra los `acceptance_criteria` REALES
  (antes `task.description`); (b) `shell_exec` ya no sugiere git (contradicción con el system
  prompt, exit 128 garantizado); (c) system prompt propio para runs de review (`_REVIEW_RUN_SYSTEM`).

Fase 2 — memoria:

- **F2.1** El destilador usa el LLM del AGENTE de la execution (provider_id, ADR 0082) con fallback
  al Ollama local (`WORKERS_MEMORIZER_USE_AGENT_PROVIDER`, default on) — llama3.2:1b producía ~50%
  ruido (tautologías, URL fabricada) que contaminaba el recall.
- **F2.3** `llm_empty` separado en 3 causas: `llm_error` / `llm_unparseable` / `llm_empty`
  (legítimo); `distill_model` registra provider:modelo real.

Fase 2b — estabilización del loop (revisión de workflow/prompts):

- **F2b.1** Bloque `PROGRESS` siempre-visible (iteración N/límite + ficheros ya escritos) — ataca la
  causa raíz del read-churn (ventana de contexto de 8 items en runs de 50 iteraciones).
- **F2b.2** Aviso "quedan N iteraciones — cierra" al 80% del presupuesto.
- **F2b.3** Nudges de research/churn al canal sticky `GUIDANCE` (antes evictables por la ventana).
- **F2b.4** Feedback de review sticky 600 → 2000 chars (el rejection estructurado se cortaba).
- **F2b.5** Presupuesto propio para reviews: 25 iter / 1h (evidencia: convergen en 13-22 steps).
- **F2b.6** Descripciones de tools unificadas a inglés (consistencia con system prompt/nudges).

## 4. Pendiente / backlog declarado

- **Operacional**: re-ejecutar el plan CI4 del tenant Demo (los repos perdidos no tienen backup);
  desbloquear/relanzar las 2 tasks blocked (`POST /tasks/{id}/human-action`); purgar/regenerar las
  ~67 memorias-ruido destiladas por el 1b; rebuild+redeploy de imágenes (workers/api-server/
  orchestrator/agent-runtime) para activar todo lo anterior.
- **Estructural** (medio plazo): mover `/data` a almacenamiento realmente persistente en Docker
  Desktop/WSL2 (path respaldado por Windows con identity-bind, ver runbook); push incremental de
  bare repos a remoto tras cada merge de task.
- **Deuda declarada en docs**: followups F1–F6 de registry-egress (ADR 0094; F2/F4 primero);
  ADR 0084-B (bucle reviewer completo, plan prod-17); review de PLAN (ADR 0063) sin ejercitar e2e;
  métrica de colas sin sink; valorar inyección automática de top-K memorias al prompt (la memoria
  sigue siendo write-mostly: 2/15 runs recientes llamaron `memory_recall`).
- **Diagnóstico abierto**: `runtime_stuck_no_progress` de 019f1d60 (0 iteraciones, 1h48m hasta el
  abort) — probable víctima temprana del mismo patrón de workspace; re-evaluar tras el e2e limpio.

## 5. Verificación

- Unit/integración: suites de workers, api-server, orchestrator, agent-runtime y shared-llm en
  verde tras cada fix (TDD: cada fix nació de un test rojo).
- E2E pendiente de deploy: re-lanzar el plan CI4 y comparar contra el baseline del 07-01
  (objetivo: 0 `max_iterations_exceeded`, tokens>0 en todos los runs terminados, panel de escaladas
  poblado, memorias destiladas con el provider del agente).
- Pares de regresión de referencia: reviews 019f139b (ciego, pre-fix) vs 019f184f (post-fix);
  tokens 019f1872 (0 tokens, 22 tool calls) vs 019f18a6 (2569 tokens).
