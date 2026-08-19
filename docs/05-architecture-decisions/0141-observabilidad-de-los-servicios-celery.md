---
title: "ADR 0141: Observabilidad de los servicios Celery — sin paquete shared-logging, y métricas de workers por textfile-collector"
status: accepted
date: 2026-07-31
deciders: [claude-code]
relates_to: [0139, 0140]
plan_referenced: prod-08-observabilidad-alertas
task: [task_prod08_shared_logging_08, task_prod08_metrics_workers_05]
# `task_gov_01`: las DOS casillas que este ADR referencia quedaron sin objeto tal
# y como estaban escritas (no se extrae shared-logging; PoolMetrics y el
# start_http_server del plan ya no existen). Ambas cerradas en negativo.
rejects: [task_prod08_shared_logging_08, task_prod08_metrics_workers_05]
docs_language: es
---

# ADR 0141: Observabilidad de los servicios Celery

> **Estado: `accepted`.** A diferencia de los ADR 0139 y 0140, aquí no hay nada
> que comprometa recursos ni calendario del operador: son dos decisiones
> puramente técnicas sobre **cómo** implementar dos tareas de prod-08, ambas
> reversibles y cubiertas por tests. Se documentan porque **se apartan de lo que
> el plan prescribía**, y esa divergencia merece quedar por escrito.

## Decisión 1 — No se extrae `packages/shared-logging`

### Lo que el plan pedía

`task_prod08_shared_logging_08`: mover `api_server/logging/{setup,pii,context}.py`
a `packages/shared-logging/`, porque «hoy orchestrator (`__main__.py:23`) y
watchdog (`__main__.py:65`) importan `api_server.logging` cruzando apps, un
anti-patrón». Presupuesto: 1,5 persona-días.

### Por qué no

Al verificar la premisa, el «cruce de fronteras» que el paquete iba a resolver
resultó ser mucho más grande que el logging, y a la vez inexistente como
problema de despliegue:

1. **Los tres consumidores corren sobre la imagen del api-server.** Los
   Dockerfiles de `workers`, `notification-dispatcher` y `orchestrator` empiezan
   todos por `ARG BASE_IMAGE=agentic-platform/api-server:ci` / `FROM
${BASE_IMAGE}`. `api_server` ya está en `/opt/venv` en tiempo de ejecución.
   No hay ninguna topología en la que el import falle.

2. **`workers` importa `api_server` en ~50 sitios más**: `api_server.db.domain`,
   `api_server.db.approval_repo`, `api_server.cortex.*`, `api_server.memorizer`,
   `api_server.db.platform_settings`… Extraer _solo_ el logging no elimina el
   acoplamiento: lo **disfraza**, dejando la sensación de que el problema está
   resuelto mientras las otras cincuenta aristas siguen ahí.

3. El coste real de la extracción no es mover tres ficheros: es un paquete nuevo
   en el workspace, su `pyproject`, su instalación en cuatro Dockerfiles, los
   shims de re-export para no romper los importadores actuales, y sus tests.
   Todo ello para no cambiar ni un comportamiento observable.

### Qué se hizo en su lugar

Un módulo nuevo **dentro** de `api_server.logging` —`celery_pipeline.py`— que
los dos servicios Celery importan igual que ya hacían orchestrator y watchdog.
El valor que la tarea perseguía (JSON + PII + `service` en workers y
notification-dispatcher) se entrega íntegro.

**Cuándo reabrir esta decisión**: el día que algún servicio deba desplegarse sin
la imagen del api-server debajo. Entonces el paquete compartido deja de ser
cosmético y pasa a ser necesario — pero habrá que extraer bastante más que el
logging.

## Decisión 2 — Los workers NO exponen `/metrics` por HTTP

### Lo que el plan pedía

`task_prod08_metrics_workers_05`: «en `celery_app.py` arrancar
`start_http_server(9540)` en `worker_process_init`», y conectar «el `PoolMetrics`
huérfano de `runtime_pool.py:125`».

### Por qué no

Dos problemas, uno de hecho y otro de diseño:

**El hecho**: `runtime_pool.py` **no existe**. Se borró en el commit `7959cdcb`
(«fuera 2.200 líneas de código que no ejecuta nadie»). `PoolMetrics` es un
fantasma: no hay nada que conectar. El plan es anterior a ese borrado.

**El diseño**: `start_http_server` en `worker_process_init` es un patrón roto
con el pool **prefork** de Celery, que es el que este stack usa. `worker_process_init`
se dispara **en cada proceso hijo**; el primero se queda el puerto 9540 y los
demás fallan con `EADDRINUSE`, o —peor— si el bind prospera, Prometheus scrapea
el estado de **un hijo al azar** y lo presenta como si fuera el del worker.
Exponer métricas per-proceso desde un pool multiproceso exige
`PROMETHEUS_MULTIPROC_DIR` y un registro agregador, complicación que el plan no
contempla.

### Qué se hizo en su lugar

Nada: **ya estaba resuelto, y mejor**. `workers/queue_metrics.py` publica por el
**textfile collector de node-exporter** (`workers/textfile_collector.py`), en un
muestreo por beat cada 30 s:

| Métrica                                      | Qué mide                              |
| -------------------------------------------- | ------------------------------------- |
| `agentic_celery_queue_depth{queue}`          | mensajes en cada cola (Redis `LLEN`)  |
| `agentic_dlq_depth{stream}`                  | entradas dead-lettered (Redis `XLEN`) |
| `agentic_tasks_by_status{status}`            | tareas por estado del ciclo de vida   |
| `agentic_executions_24h{status}`             | ejecuciones por estado, últimas 24 h  |
| `agentic_sampler_last_run_timestamp_seconds` | heartbeat del sampler                 |
| `agentic_sampler_collector_up{collector}`    | 1/0 por colector                      |

Es **superior** al exporter in-process para este caso: consulta la BD y el
broker, así que ve **el sistema entero** en vez de un proceso; no compite por
puertos; y no necesita que el worker esté sirviendo HTTP para que la métrica
exista.

### La consecuencia que hay que entender

Sin target de scrape, **los workers no tienen serie `up`**, así que
`ServiceDown` no los cubre. La señal equivalente es `MetricsSamplerStale`: si el
worker de mantenimiento muere, el heartbeat deja de refrescarse y la alerta
salta a los 5 minutos.

Esa cadena tiene una dependencia que conviene tener presente: **pasa por
node-exporter**. Si node-exporter cae, se van con él TODAS las métricas de
aplicación. Por eso la regla `ServiceDown` (`up == 0`) añadida en prod-08 cubre
también los targets de infraestructura, y no solo el api-server.

El api-server **sí** expone `/metrics` (`api_server/metrics.py`): es un único
proceso uvicorn, así que un registro in-process es correcto, y es el único sitio
donde viven las peticiones HTTP.

## Consecuencias

- `apps/workers/src/workers/celery_app.py` y el `celery_app.py` del
  notification-dispatcher importan `api_server.logging.celery_pipeline`. Si
  alguna vez se desacoplan las imágenes, ese import es lo primero que rompe —
  y está señalado con un comentario en ambos ficheros.
- No hay `packages/shared-logging`. Quien busque el logging compartido lo
  encuentra en `api_server.logging`.
- El catálogo de métricas queda repartido en dos caminos por diseño: HTTP
  in-process en el api-server, agregados de sistema por textfile-collector desde
  los workers. `docker/monitoring/prometheus/prometheus.yml` lo documenta en el
  punto donde importa: el bloque de `scrape_configs`.
