---
title: "Cualquier operación git o de `/data` va en el WORKER: la api-server no monta el volumen de repos"
area: docker, git, api-server, workers
encountered: 2026-07-24
stack: docker compose, DooD, volumen `agentic-platform-agent-data`
---

## Síntoma

Un endpoint de la api-server que lee un repo (por ejemplo el diff de una tarea)
devuelve **500**. En los logs, `git` se queja de que la ruta no existe, o de
`dubious ownership`.

## Causa raíz

Los bare repos viven en el volumen `agentic-platform-agent-data`, y **solo el
worker lo monta** (`WORKERS_DATA_ROOT=/var/lib/docker/volumes/agentic-platform-agent-data/_data`,
mount de identidad para DooD). La api-server **no tiene ese volumen**: la ruta
sencillamente no existe dentro de su contenedor.

Y aunque se montara, no bastaría: el worker corre como el usuario `app` (vía
`setpriv`), que es el **dueño** de los bares. Un `docker exec` como root sobre un
bare ajeno da `dubious ownership` de git.

## Fix

Delegar al worker por Celery en vez de ejecutar en la api-server. El endpoint
encola la operación y devuelve su resultado:

```python
# En la api-server: NO tocar /data. Se delega.
result = await run_worker_task("workers.compute_task_diff", task_id=str(task_id))
```

Regla general: **la api-server no toca ni git ni `/data`**. Si un endpoint lo
necesita, hay una tarea de worker que lo hace.

## Cómo verificar el fix

`docker exec agentic-platform-api-server-1 ls /var/lib/docker/volumes/agentic-platform-agent-data/_data`
→ no existe (correcto). El mismo `ls` en `agentic-platform-workers-1` lista los
tenants.
