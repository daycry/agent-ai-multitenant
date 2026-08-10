---
title: Runbook — Observabilidad: qué avisa, dónde mirar y cómo probarlo
audience: system-admin
updated: 2026-08-02
docs_language: es
---

# Observabilidad de la plataforma

Este runbook cubre las tres preguntas que se hacen a las 3 de la mañana: **qué
significa la alerta que acaba de llegar**, **dónde miro los logs de eso**, y
**cómo compruebo que la cadena de avisos sigue viva** antes de necesitarla.

El catálogo completo de métricas y sus labels está en
[`docs/04-reference/metricas.md`](../04-reference/metricas.md).

## El mapa en una frase

```
api-server ──/metrics──┐
                       ├─► Prometheus ──reglas──► Alertmanager ──┬─► api-server /internal/alerts/ingest ──► notificación al System Admin
workers ──.prom──►     │                                          └─► Slack de respaldo (solo severity=critical)
node-exporter ─────────┘
contenedores ──json-log──► Promtail ──► Loki ──► Grafana
```

Dos detalles que explican casi todas las sorpresas:

- **Los workers no tienen serie `up`.** No se scrapean por HTTP: publican por el
  textfile-collector de node-exporter (ADR 0141, el pool prefork hace inviable un
  exporter por proceso). Que el worker esté vivo lo dice `MetricsSamplerStale`,
  no `ServiceDown`.
- **Si cae node-exporter se van con él TODAS las métricas de aplicación**, porque
  viajan por su textfile-collector. Por eso `ServiceDown` cubre también los
  targets de infraestructura.

## Catálogo de alertas

### Críticas — sacan a alguien de la cama

| Alerta                         | Qué significa                                                                      | Primer paso                                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `ServiceDown`                  | Un target lleva 2 min sin responder al scrape.                                     | `docker compose ps` y los logs del servicio. Si es api-server, la plataforma está caída para todos.               |
| `VaultSealed`                  | Vault sellado: ningún secreto se resuelve.                                         | Desellar es el PRIMER paso post-reinicio → [restart-services](./restart-services.md).                             |
| Alertas de host (disco, OOM)   | Ver [`host_alerts.yml`](../../docker/monitoring/prometheus/rules/host_alerts.yml). | Espacio y memoria del host.                                                                                       |
| `WatchdogServiceUnrecoverable` | El watchdog agotó su backoff reiniciando un contenedor y sigue caído.              | Ya no hay recuperación automática posible: ver [«El watchdog»](#el-watchdog-recuperación-automática-y-su-límite). |

Las `critical` salen **por dos caminos**: la notificación por la plataforma y el
Slack de respaldo. Es deliberado — si el caído es el api-server, no puede
entregarse la alerta a sí mismo.

> ⚠️ **El segundo camino todavía no entrega, y hace falta una persona para que
> entregue.** Ver [«Lo que falta para que el Slack de respaldo
> funcione»](#lo-que-falta-para-que-el-slack-de-respaldo-funcione) al final de
> este runbook. Hasta entonces, una `critical` con el api-server caído **no
> llega a nadie**, que es exactamente el escenario para el que se diseñó el
> respaldo.

### Warnings — se miran en horario laboral

| Alerta                         | Qué significa                                                                                    | Primer paso                                                                            |
| ------------------------------ | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `CeleryQueueGrowing`           | Una cola lleva 15 min con >50 mensajes: nadie la drena o hay backlog.                            | ¿Vive el worker de esa cola? ¿Tiene consumidor?                                        |
| `NotificationsDLQNotEmpty`     | Notificaciones que agotaron reintentos = trabajo perdido.                                        | Revisar credenciales/URL del canal, re-encolar o descartar.                            |
| `ExecutionFailureRateHigh`     | >20% de runs fallando (con ≥10 runs, para no gritar con el sistema parado).                      | Credencial de proveedor LLM caducada, runtime roto o egress-proxy caído.               |
| `HumanApprovalsStale`          | Una aprobación lleva >24 h sin respuesta: su ejecución está DETENIDA.                            | Bandeja del inbox humano.                                                              |
| `MetricsSamplerStale`          | El sampler lleva >5 min sin correr: todo lo `agentic_*` está congelado.                          | Beat y worker de mantenimiento.                                                        |
| `MetricsCollectorDown`         | Un colector concreto falla (`up=0`).                                                             | `maintenance.sample_queue_metrics.error` en los logs del worker.                       |
| `TasksBlockedHigh`             | >10 tareas bloqueadas 30 min.                                                                    | Inbox humano y visor de runs.                                                          |
| `MemorizerDistillationFailing` | 5 destilaciones seguidas fallidas: la plataforma ha dejado de aprender de sus runs, y sin ruido. | Fila activa de kind `ollama` en `llm_providers`; logs `memorizer.distillation_failed`. |
| `VaultTokenExpiringSoon`       | Al token de Vault le quedan <24 h: la renovación no funciona.                                    | `vault.token.renew_failed` en logs; rotar el token de servicio.                        |

> **Regla al añadir una alerta**: si cita una métrica que nadie emite, NUNCA
> dispara — y eso es indistinguible de «todo va bien». La guarda
> `test_no_rule_references_a_metric_nobody_emits` lo impide, así que **añadir una
> alerta obliga a añadir su emisor**.

## Buscar logs

Todos los servicios emiten **JSON con PII enmascarada** (email, IBAN, DNI/NIE,
JWT, `Bearer …` y claves de API por prefijo: `sk-`, `gh?_`, `hvs.`, de las que se
conserva solo el prefijo para poder diagnosticar de qué familia era).

En Grafana → Explore → datasource **Loki** (retención 7 días):

```logql
{container=~".*api-server.*"} | json | request_id = "<uuid>"
{container=~".*workers.*"}    | json | execution_id = "<uuid>"
{container=~".*"} | json | level = "error"
```

El `request_id` de la petición HTTP viaja en las cabeceras del mensaje Celery y
se bindea en `task_prerun`, así que **la misma búsqueda encuentra la petición del
api-server y el trabajo del worker que disparó**.

Sin el overlay de monitorización: `docker compose logs -f <servicio>` (json-file,
~50 MB por contenedor).

## Probar la cadena sin esperar a una avería

### 1. Alerta sintética por Alertmanager

```bash
docker compose exec alertmanager amtool alert add \
  alertname=PruebaManual severity=critical instance=manual \
  --annotation=summary="Prueba de la cadena de alertas" \
  --alertmanager.url=http://localhost:9093
```

Debe aparecer como notificación en la bandeja de plataforma del System Admin
(`GET /notifications/platform/logs`) en menos de un minuto. Y **además** en el
Slack de respaldo, en cuanto alguien complete los cinco pasos de [«Lo que falta
para que el Slack de respaldo funcione»](#lo-que-falta-para-que-el-slack-de-respaldo-funcione);
hoy ese segundo camino falla en cada envío.

### 2. Directamente contra el ingest

```bash
curl -sS -X POST http://api-server:8000/internal/alerts/ingest \
  -H "Authorization: Bearer $API_SERVER_ALERTS_INGEST_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"version":"4","status":"firing","alerts":[{"status":"firing",
       "fingerprint":"manual-1",
       "labels":{"alertname":"PruebaManual","severity":"critical"},
       "annotations":{"summary":"Prueba"}}]}'
```

`{"accepted": 1, "deduped": 0}` es el resultado bueno. `503` = falta
`API_SERVER_ALERTS_INGEST_TOKEN` (fail-closed a propósito). Repetir el mismo
`fingerprint` con el mismo `status` devuelve `deduped: 1` — es correcto, así se
evita el spam de cada `repeat_interval`.

### 3. Que el exporter responde

```bash
docker compose exec api-server curl -s localhost:8000/metrics | head -20
```

## El watchdog: recuperación automática y su límite

El watchdog (`apps/watchdog`) vigila los servicios de infraestructura del compose
—postgres, redis, minio, vault, clamav, **egress-proxy** y **registry-proxy**— y
los reinicia con backoff exponencial (5 intentos: 10 s, 30 s, 90 s, 270 s, 810 s).
Cuando agota los intentos deja de reintentar y **emite
`WatchdogServiceUnrecoverable`** contra `/internal/alerts/ingest`, que la convierte
en notificación del System Admin por el camino normal.

Los dos proxies entraron en la lista con prod-08 y no es un detalle: el
`egress-proxy` es la única salida de los agent-runtimes hacia los LLM (ADR 0019) y
el `registry-proxy` la única de los runtime-templates hacia los registries
(ADR 0094). Su caída se manifiesta como «los agentes no funcionan» sin que nada
señale la causa.

### Levantarlo

Va bajo un **perfil de Compose**, porque `docker/docker-compose.yml` es la capa de
infraestructura y el watchdog es una aplicación:

```bash
docker build -f apps/watchdog/Dockerfile \
  --build-arg BASE_IMAGE=agentic-platform/api-server:latest \
  -t agentic-platform/watchdog:latest .

docker compose --profile watchdog up -d watchdog
```

**No monta el socket Docker** (principio rector 2): habla con el daemon por
`DOCKER_HOST` a través del `docker-socket-proxy` del ADR 0060, al que le basta
`CONTAINERS=1` + `POST=1`. Si en tu despliegue el proxy no existe, el watchdog
arranca y no encuentra ningún contenedor — lo dice con
`watchdog.container_missing` por cada servicio.

### Comprobar que avisa de verdad

```bash
docker compose stop egress-proxy               # o cualquier vigilado
docker compose --profile watchdog logs -f watchdog
```

Secuencia esperada: `watchdog.restart` ×5 espaciados por el backoff → una sola
línea `watchdog.alert` → `watchdog.alert_delivered`. Si sale
`watchdog.alert_delivery_rejected status=401`, el token no coincide con el del
api-server; si sale `watchdog.alert_sink_unconfigured` al arrancar, faltan
`WATCHDOG_ALERTS_INGEST_URL` / `WATCHDOG_ALERTS_INGEST_TOKEN` y la alerta se queda
en un log local — que es el defecto que esta pieza vino a cerrar.

La entrega **se reintenta en cada tick mientras no confirme**: un api-server que
arranca después del watchdog no debe costar la única notificación del episodio.
Confirmada, no se repite (el bucle tickea cada 30 s).

## Lo que falta para que el Slack de respaldo funcione

**Esto no es trabajo pendiente de código: es una decisión y una credencial, y solo
las puede aportar una persona.** Se deja escrito aquí para que la siguiente pasada
no vuelva a medir lo mismo.

Lo que **ya está hecho** y no hay que rehacer:

- `docker/monitoring/alertmanager/alertmanager.yml` tiene el receiver
  `critical-fallback` en la ruta `severity=critical`, con el `continue: true` sin
  el cual el árbol de rutas se detiene en la primera coincidencia y el respaldo
  **sustituiría** a la notificación por plataforma en vez de duplicarla;
- el instalador monta ese mismo fichero (`compose_generator._alertmanager_service`),
  así que no hay una segunda plantilla que mantener;
- `tests/unit/test_alertmanager_routing.py` lo guarda.

Lo que **falta**, en orden:

1. **Decidir dónde se custodia el webhook de Slack.** Es un secreto de
   despliegue; el sitio coherente con el resto de la plataforma es Vault
   (prod-10). Alternativa aceptable: un fichero en el host con permisos 0600.
2. **Crear el webhook entrante en Slack** y anotar su URL
   (`https://hooks.slack.com/services/...`) en el canal de guardia que
   corresponda.
3. **Provisionarlo como fichero**: el receiver lo lee de
   `/etc/alertmanager/secrets/slack_api_url`, no de una variable de entorno
   (Alertmanager no interpola env en su config).
4. **Montarlo** en el servicio `alertmanager` que genera el instalador
   (`_alertmanager_service`) — hoy no declara ese volumen, así que el fichero no
   existe dentro del contenedor.
5. **Probarlo** con la alerta sintética del paso 1 de la sección anterior y
   comprobar que llega a Slack, no solo a la bandeja de la plataforma.

Mientras 1–4 no se hagan, Alertmanager **arranca igual** y falla en cada envío al
respaldo: degradación deliberada, no un arranque roto. El efecto neto es que
`severity=critical` con el api-server caído no llega a ningún humano.

## Añadir una métrica sin romper nada

1. **Elige el camino correcto.** Dentro del proceso api-server (peticiones HTTP)
   → `api_server/metrics.py`. Agregados del sistema (BD, colas, contadores de
   varios procesos) → un colector en `workers/maintenance/queue_sampler.py` +
   render en `workers/queue_metrics.py`, y añádelo a `KNOWN_COLLECTORS` para que
   su fallo no sea mudo.
2. **Respeta el catálogo cerrado de labels**: `tenant_id`, `queue`, `status`,
   `provider`. `execution_id` / `task_id` / `user_id` / `plan_id` están
   PROHIBIDOS — cardinalidad ilimitada tumba la TSDB en una máquina única. Eso es
   trabajo de logs. Hay tests que lo hacen cumplir.
3. **Si la métrica existe para alertar, escribe la alerta y el panel a la vez.**
   Una métrica que nadie mira es coste sin beneficio.

## Relacionado

- [`docs/04-reference/metricas.md`](../04-reference/metricas.md) — catálogo completo.
- [ADR 0139](../05-architecture-decisions/0139-loki-agregacion-de-logs.md) — Loki.
- [ADR 0140](../05-architecture-decisions/0140-alcance-del-tracing-otel.md) — alcance de OTEL.
- [ADR 0141](../05-architecture-decisions/0141-observabilidad-de-los-servicios-celery.md) — por qué los workers no exponen `/metrics`.
- [`monitoring-cadvisor.md`](./monitoring-cadvisor.md) — cAdvisor sin `privileged`.
