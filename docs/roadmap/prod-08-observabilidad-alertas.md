---
plan_id: prod-08-observabilidad-alertas
title: Observabilidad de aplicación y cadena de alertas funcional
status: pending_approval
blocking_plan: [prod-01-despliegue-ejecutable]
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 18
estimated_cost_human_eur: 8.100 € – 10.800 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-08 — Observabilidad de aplicación y cadena de alertas funcional

## Cabecera

| Campo                              | Valor                            |
| ---------------------------------- | -------------------------------- |
| **ID del Plan**                    | `prod-08-observabilidad-alertas` |
| **Prioridad**                      | P1                               |
| **Bloqueado por**                  | `prod-01-despliegue-ejecutable`  |
| **Tiempo estimado (calendario)**   | 3-4 semanas                      |
| **Tiempo estimado (persona-días)** | 18                               |
| **Rama git sugerida**              | `plan/prod-08-observabilidad`    |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que la cadena de alertas está **rota en
silencio**: Alertmanager entrega TODAS las alertas (incluida "último backup fallido") a
`http://api-server:8000/internal/alerts/ingest`, un endpoint que no existe
(`alertmanager.yml:56`; el único router interno es `/internal/agent`). Prometheus solo scrapea
infraestructura: ni api-server ni workers exponen `/metrics`, y no hay métricas de negocio
(ejecuciones, tokens, costes, colas, aprobaciones) ni alertas de servicio caído o cola creciendo.
Los workers y el notification-dispatcher nunca invocan `configure_logging()`: sus logs salen sin
JSON y **sin el enmascarado PII** que el resto del stack sí aplica. Loki está declarado en
CLAUDE.md pero no existe en ningún compose; OTEL genera spans que se descartan (solo
ConsoleSpanExporter opt-in) y la traza muere en la frontera Celery; el watchdog no está desplegado
y su "alerta" final es una línea de log local; y el healthcheck del egress-proxy termina en
`|| true`, así que un tinyproxy muerto aparece healthy y los agentes pierden la salida a LLMs sin
que el stack delate la causa.

Este plan deja la plataforma **observable y que avise** cuando algo va mal: (1) ingestión real de
alertas + receptor de respaldo email/Slack; (2) `/metrics` con métricas de negocio, reglas de
alerta de servicio y dashboards Grafana de aplicación; (3) logging JSON + PII-masking uniforme en
todos los servicios con `request_id` propagado por Celery; (4) decisión explícita (ADR) sobre
Loki y OTEL — desplegar de verdad o recortar lo declarado, nada de configuración muerta; (5)
watchdog desplegado con alerta real y healthcheck del egress-proxy que puede fallar.

## Alcance

**Entra**:

- Endpoint `POST /internal/alerts/ingest` en api-server con fan-out al canal de System Admin, y
  receiver de respaldo email/Slack en `alertmanager.yml` (canónico + generado por el instalador).
- `prometheus_client` en api-server y workers; jobs de scrape; reglas `up==0`, cola creciendo,
  DLQ no vacía, tasa de fallos; conexión del `PoolMetrics` huérfano de `runtime_pool.py`.
- Dashboard Grafana de aplicación (ejecuciones/h, coste LLM, colas, 5xx, aprobaciones pendientes).
- Paquete `packages/shared-logging` extraído de `api_server.logging`, `configure_logging()` en
  workers y notification-dispatcher, propagación de `request_id` por Celery.
- ADR Loki (desplegar vs retirar) y ADR OTEL (OTLP vs recorte) + implementación de la opción
  aprobada y limpieza de dependencias muertas.
- Watchdog desplegado con alerta real (o retirado, según decisión humana); fix del `|| true` del
  healthcheck del egress-proxy.
- Runbook de observabilidad en `docs/06-runbooks/`.

**Queda fuera**:

- Exactitud de la contabilidad de tokens/costes — **prod-07** (aquí solo se exponen contadores).
- Arreglar el backup que dispara `BackupTooOld` — **prod-04** (aquí se garantiza que la alerta
  LLEGUE a un humano).
- Dockerfiles y compose de apps — **prod-01** (bloqueante: sin contenedores de apps no hay
  targets de scrape estables).
- Alta disponibilidad de Prometheus/Alertmanager (stack de una sola máquina).
- Tracing distribuido completo (Tempo/Jaeger) si el ADR de OTEL opta por el recorte.

## Decisiones clave

- **Ingestión vía plataforma + respaldo directo**: se mantiene el diseño original (Alertmanager →
  api-server → notificación Plan 10) y se añade un receiver email/Slack directo para
  `severity=critical`: si la alerta es "api-server caído", el api-server no puede entregársela a
  sí mismo.
- **`prometheus_client` + `make_asgi_app`** (no `prometheus-fastapi-instrumentator`): control de
  métricas y labels con una dependencia menos. En workers, exporter HTTP por proceso vía
  `worker_process_init`, sin celery-exporter externo.
- **Cardinalidad acotada**: labels permitidos `tenant_id`, `queue`, `status`, `provider`.
  PROHIBIDO `execution_id`/`task_id`/`user_id` como label — eso es trabajo de logs, no de métricas.
- **ADR propuesto — Loki** (decisión de producto, NO se toma aquí). Opción A (recomendada):
  Loki + Grafana Alloy en el overlay de monitoring, retención 30 días, datasource provisionado —
  buscar por `execution_id`/`tenant_id` es el caso de uso central de los logs JSON que ya
  emitimos. Opción B: retirar Loki del stack declarado en CLAUDE.md y documentar la retención
  real json-file (~50 MB/contenedor) como limitación.
- **ADR propuesto — OTEL** (decisión de producto, NO se toma aquí). Opción A (recomendada):
  recorte explícito — retirar `opentelemetry-instrumentation-sqlalchemy` (declarado y nunca
  invocado), corregir el docstring engañoso de `telemetry/setup.py`, declarar el tracing fuera de
  alcance v1; la correlación se cubre con `request_id` (Fase C). Opción B: exporter OTLP + Tempo +
  instrumentación Celery (≈ +5 persona-días, no presupuestados).
- **Watchdog: desplegar con alerta real** (orientación de auditoría): servicio en compose con
  socket Docker montado, justificación de seguridad documentada frente al principio rector 2, y
  `watchdog.alert` → POST a `/internal/alerts/ingest`. Alternativa registrada en el ADR:
  eliminarlo y confiar en `restart: unless-stopped` + alerta `up==0`.

## Tareas

### Fase A — Cadena de alertas funcional (observability-1)

#### `task_prod08_alert_ingest_01` — Endpoint `POST /internal/alerts/ingest`

- [x] **Título**: Implementar la ingestión de alertas de Alertmanager en el api-server con
      fan-out al canal de System Admin
- **Descripción**: nuevo router `apps/api-server/src/api_server/routers/internal_alerts.py`
  (prefijo `/internal/alerts`) que parsea el payload webhook v4 de Alertmanager
  (`alerts[].labels/annotations/status`), lo convierte en notificación Plan 10 y la encola en el
  notification-dispatcher. Registrarlo en `main.py` junto a `internal_agent.py:51`. Protección:
  solo red `agentic-net` + token compartido en cabecera (mismo patrón que `/internal/agent`).
  Deduplicar por `fingerprint` para no duplicar avisos en cada `repeat_interval`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_internal_alerts_ingest.py -v"
  ```

#### `task_prod08_alert_fallback_02` — Receiver de respaldo email/Slack

- [ ] **Título**: Receiver directo email/Slack para `severity=critical` en `alertmanager.yml`
- **Descripción**: en `docker/monitoring/alertmanager/alertmanager.yml` y en la plantilla del
  instalador (`compose_generator.py:608`), añadir un segundo receiver (`email_configs` o
  `slack_configs`) en la ruta `severity=critical`, con credenciales SMTP/webhook desde el `.env`
  del instalador (custodia coordinada con prod-10). Documentar que cubre el caso "api-server
  caído".
- **Tiempo**: 0,5 días · **Complejidad**: s · **Dependencias**: ninguna (paralelizable con 01)
- ⏳ **Pendiente (2026-08-01)**: la CONFIG está hecha y guardada por
  `tests/unit/test_alertmanager_routing.py` — `alertmanager.yml` tiene el receiver
  `critical-fallback` y el `continue: true` sin el cual el árbol de rutas se
  detiene en la primera coincidencia y el respaldo habría SUSTITUIDO a la
  notificación por plataforma en vez de duplicarla. El instalador monta ese mismo
  fichero (`compose_generator.py:_alertmanager_service`), así que no hay plantilla
  que duplicar. **Lo que falta es la CREDENCIAL**: el receiver lee el webhook de
  Slack de `/etc/alertmanager/secrets/slack_api_url` y el instalador no provisiona
  ni monta ese fichero, así que en un despliegue generado el canal de respaldo
  falla en cada envío (degradación deliberada: Alertmanager arranca igual). Cierra
  con el aporte de prod-10 (custodia) + un volumen en `_alertmanager_service`.
  Además, su docstring sigue diciendo «no SMTP/Slack secrets here», ya falso.
  - **Re-verificado el 2026-08-01** (`alertmanager.yml:55/68/104-106` y
    `compose_generator.py:1215-1233`): sigue siendo exacto. Lo que falta
    **no es código**: es decidir dónde se custodia el webhook de Slack y quién
    lo provisiona — una decisión humana (presupuesto/claves), que es
    precisamente lo que prod-10 resuelve. La parte que sí es código
    (`apps/installer/**`) queda fuera del carril `observabilidad`.
- ⏳ **2026-08-02 — sigue GATED EN UN HUMANO, y ya no hace falta volver a
  investigarlo.** Lo confirmado hoy no cambia: la config está y la credencial no.
  Lo que sí cambia es que **el procedimiento completo está escrito** en
  [`docs/06-runbooks/observabilidad.md` §«Lo que falta para que el Slack de
  respaldo funcione»](../06-runbooks/observabilidad.md): los cinco pasos en orden
  (decidir custodia → crear el webhook en Slack → provisionarlo como fichero en
  `/etc/alertmanager/secrets/slack_api_url` → montar el volumen en
  `_alertmanager_service` → probar con `amtool`), qué está ya hecho y no hay que
  rehacer, y por qué Alertmanager arranca igual aunque falle en cada envío.
  También se corrigió el runbook donde afirmaba que la alerta llega «además al
  Slack de respaldo»: hoy no llega, y decirlo era la peor clase de documentación
  — la que se lee y se cree.
  **Lo que necesita el operador es una credencial y una decisión de custodia**, no
  código. Marcar esta casilla sería declarar operativa una vía de aviso que falla
  en cada envío, y precisamente en el escenario para el que existe (api-server
  caído).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_02_a
    runtime: python-pytest
    command: "pytest apps/installer/backend/tests/test_compose_generator.py -k alertmanager -v"
  ```

#### `task_prod08_alert_e2e_03` — Test e2e de alerta sintética

- [x] **Título**: Alerta sintética → notificación visible para el System Admin
- **Descripción**: test de integración que hace POST de un payload webhook v4 real (fixture) a
  `/internal/alerts/ingest` y verifica que se crea la notificación en BD y que el dispatcher la
  encola. Smoke manual vía `amtool alert add` documentado en el runbook (Fase F).
- **Tiempo**: 0,5 días · **Complejidad**: s · **Dependencias**: task_prod08_alert_ingest_01
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_03_a
    runtime: python-pytest
    command: "pytest tests/integration/test_alert_chain_e2e.py -v"
  ```

### Fase B — Métricas de aplicación y alertas de servicio (observability-2)

#### `task_prod08_metrics_api_04` — `/metrics` en api-server

- [x] **Título**: Exporter Prometheus en api-server con métricas HTTP y de negocio
- **Descripción**: añadir `prometheus_client` a `apps/api-server/pyproject.toml` y montar
  `make_asgi_app()` en `/metrics` (sin auth, alcanzable solo desde `agentic-net`; NO colisiona
  con el `/metrics` JSON autenticado de `human_inbox.py:255`, que es otro path). Métricas:
  histograma de latencia y contador 5xx por router; negocio: `executions_total{status}`,
  `llm_tokens_total{provider}`, `llm_cost_eur_total{provider}` (contadores alimentados por la
  contabilidad de prod-07) y gauge `human_approvals_pending`.
- **Tiempo**: 2 días · **Complejidad**: m
- ✅ **Cerrada (2026-08-01)** con una divergencia: las métricas de NEGOCIO no se
  declaran en el api-server. Un contador in-process de ejecuciones/tokens/coste
  allí valdría siempre 0 (eso pasa en los workers) y sería la «configuración
  muerta» que prohíbe el criterio de cierre 4. Se emiten desde el sampler de
  workers, que consulta la BD y ve el sistema entero: `agentic_executions_24h`,
  `agentic_human_approvals_pending` + `_oldest_age_seconds`,
  `agentic_llm_tokens_24h{provider}` / `agentic_llm_cost_usd_24h{provider}` y
  `agentic_run_tokens_24h` / `agentic_run_cost_usd_24h`. Coste en DOS familias a
  propósito: `llm_usage_events` tiene proveedor pero solo cubre
  asistente/córtex/planning, y `executions.total_cost_usd` (la mayor parte del
  gasto) no tiene columna de proveedor — fundirlas repartiría el gasto de los runs
  entre proveedores inventados. El api-server expone lo suyo: HTTP + proceso/GC.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_metrics_endpoint.py -v"
  ```

#### `task_prod08_metrics_workers_05` — `/metrics` en workers + PoolMetrics

- [x] **Título**: Exporter Prometheus en workers conectado a PoolMetrics y señales Celery
- **Descripción**: añadir `prometheus_client` a `apps/workers/pyproject.toml`; en
  `apps/workers/src/workers/celery_app.py` arrancar `start_http_server(9540)` en
  `worker_process_init`. Conectar el `PoolMetrics` huérfano de `runtime_pool.py:125` (su
  docstring promete un exporter que nunca existió). Señales `task_prerun/postrun/failure` →
  `celery_tasks_total{queue,status}` y duración por cola; gauge `celery_queue_depth{queue}` y
  tamaño de DLQ muestreados con un beat ligero contra Redis.
- **Tiempo**: 2 días · **Complejidad**: m
- ✅ **Cerrada (2026-08-01)** según el **ADR 0141**, que ya había descartado las
  dos premisas de esta tarea: `runtime_pool.py` (y con él `PoolMetrics`) se borró
  en `7959cdcb`, y `start_http_server(9540)` en `worker_process_init` no funciona
  con el pool prefork (se dispara en cada hijo). Pero el ADR resolvió el
  TRANSPORTE y dejó la métrica sin hacer: **nadie contaba los resultados de las
  tareas**. Añadido `workers/task_metrics.py` — las señales `task_prerun` /
  `postrun` / `failure` acumulan en Redis (compartido por los N hijos) y el
  sampler publica `agentic_celery_tasks_total{queue,status}` y
  `agentic_celery_task_duration_seconds_total{queue}`. Sin esto, un worker sano y
  uno que drena la cola fallando el 100% presentan la misma profundidad: cero.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_05_a
    runtime: python-pytest
    command: "pytest apps/workers/tests/test_metrics_exporter.py -v"
  ```

#### `task_prod08_scrape_rules_06` — Scrape configs + reglas de alerta de servicio

- [x] **Título**: Jobs de scrape de apps y reglas `up==0`, cola creciendo y DLQ no vacía
- **Descripción**: en `docker/monitoring/prometheus/prometheus.yml:35` añadir jobs `api-server`
  (`api-server:8000/metrics`) y `workers` (`workers:9540`), más el resto de apps cuando prod-01
  las declare. Nuevo `docker/monitoring/prometheus/rules/app_alerts.yml`: `ServiceDown` (`up==0`
  2m, critical), `CeleryQueueGrowing` (crecimiento sostenido 15m), `DLQNotEmpty`,
  `ExecutionFailureRateHigh` (>20% en 30m), `HumanApprovalsStale`. Replicar jobs y reglas en la
  config que emite el instalador (`compose_generator.py`).
- **Tiempo**: 1 día · **Complejidad**: m · **Dependencias**: tasks 04 y 05
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_06_a
    runtime: python-pytest
    command: "pytest tests/integration/test_prometheus_rules_lint.py -v  # promtool check rules vía subprocess"
  ```

#### `task_prod08_dashboards_07` — Dashboards Grafana de aplicación

- [x] **Título**: Dashboard "Plataforma" junto al host-overview existente
- **Descripción**: añadir `docker/monitoring/grafana/dashboards/platform-overview.json`
  (provisionado por el `dashboards.yml` ya existente): ejecuciones por estado/h, tokens y coste
  LLM por proveedor, profundidad de colas + DLQ, tasa 5xx y latencia p95 del api-server,
  aprobaciones humanas pendientes. JSON versionado en el repo, sin ediciones manuales en Grafana.
- **Tiempo**: 1,5 días · **Complejidad**: m · **Dependencias**: task_prod08_scrape_rules_06
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_grafana_dashboards_valid_json.py -v"
  ```

### Fase C — Logging uniforme y correlación (observability-3, observability-7)

#### `task_prod08_shared_logging_08` — Paquete `packages/shared-logging`

- [x] **Título**: Extraer `api_server.logging` (setup, pii, context) a un paquete compartido
- **Descripción**: mover `apps/api-server/src/api_server/logging/{setup,pii,context}.py` a
  `packages/shared-logging/` — hoy orchestrator (`__main__.py:23`) y watchdog (`__main__.py:65`)
  importan `api_server.logging` cruzando apps, un anti-patrón. Mantener shims de re-export en
  api-server. Ampliar el catálogo PII con prefijos de API keys (`sk-`, `ghu_`, `hvs.`).
- **Tiempo**: 1,5 días · **Complejidad**: m
- ✅ **Cerrada (2026-08-01)** con la decisión del **ADR 0141** (`accepted`): NO se
  extrae `packages/shared-logging`. Los tres consumidores construyen sobre la
  imagen del api-server (`ARG BASE_IMAGE`) y `workers` ya importa `api_server` en
  ~50 sitios más, así que mover tres ficheros no elimina el acoplamiento: lo
  disfraza. El valor se entregó vía `api_server/logging/celery_pipeline.py`. Lo
  que SÍ quedaba de esta tarea era el catálogo PII, y está hecho: claves de API
  por prefijo (`sk-`, `gh[pousr]_`, `hvs.`) enmascaradas conservando el prefijo
  — saber si la que se filtró era de Vault o de un proveedor LLM es medio
  diagnóstico. Con cuerpo mínimo para no comerse `sk-1` de un nombre de rama.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_08_a
    runtime: python-pytest
    command: "pytest packages/shared-logging/tests/ -v"
  ```

#### `task_prod08_celery_logging_09` — `configure_logging()` en los dos servicios Celery

- [x] **Título**: Pipeline JSON + PII-masking en workers y notification-dispatcher
- **Descripción**: en `apps/workers/src/workers/celery_app.py:45` y
  `apps/notification-dispatcher/src/notification_dispatcher/celery_app.py`, conectar la señal
  `celery.signals.setup_logging` → `configure_logging(service=...)` del paquete compartido. Test
  que emite desde un task un log con JWT y email y verifica que salen enmascarados, en JSON y con
  campo `service`.
- **Tiempo**: 1 día · **Complejidad**: s · **Dependencias**: task_prod08_shared_logging_08
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_09_a
    runtime: python-pytest
    command: "pytest apps/workers/tests/test_logging_pipeline.py apps/notification-dispatcher/tests/test_logging_pipeline.py -v"
  ```

#### `task_prod08_request_id_10` — `request_id` a través de la frontera Celery

- [x] **Título**: Propagar `request_id` en los headers de cada tarea Celery y bindearlo en
      `task_prerun`
- **Descripción**: al encolar desde el api-server (puntos de `apply_async` del dispatch,
  `db/domain.py:1007`), incluir el `request_id` del contextvar (`logging/context.py:92`) como
  header Celery; en workers, señal `task_prerun` que haga `bind_request_context(request_id)` para
  que `workers.execution_started` (`execution.py:489`) y todos los logs del task compartan
  correlation id con la petición HTTP de origen. Dejar seam para `traceparent` W3C si el ADR-OTEL
  opta por la opción B.
- **Tiempo**: 0,5 días · **Complejidad**: s · **Dependencias**: task_prod08_celery_logging_09
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_request_id_propagation.py -v"
  ```

### Fase D — Decisiones de stack: Loki y OTEL (observability-4, observability-5)

#### `task_prod08_adr_loki_otel_11` — ADRs: Loki (desplegar/retirar) y OTEL (OTLP/recorte)

- [x] **Título**: Redactar dos ADR en `docs/05-architecture-decisions/` con opciones, coste y
      recomendación; aprobación humana requerida
- **Descripción**: ADR-Loki y ADR-OTEL según las opciones descritas en «Decisiones clave».
  Ambos quedan `proposed` hasta decisión humana; este plan presupuesta las opciones recomendadas
  (A-Loki desplegado, A-OTEL recorte). Incluir en el ADR del watchdog (o sección del mismo doc)
  la justificación del socket Docker frente al principio rector 2.
- **Tiempo**: 1 día · **Complejidad**: s
- **Tests automáticos**: no aplica (documento); revisión humana en el cierre.

#### `task_prod08_loki_deploy_12` — Implementar la opción aprobada del ADR-Loki

- [x] **Título**: Desplegar Loki + Alloy en el overlay de monitoring (opción A) o retirar Loki
      del stack declarado (opción B)
- **Descripción**: si A: servicios `loki` y `alloy` en `docker/docker-compose.monitoring.yml`
  (Alloy leyendo `/var/lib/docker/containers`), retención 30 días, datasource Loki en
  `docker/monitoring/grafana/provisioning/datasources/`, labels por contenedor y búsqueda por
  `execution_id`/`tenant_id`/`request_id` sobre los logs JSON; replicar en el generador del
  instalador. Si B: la edición de CLAUDE.md/docs se ejecuta vía prod-15-gobernanza. Estimación
  presupuestada para la opción A.
- **Tiempo**: 2 días · **Complejidad**: l · **Dependencias**: tasks 11 (ADR aprobado) y 09 (los
- ✅ **Cerrada (2026-08-01)**: la premisa del plan («Loki no existe en ningún
  compose») era FALSA — ver el aviso de recon del ADR 0139. Loki y **Promtail**
  (no Alloy) llevan desplegados y `healthy` desde `e3b2e243`, con datasource
  provisionado y retención de 168 h. Lo que faltaba era la guarda, y ese era el
  riesgo real: casi todas las formas de romper esto son mudas. Añadido
  `tests/unit/test_monitoring_compose_loki.py`, que cierra las cuatro —
  Promtail sin el bind de `/var/lib/docker/containers` (arranca sano y no envía
  nada), `retention_period` sin `retention_enabled` (parece que borra y no borra),
  `loki_data` sin volumen nombrado, y la URL del datasource separada del nombre
  del servicio. La retención definitiva sigue siendo decisión del operador (ADR
  0139 `proposed`).
  logs deben ser JSON para que la búsqueda sirva)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_monitoring_compose_loki.py -v  # compose config + query LogQL smoke"
  ```

#### `task_prod08_otel_cleanup_13` — Implementar la opción aprobada del ADR-OTEL

- [x] **Título**: Recorte explícito de OTEL (opción A): retirar dependencia muerta y docstring
      engañoso
- **Descripción**: eliminar `opentelemetry-instrumentation-sqlalchemy` de
  `apps/api-server/pyproject.toml:73` (`SQLAlchemyInstrumentor` jamás se invoca), reescribir el
  docstring de `telemetry/setup.py` para reflejar que el único exporter es Console opt-in
  (`API_SERVER_OTEL_CONSOLE=1`, `main.py:99-105`) y que el tracing distribuido queda fuera de
  alcance v1. Si el humano aprueba la opción B, esta tarea se sustituye por un plan follow-up
  (+5 p-d, fuera de presupuesto).
- **Tiempo**: 0,5 días · **Complejidad**: s · **Dependencias**: task_prod08_adr_loki_otel_11
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_13_a
    runtime: python-pytest
    command: "pytest tests/unit/test_telemetry_setup.py -v"
  ```

### Fase E — Watchdog y healthchecks honestos (observability-6, deploy-9, deploy-10)

#### `task_prod08_watchdog_14` — Watchdog desplegado con alerta real

- [ ] **Título**: Declarar el watchdog como servicio de compose y enrutar `watchdog.alert` a la
      ingestión de alertas
- **Descripción**: hoy `apps/watchdog` no aparece en ningún `docker-compose.*.yml` ni en
  `_BUILDERS` del instalador (`compose_generator.py:704`) y al agotar reintentos su única salida
  es `_logger.error("watchdog.alert", ...)` (`service_monitor.py:83`). Cambios: (1) servicio
  `watchdog` en `docker/docker-compose.yml` y en `compose_generator.py` con el socket Docker
  montado, sin puertos publicados y `cap_drop: ALL`; (2) ampliar `_DEFAULT_SERVICES`
  (`__main__.py:21`) para incluir egress-proxy; (3) al agotar el backoff, POST a
  `/internal/alerts/ingest` (payload Alertmanager sintético) con fallback a log. Si el humano
  decide retirarlo, la tarea se convierte en eliminación del paquete + docs (mismo presupuesto).
- **Tiempo**: 1,5 días · **Complejidad**: m · **Dependencias**: task_prod08_alert_ingest_01
- ⏳ **Pendiente (2026-08-01)**: sin empezar y **verificado que sigue haciendo
  falta**. `grep -n watchdog docker/docker-compose*.yml compose_generator.py` da
  una sola coincidencia, y es un COMENTARIO. `apps/watchdog/` existe con su
  `pyproject.toml` y su `service_monitor.py`, no está en `_BUILDERS` del
  instalador, y al agotar reintentos su única salida sigue siendo
  `_logger.error("watchdog.alert", ...)` — una línea de log local. El destino al
  que debe POSTear (`/internal/alerts/ingest`) YA existe y funciona de punta a
  punta (ver task 03), así que esta tarea es solo el emisor. Fuera del carril
  `observabilidad`: toca `docker/docker-compose.yml`, `apps/watchdog/**` y
  `compose_generator.py`.
- ⏳ **2026-08-02 — (1) y (2) y (3) HECHOS; falta el generador del instalador.**
  El paquete ha dejado de ser código muerto:
  - **(3) el emisor**, que era el corazón de la tarea:
    `apps/watchdog/src/watchdog/alerting.py` — `AlertSink` construye el webhook v4
    **sintético de Alertmanager** (el endpoint lo valida con ese schema; un
    payload propio se habría comido un 422 y volveríamos al silencio), lo POSTea
    con `Authorization: Bearer` y **degrada a log** ante cualquier fallo. Se
    entrega **una sola vez por episodio** (el bucle tickea cada 30 s: sin esa
    guarda, un postgres muerto son 120 notificaciones/hora) pero **se reintenta
    mientras no confirme**, porque un api-server que arranca después del watchdog
    no debe costar la única notificación. `AttemptRecord.alert_delivered` separa
    «lo escribí en el log» de «llegó a un humano»: confundir esas dos cosas era el
    defecto original. Transporte por `urllib` de la stdlib e inyectable — el
    watchdog declara dos dependencias y no necesita una tercera para un POST de
    400 bytes.
  - **(2) la lista vigilada** incluye ya `egress-proxy` y `registry-proxy`… y
    **hacer eso destapó una trampa**: los dos declaran `container_name:` explícito
    en el compose, así que la convención `{proyecto}-{servicio}-1` con la que
    `_build_monitors` resolvía **no los encuentra**. Añadirlos sin más habría dado
    un watchdog que dice vigilarlos y no vigila ninguno — un `container_missing`
    en el arranque, silencio después, cobertura aparente. La resolución pasa a ser
    por **etiquetas de Compose** (`resolve_container`), con el nombre como
    fallback para stacks levantados a mano.
  - **(1) el servicio** está declarado en `docker/docker-compose.yml`, con dos
    desviaciones deliberadas respecto al enunciado:
    · **NO monta el socket Docker.** La tarea lo pedía; el principio rector 2 y
    `tests/security/test_pentest_findings.py::test_no_prod_service_mounts_docker_socket`
    lo prohíben, y con razón. Usa `DOCKER_HOST` contra el `docker-socket-proxy`
    del **ADR 0060**, que es el patrón que los workers ya seguían y al que le
    basta `CONTAINERS=1` + `POST=1`. El riesgo 4 del plan («socket Docker en
    watchdog vs principio rector 2») queda así **disuelto, no mitigado**.
    · Va bajo `profiles: [watchdog]`, porque este compose es la capa de
    infraestructura y no construye imágenes de aplicación: sin el perfil,
    `docker compose up` se comporta exactamente como antes.
    Con `Dockerfile` propio (`apps/watchdog/Dockerfile`, basado en la imagen de la
    api-server como el notification-dispatcher, para heredar el logging JSON con
    enmascarado de PII).
  - **Tests ejecutados, todos en verde**: `tests/unit/test_watchdog_alert_delivery.py`
    (11), `tests/unit/test_watchdog_container_resolution.py` (8),
    `tests/security/test_watchdog_service_declaration.py` (6), más los 24 que ya
    había (`test_service_monitor.py` + `test_backoff.py`) sin tocar. Rojo
    verificado por mutación en tres invariantes (dedup de entrega, umbral de
    `severity`, chequeo del código HTTP).
  - **Desviación de la ruta declarada, a propósito**: el plan nombra
    `apps/watchdog/tests/test_alert_delivery.py`, y ese directorio **no está en
    `testpaths` ni lo corre CI** — habría sido un test que nadie ejecuta. Los
    tests del watchdog ya vivían en `tests/unit/` (`test_service_monitor.py`,
    `test_backoff.py`) y ahí van los nuevos.
  - **Por qué NO se marca**: falta la mitad que de verdad llega a producción —
    declarar el servicio en `compose_generator.py` (con su `docker-socket-proxy`
    alcanzable y el `_BUILDERS` de la imagen). `apps/installer/**` es de otro
    carril. Lo que hace falta ahí está dictado: replicar el bloque `watchdog:` del
    compose canónico y añadir `watchdog` a `_BUILDERS`.
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_14_a
    runtime: python-pytest
    command: "pytest apps/watchdog/tests/test_alert_delivery.py -v"
  ```

#### `task_prod08_egress_health_15` — Healthcheck del egress-proxy que puede fallar

- [ ] **Título**: Eliminar el `|| true` del healthcheck de tinyproxy en compose canónico y
      generador
- **Descripción**: en `docker/docker-compose.yml:268-273` y `compose_generator.py:366-374`,
  sustituir el final `|| true` del test `wget ... | grep -q tinyproxy` por `|| exit 1`. El
  egress-proxy es la ÚNICA salida de los agent-runtimes hacia los LLMs (ADR 0019): su estado debe
  ser honesto para que `ServiceDown`/watchdog actúen. La verificación con `docker inspect`
  (tinyproxy parado → `unhealthy`) queda en el test humano.
- **Tiempo**: 0,25 días · **Complejidad**: s
- ⏳ **Pendiente (2026-08-01)**: hecho a MEDIAS. El compose canónico ya es honesto
  (`docker/docker-compose.yml:411` y `:445` terminan en `|| exit 1`, para
  egress-proxy y registry-proxy). El generador del instalador NO: sigue con
  `|| true` en `compose_generator.py:458` (egress-proxy) y `:482`
  (registry-proxy), así que **en producción un tinyproxy muerto sigue saliendo
  `healthy`** — justo el despliegue donde más duele. El cambio es literal: en esas
  dos líneas, `... | grep -q tinyproxy || true` → `... | grep -q tinyproxy ||
exit 1`. Fuera del carril `observabilidad` (`apps/installer/**`). Ojo: el test
  que cita el plan (`apps/installer/backend/tests/test_compose_generator.py`) no
  existe; el real es `tests/unit/test_compose_generator.py` y hoy no cubre el
  healthcheck de los proxies.
- ⏳ **Re-verificado el 2026-08-02: idéntico, y la mitad que falta sigue fuera de
  este carril.** El compose canónico es honesto (`docker/docker-compose.yml`,
  egress-proxy y registry-proxy terminan en `|| exit 1`, con
  `tests/unit/test_compose_healthchecks_honest.py` guardándolo); el generador del
  instalador sigue con `|| true`. Cambio de contexto que aumenta lo que cuesta
  dejarlo así: desde hoy el **watchdog vigila los dos proxies**
  (`task_prod08_watchdog_14`), y el watchdog decide por el estado de salud que
  reporta Docker. Con `|| true` en el despliegue generado, un tinyproxy muerto
  sale `healthy`, **el watchdog no lo reinicia y nadie recibe la alerta**: la
  recuperación automática que se acaba de entregar queda desactivada justo en el
  entorno donde importa. El cambio son dos caracteres en dos líneas
  (`compose_generator.py:458` y `:482`).
- **Tests automáticos**:
  ```yaml
  - id: auto_prod08_15_a
    runtime: python-pytest
    command: "pytest apps/installer/backend/tests/test_compose_generator.py -k egress_healthcheck -v"
  ```

### Fase F — Documentación y runbook

#### `task_prod08_runbook_16` — Runbook de observabilidad

- [x] **Título**: `docs/06-runbooks/observabilidad.md` + referencia de métricas en
      `docs/04-reference/`
- **Descripción**: catálogo de alertas (qué significa cada una y qué hacer), cómo buscar logs por
  `execution_id`/`tenant_id`/`request_id` (Loki o json-file según ADR), cómo probar la cadena con
  una alerta sintética (`amtool`), y cómo añadir una métrica nueva sin romper la cardinalidad.
  Actualizar `docs/04-reference/` con la lista de métricas expuestas y sus labels.
- **Tiempo**: 1 día · **Complejidad**: s · **Dependencias**: Fases A-E completadas
- **Tests automáticos**: no aplica (documentación); revisión humana.

## Hallazgos de auditoría cubiertos

| fid             | Severidad | Tarea(s) que lo cierran                                                                                            |
| --------------- | --------- | ------------------------------------------------------------------------------------------------------------------ |
| observability-1 | high      | task_prod08_alert_ingest_01, task_prod08_alert_fallback_02, task_prod08_alert_e2e_03                               |
| observability-2 | high      | task_prod08_metrics_api_04, task_prod08_metrics_workers_05, task_prod08_scrape_rules_06, task_prod08_dashboards_07 |
| observability-3 | high      | task_prod08_shared_logging_08, task_prod08_celery_logging_09                                                       |
| observability-4 | medium    | task_prod08_adr_loki_otel_11, task_prod08_otel_cleanup_13, task_prod08_request_id_10 (correlación alternativa)     |
| observability-5 | medium    | task_prod08_adr_loki_otel_11, task_prod08_loki_deploy_12                                                           |
| observability-6 | medium    | task_prod08_watchdog_14                                                                                            |
| observability-7 | low       | task_prod08_request_id_10                                                                                          |
| deploy-9        | medium    | task_prod08_egress_health_15                                                                                       |
| deploy-10       | medium    | task_prod08_watchdog_14 (mismo hallazgo que observability-6, dimensión deploy)                                     |

**Coordinación con la serie**: la alerta `BackupTooOld` que este plan hace llegar a humanos
depende de que prod-04 arregle el backup que la dispara; los contadores de tokens/coste de la
task 04 consumen la contabilidad que prod-07 hace exacta; las credenciales SMTP/Slack de la task
02 se custodian según prod-10; los cambios en `compose_generator.py` (tasks 02, 06, 12, 14, 15)
se rebasan sobre el compose de apps que entrega prod-01; y si el ADR-Loki opta por retirar Loki,
la edición de CLAUDE.md se ejecuta vía prod-15-gobernanza.

## Riesgos

1. **Dependencia fuerte de prod-01**: sin imágenes ni servicios de apps en compose, los jobs de
   scrape y el e2e de alertas solo son verificables en dev. Mitigación: plan bloqueado por
   prod-01; los tests de integración corren contra el stack dev mientras tanto.
2. **Explosión de cardinalidad en Prometheus**: labels con `execution_id`/`user_id` tumbarían la
   TSDB en una máquina única. Mitigación: catálogo de labels cerrado + revisión en code review.
3. **Consumo de Loki en máquina única**: Loki + Alloy añaden RAM/disco a un host que ya corre
   todo el stack. Mitigación: retención 30 días, límites de recursos en compose, opción B del ADR
   como salida.
4. **Socket Docker en watchdog vs principio rector 2**: el endpoint de restart requiere acceso de
   escritura al socket. Mitigación: ADR explícito, sin puertos publicados, `cap_drop: ALL`, y la
   alternativa de retirarlo si el humano lo prefiere.
5. **Doble vía de notificación**: Alertmanager→plataforma y Alertmanager→email pueden duplicar
   avisos críticos. Mitigación: el receiver directo solo cubre `severity=critical`, documentado
   como redundancia deliberada.
6. **Endpoint interno abusable**: `/internal/alerts/ingest` en `agentic-net` podría usarse para
   spamear notificaciones desde un contenedor comprometido. Mitigación: token compartido,
   rate-limit y deduplicación por fingerprint.

## Tests humanos del Plan

```yaml
- id: human_prod08_01
  description: "La cadena de alertas entrega de verdad a un humano"
  hint: "Parar un worker a mano y esperar la alerta ServiceDown"
  checklist:
    - "docker stop del contenedor workers → en <5 min llega notificación al canal System Admin"
    - "La misma alerta crítica llega también por el receiver de respaldo (email/Slack)"
    - "amtool alert add de una alerta sintética → aparece como notificación en la UI"
    - "Arrancar el worker de nuevo → llega el aviso resolved"

- id: human_prod08_02
  description: "Métricas y dashboards de aplicación operativos"
  hint: "Lanzar un plan con 2-3 tareas y observar Grafana"
  checklist:
    - "curl http://api-server:8000/metrics devuelve formato Prometheus con executions_total"
    - "El dashboard 'Plataforma' muestra ejecuciones/h, coste LLM y profundidad de colas moviéndose"
    - "Encolar tareas sin workers arrancados → CeleryQueueGrowing pasa a pending/firing"

- id: human_prod08_03
  description: "Logs JSON con PII enmascarado en TODOS los servicios"
  hint: "docker logs de workers y notification-dispatcher durante una ejecución"
  checklist:
    - "Los logs de workers salen en JSON con campos service y request_id"
    - "Un JWT o email logueado a propósito sale enmascarado en los logs del worker"
    - "El request_id de la petición HTTP que disparó el plan aparece en los logs del worker"
    - "Si el ADR-Loki aprobó la opción A: buscar por execution_id en Grafana devuelve logs de api-server Y workers"

- id: human_prod08_04
  description: "Healthchecks honestos y watchdog operativo"
  hint: "Matar tinyproxy dentro del contenedor egress-proxy"
  checklist:
    - "docker inspect del egress-proxy con tinyproxy muerto → status unhealthy (antes era healthy siempre)"
    - "El watchdog aparece en docker compose ps y reinicia un postgres parado a mano"
    - "Forzar agotamiento de reintentos del watchdog → llega notificación al System Admin (no solo una línea de log)"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. Los dos ADR (Loki, OTEL) y la decisión del watchdog aprobados por un humano; las tareas
   condicionadas (12, 13, 14) implementan la opción aprobada, no la asumida.
3. Los 4 tests humanos del plan validados.
4. Ningún `/metrics`, regla o dashboard referencia métricas que no se emiten (cero configuración
   muerta nueva — el defecto que este plan corrige).
5. Entrada de changelog en `docs/07-changelog/prod-08-observabilidad-alertas.md`.
6. PR del plan mergeado a `master`.

## Próximo Plan

**prod-09-sesiones-autorizacion-frontend** [P1] — Sesiones y autorización de producción: admin
hardening, SSO, 401 global y cookies. Con la plataforma ya observable y avisando, el siguiente
paso de la serie endurece la superficie de autenticación/autorización que estos mismos dashboards
y alertas ayudarán a vigilar.
