---
title: Referencia — Métricas expuestas y sus labels
audience: system-admin
updated: 2026-08-01
docs_language: es
---

# Métricas de la plataforma

Catálogo de lo que la plataforma emite a Prometheus. Para qué hacer cuando una
alerta salta, ver [`docs/06-runbooks/observabilidad.md`](../06-runbooks/observabilidad.md).

## Los dos caminos de salida

El catálogo está repartido en dos transportes **por diseño**, no por accidente:

| Camino                                        | Quién          | Qué contiene                    | Frecuencia   |
| --------------------------------------------- | -------------- | ------------------------------- | ------------ |
| `/metrics` HTTP in-process                    | api-server     | peticiones HTTP + proceso/GC    | scrape (15s) |
| textfile-collector de node-exporter (`.prom`) | workers (beat) | agregados de BD, colas y broker | 30s          |

El api-server es un único proceso uvicorn, así que un registro in-process es
correcto. Los workers usan el pool **prefork**: un `start_http_server` por
proceso o se pelea por el puerto o expone el estado de un hijo al azar, así que
publican a fichero desde un muestreo por beat que además consulta la BD y ve el
sistema entero. Ver [ADR 0141](../05-architecture-decisions/0141-observabilidad-de-los-servicios-celery.md).

**Consecuencia operativa**: los workers **no tienen serie `up`**. Su señal de
vida es `MetricsSamplerStale` (el heartbeat del sampler), no `ServiceDown`.

## Catálogo de labels — CERRADO

Permitidos: `tenant_id`, `queue`, `status`, `provider` (más `method` / `route`
en las métricas HTTP y `collector` / `stream` en las de sampler).

**Prohibidos**: `execution_id`, `task_id`, `user_id`, `plan_id`, `request_id`,
`path`. Cada valor nuevo crea una serie temporal permanente; con identificadores
de entidad eso es cardinalidad ilimitada y tumba la TSDB de una máquina única.
Esa correlación es trabajo de **logs** (Loki, por `request_id`), no de métricas.
Lo hace cumplir `tests/unit/test_metrics_exporter.py`.

## api-server (`GET /metrics`)

| Métrica                                 | Tipo      | Labels                      | Qué mide                                     |
| --------------------------------------- | --------- | --------------------------- | -------------------------------------------- |
| `agentic_http_requests_total`           | counter   | `method`, `route`, `status` | Peticiones atendidas.                        |
| `agentic_http_request_duration_seconds` | histogram | `method`, `route`           | Latencia (buckets 5 ms – 10 s).              |
| `process_*`, `python_gc_*`              | varios    | —                           | RSS, fds, CPU, GC (de serie en la librería). |

`route` es la **plantilla** (`/items/{item_id}`), nunca el path crudo: sin eso,
cada 404 de un escáner (`/wp-admin`, `/.env`, …) sería una serie nueva y
permanente. Todo lo no enrutado colapsa en `__unmatched__`.

Sin auth: solo alcanzable desde `agentic-net`, sin puerto publicado, y no expone
datos de tenant. No colisiona con el `/inbox/metrics` autenticado (otro path).

## Workers (textfile-collector, cada 30 s)

| Métrica                                      | Tipo    | Labels            | Qué mide                                          |
| -------------------------------------------- | ------- | ----------------- | ------------------------------------------------- |
| `agentic_celery_queue_depth`                 | gauge   | `queue`           | Mensajes esperando (Redis `LLEN`).                |
| `agentic_celery_tasks_total`                 | counter | `queue`, `status` | Tareas **terminadas**, por resultado.             |
| `agentic_celery_task_duration_seconds_total` | counter | `queue`           | Segundos de ejecución acumulados.                 |
| `agentic_dlq_depth`                          | gauge   | `stream`          | Entradas dead-lettered (Redis `XLEN`).            |
| `agentic_tasks_by_status`                    | gauge   | `status`          | Tareas por estado del ciclo de vida.              |
| `agentic_executions_24h`                     | gauge   | `status`          | Ejecuciones por estado, ventana de 24 h.          |
| `agentic_human_approvals_pending`            | gauge   | —                 | Aprobaciones humanas esperando respuesta.         |
| `agentic_human_approvals_oldest_age_seconds` | gauge   | —                 | Edad de la más antigua sin responder.             |
| `agentic_sampler_last_run_timestamp_seconds` | gauge   | —                 | Heartbeat del sampler.                            |
| `agentic_sampler_collector_up`               | gauge   | `collector`       | 1/0 por colector en la última pasada.             |
| `agentic_llm_tokens_24h`                     | gauge   | `provider`        | Tokens FUERA de runs (asistente/córtex/planning). |
| `agentic_llm_cost_usd_24h`                   | gauge   | `provider`        | Gasto USD fuera de runs.                          |
| `agentic_run_tokens_24h`                     | gauge   | —                 | Tokens del pipeline de runs.                      |
| `agentic_run_cost_usd_24h`                   | gauge   | —                 | Gasto USD del pipeline de runs.                   |
| `agentic_backup_*`                           | varios  | —                 | Última copia con éxito, tamaño, copia offsite.    |

### Por qué hay dos métricas de cola y no una

`agentic_celery_queue_depth` cuenta lo que **espera**;
`agentic_celery_tasks_total` cuenta lo que **terminó y cómo**. Hacen falta las
dos: un worker sano y un worker que drena la cola fallando el 100% de lo que
saca presentan **la misma profundidad de cola** (cero). Los contadores de
resultado los acumulan las señales de Celery en Redis
(`workers/task_metrics.py`), porque el pool prefork los reparte entre N procesos.

Son **counters monotónicos que viven en Redis**, no gauges del último intervalo:
un scrape perdido no pierde información y `rate()` funciona. Sobreviven al
reinicio del worker; se reinician si se limpia el Redis del broker, y Prometheus
maneja el reset de un counter.

### Por qué el gasto LLM son dos familias y no una

Las fuentes **no son intercambiables**. `llm_usage_events` tiene
`provider_kind`, pero solo cubre asistente, córtex y planning. El gasto del
pipeline de runs vive en `executions.total_cost_usd`, tabla que **no guarda con
qué proveedor corrió** (el modelo se resuelve por agente en tiempo de ejecución).

Fundirlas bajo un único `{provider}` repartiría el gasto de los runs entre
proveedores inventados; publicar solo la primera presentaría como «coste LLM»
una fracción del real — y la mayor parte del gasto está en los runs. Por eso los
paneles del dashboard las muestran separadas y lo dicen en su descripción.

La exactitud de esta contabilidad es alcance de **prod-07**; aquí solo se expone
lo que la BD ya contiene.

### Por qué la edad de la aprobación y no solo el contador

Cuando un agente pide aprobación humana, su ejecución **se detiene**. Una
petición olvidada no genera error, ni log de fallo, ni cola creciendo. Y tres
aprobaciones recién pedidas son el funcionamiento normal, mientras que una de
hace tres días es trabajo parado: alertar sobre el contador dispararía con el
sistema sano y acabaría silenciada.

## api-server (sondas de plataforma)

| Métrica                           | Tipo  | Qué mide                                   |
| --------------------------------- | ----- | ------------------------------------------ |
| `agentic_vault_sealed`            | gauge | 1 si Vault está sellado o sin inicializar. |
| `agentic_vault_token_ttl_seconds` | gauge | TTL restante del token de servicio.        |

Las publica el api-server desde su sonda de `/v1/sys/seal-status`, no el exporter
de Vault: las métricas de un Vault sellado son justo las que no se pueden leer.

## Cero configuración muerta

Criterio de cierre del plan prod-08: **ninguna regla ni panel puede referenciar
una métrica que nadie emite**. Una regla sobre una métrica inexistente jamás
dispara, y eso es indistinguible de «todo va bien» — el defecto original que este
trabajo corrige.

Dos guardas lo sostienen, y ambas descubren los emisores leyendo el código (una
lista escrita a mano envejecería en la dirección cómoda, que es pasar):

- `tests/unit/test_prometheus_rules_and_scrape.py::test_no_rule_references_a_metric_nobody_emits`
- `tests/unit/test_grafana_dashboards.py::test_no_panel_queries_a_metric_nobody_emits`

Añadir una alerta obliga, por tanto, a añadir su emisor.
