---
name: fix-review-task-no-bloquea-por-hallazgos
description: Fix agent-runtime — una tarea de REVISIÓN/análisis no se bloquea por reportar hallazgos; entregable en prosa cuenta para escalar (no abortar)
metadata:
  node_type: memory
  type: project
  originSessionId: ed356da1-3ffb-49dc-a846-642abace2f05
  modified: 2026-07-24T19:48:05.505Z
---

Diagnóstico + fix de un run bloqueado (2026-07-24, tarea `019f8e48` «Revisión de seguridad básica», plan `019f8e47`, proyecto CI4 `123b2f2c`, ejecución `019f9323`).

**Causa raíz**: `has_produced` (agent*runtime) SOLO latchea con un \_producing tool* exitoso (`_PRODUCING_TOOLS` = write_file/edit_file/create_file/shell_exec/stack_exec/apply_patch; graph.py:1017). Una tarea de **revisión/análisis** entrega su informe en prosa vía `submit_result` y no escribe ficheros → `has_produced` es estructuralmente False. Cuando el `self_review` la rechaza (los criterios que genera el planner van como «no debe existir X» y el informe reconoce hallazgos no bloqueantes → el modelo de review lee «hay hallazgos ⇒ criterios no cumplidos ⇒ fail») y en el reintento salta `research_exhausted` (elegible porque `review_retries>0`), el desenlace pasaba por `_abort_or_escalate_status(has_produced=False, is_review=False)` → `aborted` → tarea **blocked**. Es un FALSO NEGATIVO: el entregable estaba bien y cumplía los criterios.

**Fix (commit `3a886a47`, rama plan/runs-visor-trabajo, TDD 387/387):**

- **A**: nuevo flag `_AgentLoop.has_deliverable`, latcheado en `finalize` cuando el agente termina con entregable real (`finish_status in success/partial` o output no vacío). `_abort_or_escalate_status(has_produced, *, is_review, has_deliverable)` escala a `needs_human_review` si hay entregable — en TODOS los trip sites del grafo (max_iterations, budgets, research_exhausted, stack_exec, loop). Un run genuinamente estéril sigue abortando limpio (respeta el aviso G3/r4 de no contaminar la cola humana).
- **B**: `_trip_outcome(...)` compartido (extraído de `_loop_trip_outcome`); un `research_exhausted` DENTRO de un ciclo de self-review (`review_retries>0`) se reporta como `SELF_REVIEW_STALEMATE` legible con el feedback del revisor en el output («criterios contradictorios/insatisfacibles»), no un opaco `research_exhausted`.

El worker YA mapea `needs_human_review` → `in_review` (cola humana; execution.py:325-327/1467-1498), así que ahora el run va a validación humana en vez de blocked.

**DESPLEGADO 2026-07-24**: rebuild `agent-runtime:v1` (`--build-arg WITH_CLAUDE=1 -f docker/agent-runtimes/agent-runtime/Dockerfile .` contexto raíz) — el worker lo lanza fresco por tarea (`WORKERS_AGENT_RUNTIME_IMAGE=agent-runtime:v1`), **no** hay que recrear servicios. Smoke en la imagen: A+B vivos.

**GOTCHAS aprendidos**:

- El bucle del agente (graph.py) corre en la imagen BASE `agent-runtime:v1`; los runtime-templates `agent-runtime-<id>:v1` (php-phpunit…) son SOLO el toolchain para stack_exec/tests (`FROM php:8.3-cli`, `ENTRYPOINT sleep infinity`) — NO contienen `agent_runtime`. Para un fix del grafo basta rebuild de la base.
- `_research_exhausted` (nudges.py:202) solo es elegible cuando `has_produced OR review_retries>0 OR is_review` — un trip ahí nunca es un abort estéril legítimo.
- `is_review` = el ROL reviewer de plataforma (revisa diff del implementador, sin self-review), NO una tarea cuyo título es "revisión".

**RESUELTO**: el run `019f8e48` se RELANZÓ con el fix vivo → convergió limpio en 14 iter (self-review pasó, done) y pasó a `in_review` (tiene reviewer) — ya NO blocked. Verificado en vivo 2026-07-24. El plan 019f8e47 volvió a in_progress y siguió. **Rama `plan/runs-visor-trabajo` EMPUJADA a origin hasta `3a886a47`** (incluye ADR 0129 fase 2 `ddfb3c82`, ADR 0130 `fd68069c` y este fix). Follow-up opcional (Fix C, no hecho): que el planner genere criterios con forma de ENTREGABLE para tareas de revisión, no aserciones «no debe existir X». Relacionado: [[reviewer-ciego-convergencia-fix]], [[supervision-runs-autofix-plataforma]].
