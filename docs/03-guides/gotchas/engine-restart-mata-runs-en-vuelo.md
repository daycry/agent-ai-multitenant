---
title: "Un reinicio del Docker engine mata los runs en vuelo (provider_error transitorio + ejecuciones zombi)"
tags: [docker, engine-restart, runs, provider_error, zombies, celery, workers]
---

# Un reinicio del Docker engine mata los runs en vuelo

## Síntoma

Tras un reinicio de Docker Desktop / del engine (crash, update, `wsl --shutdown`):

1. Tareas que estaban `in_progress` pasan a `blocked` con ejecuciones `aborted` /
   `abort_code=provider_error` de **1 iteración y pocos segundos**, agrupadas en los
   ~3 minutos posteriores al arranque.
2. Otras tareas quedan `ready` pero **no se re-despachan**: su última ejecución sigue
   `running` para siempre (zombi — el contenedor murió con el engine), y la promoción
   del DAG salta tareas con ejecución activa.
3. Puede aparecer algún `workspace_unavailable` por un `index.lock` huérfano en el
   worktree (git murió a mitad de operación).

## Causa raíz

- Los agent-runtime son contenedores efímeros SIN restart policy: el engine-restart los
  mata. Celery re-entrega los mensajes en cuanto los workers vuelven, pero los primeros
  relanzamientos corren **mientras la infra aún se asienta** (egress-proxy, redes
  per-task, ollama): el primer call LLM falla → `provider_error` legítimo pero
  transitorio.
- Las filas `executions.running` de los contenedores muertos no las cierra nadie hasta
  el sweep de zombis (`sweep_stale_executions`, umbral 7 h — pensado para OOM/hard-limit,
  no para reinicios).

## Fix / Remediación

1. **Zombis**: cerrarlos a mano (o esperar el sweep):
   ```sql
   UPDATE executions SET status='failed', abort_code='stale_after_worker_loss',
     completed_at=now()
   WHERE status='running' AND created_at < '<instante del reinicio>';
   ```
2. **Tareas blocked por provider_error del arranque**: `status='backlog'`,
   `retry_count=0` y disparar `workers.promote_ready_plans`.
3. **index.lock huérfano**: borrar el lock que cite el error de provisión
   (`.../repos/<repo>.git/worktrees/<task>/index.lock`).
4. Desde 2026-07-03 el step/`output` del abort lleva el **mensaje real** del provider
   (antes solo el código, y el detalle moría con el contenedor reapeado) — mira ahí
   antes de asumir credencial caducada.

## Mejora pendiente

Boot-reaper en el arranque del worker: cerrar `running` sin contenedor vivo
(etiqueta `com.agentic-platform.execution-id`) en vez de esperar 7 h. Valorado en la
revisión 2026-07-03; no implementado aún.
