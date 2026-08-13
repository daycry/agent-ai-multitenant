---
title: Auditoría dirigida — monitorización, tools por proveedor y notificaciones (2026-07-16)
version: 1.0
audit_date: 2026-07-16
last_updated: 2026-07-16
status: published
created_by: claude-fable-5-audit-2026-07-16
docs_language: es
baseline_branch: plan/runs-visor-trabajo
baseline_commit: ebee96806b8eb5e5ce2a9b2f3916a628a0eccf39
scope: monitorizacion-docker, tools-por-proveedor, notificaciones, barrido-colateral
---

# Auditoría dirigida (2026-07-16) — monitorización, tools por proveedor y notificaciones

Auditoría read-only del código y del stack Docker VIVO en dev, solicitada por el
operador con tres focos: (1) cableado con los dockers de monitorización, (2)
agentes/memorias/tools con formato dependiente del modelo que «no cargan bien»,
(3) notificaciones. Incluye el barrido colateral del sistema de runs/memoria/
review (workflow de 30 agentes con verificación adversarial de cada hallazgo
critical/high/medium). No se modificó ningún estado, fila ni contenedor.

## Veredicto ejecutivo

1. **El síntoma de las tools es real y tiene causa raíz concreta, pero está en
   el camino que nunca se ejercita.** Las dos tools de finalización
   (`submit_result`, `submit_verdict`) se inyectan en formato plano (sin el
   envelope OpenAI `{"type":"function","function":…}`) hacia los tres
   proveedores HTTP (ollama/copilot/azure_foundry): en endpoints estrictos es
   un 400 en la primera iteración; en Ollama, una tool fantasma que el modelo
   no ve. No ha explotado en producción porque **el 100% del histórico (103
   runs con model_call) es claude_sdk**. Un test de integración lleva rojo
   desde el 2026-06-27 documentándolo (AUD16-01).

2. **La monitorización está mejor cableada de lo esperado**: la cadena
   Prometheus → Alertmanager → `/internal/alerts/ingest` → notification-dispatcher
   → notificación in-app se verificó VIVA hoy con una alerta real
   (`HostSwapActive`), y las métricas de aplicación fluyen por el textfile
   collector. Los agujeros reales de este host son cAdvisor (0 contenedores
   visibles → paneles per-container vacíos y `ContainerOOMKilled` no puede
   disparar jamás, con healthcheck verde que lo enmascara) y la ausencia de
   métricas de disco del host en Windows. Loki y las trazas OTLP no están «a
   medias»: están limpiamente ausentes a la espera de los ADR de prod-08.

3. **Las notificaciones funcionan mecánicamente… y no llegan a nadie.** El
   dispatcher está healthy, sin backlog ni DLQ; el bug del body de WhatsApp
   está arreglado y desplegado; el alerts-ingest está vivo. Pero todos los
   envíos reales son platform-scoped (`tenant_id=NULL`) y el único endpoint de
   inbox los excluye por diseño; además `in_app` no persiste el cuerpo del
   mensaje. Resultado: hoy ninguna notificación llega de facto a un ojo humano
   (`notification_log_reads` = 0 lo confirma).

4. **El barrido colateral confirma que la ola de fixes de convergencia
   funcionó** (cero aborts estructurales desde el 07-03 en ~40 runs, memoria
   86,3% memorizada con embeddings al 100%, review pipeline sano, recovery de
   zombis correcto, backups verificados hoy) — y aflora deuda nueva con dato
   vivo: el destilador de memorias cae siempre al fallback `llama3.2:1b`
   (el fix F2.1 nació muerto para agentes con modelo heredado), el coste
   facturable por catálogo está ciego en 128/128 runs (`claude-opus-4-8` no
   existe en `model_prices`), y los pilares de gobernanza (guardrails,
   approval_requests, audit_log) tienen **cero dato vivo en toda la historia**.

## Alcance y método

- Tres agentes de auditoría dirigidos (monitorización, tools/memorias/persona,
  notificaciones) sobre código + stack vivo, con comandos solo-lectura
  (psql SELECT, docker logs/inspect, redis-cli de lectura, HTTP GET internos).
- Workflow `auditoria-runs-sistema`: 6 analistas forenses + verificación
  adversarial de cada hallazgo critical/high/medium + crítico de completitud
  (30 agentes en total). Los veredictos adversariales corrigieron o refutaron
  varios hallazgos; aquí solo se reportan los que sobrevivieron.
- Contrastado con la auditoría integral del 14-07 (AUD14-01…08) y su plan de
  remediación (`pending_approval`) para no duplicar hallazgos con dueño.
- El stack estuvo apagado del 14-07 13:37 al 16-07 ~14:08 (~49 h, host dev
  Windows); todo lo «en vivo» se observó tras el arranque de hoy.

---

## Frente A — Agentes, memorias y tools por proveedor

### AUD16-01 — `submit_result`/`submit_verdict` viajan sin envelope OpenAI a los 3 proveedores HTTP

**Severidad**: crítica · **estado**: broken (latente: hoy solo corre claude_sdk)

`_SUBMIT_RESULT_TOOL` y `_SUBMIT_VERDICT_TOOL`
(`docker/agent-runtimes/agent-runtime/agent_runtime/providers.py:179-201` y
`:141-159`) son dicts planos `{name, description, parameters}`, mientras el
resto de tools del agente sí van envueltas
(`apps/workers/src/workers/agent_tool_schemas.py:319`). El body resultante de
cada `decide()` es una lista MIXTA y el de cada `review()` lleva su única tool
sin envelope; ollama/copilot/azure la pasan verbatim
(`packages/shared-llm/.../ollama.py:142-143`, `copilot.py:335-336`,
`azure_foundry.py:123-124`). Azure/Copilot validan estricto → `ProviderError`
no transitorio → run abortado en la iteración 0; Ollama degrada a una tool
«husk» sin nombre → el modelo nunca ve `submit_result`/`submit_verdict` y el
FINISH cae siempre a la red de prosa. El contraste interno delata el bug:
`_SUBMIT_PROGRESS_TOOL` (ADR 0112) sí va envuelto.

**Evidencia ejecutada**: `pytest tests/integration/test_model_clients.py` →
`test_azure_decide_targets_apim_url_with_subscription_key` **FALLA hoy**
(«Left contains one more item: {'name': 'submit_result', …}») — rojo desde que
ADR 0087 aterrizó (`bcdd9cb2`, 2026-06-27), ~3 semanas sin que nadie lo viera.

**Acción**: envolver ambas tools (o normalizar en los providers HTTP como hace
`_unwrap_tool_schemas` de claude_sdk, que tolera ambas formas —
`claude_agent.py:721-742`) y extender el test de wire-format a decide+review
de los 3 kinds HTTP.

### AUD16-02 — Tools de orquestación: se ejecutan, devuelven `ok=true` y sus efectos no se aplican jamás

**Severidad**: alta · **estado**: broken (regresión parcial del H3 de 2026-06-24 a «éxito falso»)

`kanban_update`/`task_comment`/`agent_invoke`/`notify_user` emiten un _effect_
a un `OrchestrationSink` local (`orchestration_tools.py:29-41`) «que el worker
drena» — pero el worker no lo drena: `workers/execution.py:1113-1130` solo
procesa `step`/`execution.finished`/`execution.error`, y no hay ningún
consumidor de `effect` en `apps/workers` ni endpoint kanban/comment/invoke en
`internal_api.py`. El sink nace y muere dentro del contenedor. Evidencia viva:
un `task_comment` con `ok=true` en steps_log cuyo comentario no existe en
ninguna tabla. Es peor que el silencio original: el agente cree que funcionó.

**Acción**: drenar los effects en el worker o dejar de anunciar la familia al
modelo (`SYSTEM_TOOL_NAMES`, `agent_tool_schemas.py:201-213`) hasta cablearla.

### AUD16-03 — Cero runs jamás sobre ollama/copilot/azure: el camino HTTP no está validado en vivo

**Severidad**: alta · **estado**: gap de validación

SQL sobre steps_log: el histórico completo es `claude-opus-4-8` (103 runs,
2026-06-29→07-09) + 25 runs sin model_call (fallos de infra). El default de
plataforma apunta a ollama (`gpt-oss:120b`) pero el equipo activo corre con
opus. Por eso AUD16-01 nunca explotó: el único camino no ejercitado es
exactamente el que está roto. **Acción**: tras corregir AUD16-01, lanzar un
run e2e de humo por cada kind HTTP antes de dar por buenos los 4 caminos.

### AUD16-04 — Los system prompts anuncian `search_code`, que no existe en el runtime

**Severidad**: media · **estado**: broken

`providers.py:99` y `:126-128` nombran `search_code` entre las tools read-only,
pero no está cableada (el propio `agent_tool_schemas.py:237-244` la excluye del
anuncio por g4). El modelo la invoca a pelo: steps_log 14 días → **7 llamadas,
7 fallos** («unknown tool: search_code»), turnos quemados en errores.
**Acción**: retirarla de ambos prompts o cablear un executor real.

### AUD16-05 — claude_sdk degrada los JSON Schema de las tools a `{campo: tipo}`

**Severidad**: media · **estado**: risk

`claude_agent.py:745-764` (`_json_schema_to_tool_schema`) descarta `required`,
`enum` (p. ej. los scopes de `memory_recall`), las descriptions por campo y los
objetos anidados; una tool sin properties inventa `{"input": str}`. Los HTTP
reciben el schema íntegro. Es la asimetría real de fidelidad que queda HOY
entre proveedores: en claude_sdk el modelo adivina valores que en los demás ve
especificados. **Acción**: pasar el JSON Schema completo al `@tool` del SDK
(lo admite como dict crudo).

### AUD16-06 — Streaming OpenAI-compat descarta los deltas de `tool_calls`

**Severidad**: baja · **estado**: risk (hoy sin uso con tools)

`_openai_compat.py:126-146` solo extrae `delta.content`; un `delta.tool_calls`
se ignora en silencio. Único uso actual de `stream()` es el camino FINISH_NUDGE
del asistente (sin tools). Documentar la limitación o parsear los deltas.

### Verificado en verde (frente A)

- **Persona, memorias (auto-recall D1) y auto-RAG llegan al prompt de forma
  provider-agnóstica** — mismo `system_preamble` y mismo nodo `recall` para
  los 4 adapters (verificado fichero a fichero). Matiz no per-provider: con
  > 8 items de contexto las memorias más viejas se condensan a 1 línea y con
  > 15 se evictan.
- **Córtex/asistente**: schema-gap #10e cerrado y pineado por test
  (`test_cortex_claude_sdk_transport`); FINISH_NUDGE vigente; los schemas van
  correctamente envueltos en los 4 caminos.
- **Divergencias por-kind deliberadas** (no bugs, conviene conocerlas): la
  allowlist de shell ADR 0092 solo aplica a claude_sdk (un agente HTTP sin
  `allowed_commands` tiene shell deny-all); `submit_result`/`tool_choice`
  solo HTTP (claude_sdk termina en prosa + tag `<finish>`); stack_exec y MCP
  se anuncian igual en los 4 caminos.

**Colaterales del frente A**: un turno del córtex murió el 07-13 con
`httpx.ReadTimeout` contra ollama local → 500 crudo al usuario
(`routers/cortex.py:310`) — merece manejo tipado; 12 agentes plantilla
`global_builtin` llevan `model_config.provider='anthropic'` (kind inválido —
ningún run los usa directamente, pero fallarían en `resolve_model_spec` si se
instanciaran tal cual); `llm_usage_events` (ADR 0116) a 0 filas — esperado, no
hay tráfico posterior al despliegue; verificar tras el próximo turno.

---

## Frente B — Cableado con los dockers de monitorización

Despliegue vivo verificado: base + dev + manuals + monitoring + monitoring.dev

- windows, todos los servicios de monitoring corriendo y healthy en la red
  única `agentic-net` (sin partición; los 3 targets de Prometheus `up`).

### AUD16-07 — cAdvisor no ve NINGÚN contenedor en este host

**Severidad**: alta · **estado**: broken (en Docker Desktop)

`count(container_last_seen)` = 1 (solo el cgroup raíz `{id="/"}`). Los logs de
cAdvisor repiten para todos los contenedores: `failed to identify the
read-write layer ID … image/overlayfs/layerdb/mounts/...: no such file or
directory` — Docker Desktop usa el containerd snapshotter y cAdvisor v0.49.1
no resuelve la capa RW. Consecuencias: los paneles per-container de ambos
dashboards están vacíos y **la alerta `ContainerOOMKilled` no puede disparar
nunca**; el healthcheck da verde (el endpoint responde), enmascarando el
fallo. En un deploy Linux con overlay2 probablemente funciona, pero
`docker-compose.windows.yml` no lo documenta ni mitiga y no hay gotcha en
`docs/03-guides/gotchas/`.

**Acción**: documentar la gotcha; evaluar bump/flags de cAdvisor para
containerd; añadir al smoke de monitoring un meta-check «cadvisor ve >1
contenedor».

### AUD16-08 — Vigilancia de disco del host inexistente en Windows-dev

**Severidad**: media · **estado**: risk (degradación documentada, pero deja ciego un requisito)

`docker-compose.windows.yml:24-38` (`volumes: !override`) elimina el rootfs de
node-exporter → `node_filesystem_size_bytes` solo expone 2 series tmpfs que la
regla `HostDiskUsageHigh` excluye: la alerta de disco >80% (task_12_14) no
puede disparar jamás en este host, y un backup-disco-lleno solo lo cazaría
`BackupLastRunFailed` a posteriori. **Acción**: anotar en el runbook; sin fix
de código razonable en dev Windows.

### AUD16-09 — Métricas de app omitidas cuando no hay datos: «No data» ≡ «sampler muerto»

**Severidad**: baja · **estado**: risk de diseño

`queue_metrics.py:83` omite la familia entera `agentic_executions_24h` si el
dict está vacío (ídem `dlq_depths`): el panel «pulso» no distingue «no hubo
runs» de «el sampler murió». **Acción**: emitir siempre HELP/TYPE con series a
0, o un heartbeat `agentic_sampler_last_run_timestamp_seconds` + regla de
staleness.

### Verificado en verde (frente B)

- **Cadena de alertas extremo a extremo VIVA**, verificada hoy con una alerta
  real: `HostSwapActive` (firing) → Alertmanager → `POST
/internal/alerts/ingest` 200 (Bearer coincidente, endpoint fail-closed) →
  notification-dispatcher → `send_notification … status: sent, channel:
in_app`. El hueco histórico de prod-08 («endpoint que no existía») está
  cerrado. (Pero ver AUD16-10: la notificación resultante es invisible.)
- **Pipeline textfile-collector funcionando**: `agentic_celery_queue_depth`
  (5 colas), `agentic_tasks_by_status`, `agentic_dlq_depth`,
  `agentic_backup_last_success=1`, `node_textfile_scrape_error=0`.
- Retención acotada (TSDB 15d, 450 MB; logs json-file rotados), Grafana
  aprovisionado limpio.

**Contexto con dueño (prod-08, `pending_approval` — no son regresiones)**:
Loki/recolección de logs limpiamente ausente (logs solo en json-file rotado
10m×5, sin búsqueda cross-servicio, sin enmascarado PII); trazas OTel: solo el
api-server instrumenta (spans a ConsoleSpanExporter opt-in apagado — se
generan y descartan; el fruto real es `trace_id` en logs), resto de servicios
sin instrumentar; Prometheus solo scrapea prometheus/node-exporter/cadvisor —
las métricas de app llegan exclusivamente por textfile.

**Menores**: node-exporter es el único servicio del overlay sin healthcheck;
el update-checker de plugins de Grafana sigue llamando fuera pese a
`GF_ANALYTICS_CHECK_FOR_UPDATES=false`; el token del alerts-ingest en dev es
el literal `dev-alerts-ingest-token` (el installer genera uno aleatorio en
prod — correcto); el gate AUD14-01 (`textfile-init` sin hardening) **sigue
rojo**, dueño: plan de remediación 07-14, Fase A.

---

## Frente C — Notificaciones

Arquitectura real verificada: los emisores (api-server, orchestrator, workers)
producen la task Celery `notification_dispatcher.dispatch_event` sobre las
colas Redis `notifications.default`/`notifications.priority` (son colas
kombu en DB1, **no** streams; el único stream es el DLQ `dlq:notifications` en
DB0) → EVENT_REGISTRY de 20 eventos → preferencias + quiet-hours + plantillas
ES/EN → canal (in_app, telegram, email, slack, teams, discord, whatsapp
Cloud API/neonize, SMS, webhook saliente con HMAC).

### AUD16-10 — Ninguna notificación llega de facto a un humano: los envíos platform-scoped son invisibles

**Severidad**: alta · **estado**: broken

El único lector de `NotificationLog` es `GET /notifications/logs`
(`routers/notifications.py:615-683`) que filtra `tenant_id == tenant_id` con
comentario explícito: _«the inbox can never include a NULL-tenant platform
send»_; el inbox del admin-panel llama a ese mismo endpoint. Todas las filas
existentes (23) son `infra_alert / in_app / sent` con `tenant_id=NULL`, no
existe inbox de plataforma ni canal telegram/email platform (solo hay 1 canal
en toda la BD: in_app platform). `SELECT count(*) FROM notification_log_reads`
→ **0**; 0 requests a `/notifications/logs` en 72 h. La cadena de alertas
«resucitada» funciona hasta la BD y muere ahí; los mensajes proactivos del
córtex, igual.

**Acción**: inbox/endpoint de plataforma para el System Admin (o incluir
NULL-tenant cuando `is_system_admin`) y/o configurar un canal externo
platform (telegram/email).

### AUD16-11 — `in_app` no persiste el contenido del mensaje

**Severidad**: media-alta · **estado**: broken (producto)

`notification_logs` no tiene columna subject/body
(`db/notification.py:344-403`); el body renderizado se descarta en
`tasks.py:450-460` y la respuesta del endpoint solo lleva
event_type/channel/status/target. Una notif in-app solo dice «pasó un
`infra_alert`» — qué alerta, qué instancia, qué plan: perdido. **Acción**:
persistir subject/body (truncados) para `channel_type=in_app`.

### AUD16-12 — WhatsApp: body bug ARREGLADO; neonize implementado pero sin desplegar

**Severidad**: media · **estado**: fixed_verified (body) + risk operativo (neonize)

El body bug está corregido y desplegado (commit `6392a5f0`, NOTIF-1:
`event_mapping.py:626-634` + fallback en `channels/whatsapp.py:339-345`; la
imagen `notification-dispatcher:manuals` del 07-12 lo incluye — verificado
conductualmente). Neonize (NOTIF-4/ADR 0109) está completo en código con
sidecar y runbook, pero `profiles: [neonize]` está apagado, no hay contenedor,
no hay emparejamiento QR ni canal con provider neonize: **código muerto en
runtime hasta que el operador lo active**. Matiz: el canal WhatsApp nunca ha
enviado nada (0 canales whatsapp en BD).

### AUD16-13 — Los eventos de escalado jamás se han notificado; sin canales tenant ni preferencias

**Severidad**: media · **estado**: unexercised

El dispatcher soporta `task_blocked`, `review_escalated`,
`human_validation_needed`, `plan_*` (plantillas ES/EN incluidas), pero el
histórico completo de `notification_logs` es una sola combinación:
`infra_alert/in_app/sent` ×23. Cero notificaciones de escalado en toda la
historia; `notification_preferences` = 0 filas; único canal = in_app platform.
Las 19 executions `needs_human_review` se ven en el panel de escaladas, pero
nadie recibe aviso push/externo. **Acción**: configurar canales/preferencias
reales (al menos telegram o email para el operador) y un e2e de
`task_blocked`.

### Verificado en verde (frente C)

- Dispatcher healthy, healthcheck correcto (el fix del installer del 07-10
  activo), colas a 0, DLQ a 0, 0 errores en 72 h, sin duplicados (23/23).
- Alerts-ingest implementado y vivo e2e (ver frente B), con dedup por
  fingerprint y fail-closed sin token.
- Multi-tenancy del camino completa: BYPASSRLS compensado con validación
  explícita de boundary (`CrossTenantNotificationError`), filtros tenant-o-NULL
  y CRUD con guards. Sin queries sin tenant_id.
- Los 20 eventos del registro tienen emisor real (sin huérfanos); webhook
  saliente con HMAC-SHA256 + nonce + anti-replay.

**Menores**: la cola `notifications.priority` está declarada sin
exchange/routing propio (`celery_app.py:40`) — hoy sin efecto (un único worker
drena ambas), pero corregir antes de separar workers por lane;
`apps/webhook-dispatcher/` es una app fantasma (solo `.gitkeep`) — el canal
webhook del notification-dispatcher cubre su función; el installer de prod
pone `NOTIFY_EVENTS_REDIS_URL` en DB **3** vs DB 0 en dev
(`compose_generator.py:872`) — revisar qué DB lee la vista del DLQ en prod.

---

## Frente D — Barrido colateral del sistema de runs (workflow, 30 agentes)

### Confirmado en verde con dato vivo

- **La ola de fixes de convergencia funcionó**: cero aborts estructurales
  (max_iterations, repetitive_loop, research_exhausted, max_review_retries)
  desde el 2026-07-03 en ~40 runs posteriores. Los «misterios» del inventario
  previo eran datos caducados: `finish_status` se puebla desde el 07-02 (27
  filas `success`), `review_sessions` no es tabla muerta (es la sesión de
  validación humana a nivel PLAN, ADR 0063; hoy 5 filas con ciclo completo),
  y las 2 tasks «que no convergían» están done desde el 07-03 con su plan
  completed.
- Memoria: 86,3% de runs done memorizados (63/73, los 10 restantes con causa
  explícita), embeddings 200/200 (vector 768, HNSW), auto-recall D1 inyectando
  5 memorias en todos los runs recientes.
- Review: feedback accionable persistido y reinyectado en el prompt del
  siguiente intento; `needs_human_review` → task blocked visible y accionable
  en el panel; reviewer-ciego (ADR 0095) observado funcionando.
- Workers/infra: recovery de zombis tras apagado del host funciona (sweep de
  huérfanos ~9 min); egress-proxy y registry-proxy con allowlist operativa;
  colas limpias; backups **producen y verifican artefactos** (bundle de hoy
  14:08 con 10 checks OK).

### AUD16-14 — El destilador de memorias nunca usa el LLM del agente: 100% de las memorias vivas las destiló `llama3.2:1b`

**Severidad**: alta · **estado**: fix_regressed (nació muerto para modelos heredados)

El camino primario de F2.1 lee `agent.model_config` crudo, pero los agentes
con modelo heredado (plataforma→proyecto→agente, ADR 0065/0082) tienen
`model_config` sin provider_id/model — la herencia se resuelve en el dispatch,
no se materializa en la fila. `_build_agent_llm` devuelve `None` en silencio
para TODOS los agentes del tenant Demo y el destilado cae siempre al fallback
local de 1B — exactamente el modelo cuyo ~50% de ruido motivó el fix. Las 95
memorias vivas de runs llevan `distill_model='ollama:llama3.2:1b'` y ~21% son
ruido trivial (<60 chars) que ocupa slots del recall (límite 5).

**Acción**: en `_build_agent_llm`, resolver la herencia real (mismo resolver
por provider_id/kind del dispatch) cuando `model_config` no traiga
provider/model, y loguear el fallback en vez de tragarlo.

### AUD16-15 — Coste facturable por catálogo ciego en 128/128 runs

**Severidad**: alta · **estado**: broken

`price_snapshot_cost_usd` es NULL en todas las executions pese a
`total_cost_usd` poblado ($42,86 en 103 runs): cada `price_snapshot` dice
`{"available": false … no current price in catalog}` porque el modelo real en
uso (`claude-opus-4-8`) **no existe en `model_prices`**. Todo el coste depende
del auto-reporte del provider. **Acción**: añadir el modelo (y alias/matching)
al catálogo de precios; alerta si el snapshot corre >N días sin precio.

### AUD16-16 — Gobernanza con cero dato vivo: guardrails, aprobaciones y audit_log jamás ejercitados

**Severidad**: alta · **estado**: unexercised/broken (principios rectores 10 y 11)

- `guardrail_events` = 0, `guardrail_alert_rules` = 0, `guardrails_config`
  NULL en los 10 proyectos, cero claves de guardrails en `platform_settings`:
  el pilar entero jamás ha disparado. ADR 0102 además está parcial:
  pre_tool/post_tool vivos con enforce en el runtime
  (`graph.py:774-819`), **pre_llm/post_llm sin invocar en ningún punto**.
- `approval_requests` = 0 filas en 128 runs con commits, pushes y stack_exec:
  la validación humana por 13 categorías de acciones sensibles nunca generó
  una solicitud — o no está cableada al camino del run o ninguna política
  está activa.
- `audit_log` = 0 filas: `write_audit_log` tiene 17 call sites pero ninguno en
  login (pese a su docstring) ni en las acciones humanas de escalado.

**Acción**: parte tiene dueño (prod-03/ADR 0102, prod-09); lo accionable ya:
un e2e que configure UNA regla de guardrail y UNA política de aprobación y
verifique el disparo, y cablear `write_audit_log` al login.

### Resto de hallazgos del barrido

| ID       | Sev.  | Hallazgo                                                                                                                                                                                                                                                                          | Acción                                                                                              |
| -------- | ----- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| AUD16-17 | media | P1-1(a) «aprender de fracasos» inefectivo en el camino del worker: sin fila en `platform_settings`, el default real sigue `{'done'}` (el default nuevo de `policy.py` solo aplica a `eligible_statuses=None`, que solo pasan los tests)                                           | Alinear `DEFAULT_MEMORY_MEMORIZABLE_STATUSES` o insertar la setting + test del camino real          |
| AUD16-18 | media | Duplicados de memoria preexistentes vivos (5 filas idénticas del mismo batch del 07-07) — el dedup P1-2 previene nuevos pero no limpia lo anterior, y `recall()` no dedupe por contenido                                                                                          | Consolidar/soft-borrar preexistentes; dedup por contenido normalizado en la fusión RRF              |
| AUD16-19 | media | Backups sin copia offsite: `uploaded=[]` en todos los bundles pese al ítem OFFSITE implementado — deshabilitado o sin configurar; ventana de pérdida = host apagado                                                                                                               | Configurar el destino offsite y alerta si `uploaded` vacío N días                                   |
| AUD16-20 | media | Errores 5xx repetidos de stack_exec no cortan el run: 8 fallos idénticos de infra quemaron 50 iteraciones (019f21be-e5c0); las guardas por novedad no aplican (stack_exec es producing-tool, `tool_classification.py:20-27`)                                                      | N fallos de transporte consecutivos → abort `stack_exec_unavailable`                                |
| AUD16-21 | media | Relanzamientos/reaperturas sin rastro: los redispatch (sweeper, reconciler, SQL manual) no generan `task_audit_events` — la cronología de una task no es reconstruible desde BD; + 8 runs finalizados por reaper/supersede sin `memorize_skip_reason`                             | Emitir task_audit_event en todo redispatch/reapertura con motivo                                    |
| AUD16-22 | media | El prompt del reviewer puede exigir acciones worker-side (histórico: pidió commit/push, imposible en sandbox → bucle). Mitigado desde el 07-01 (prompt del implementador prohíbe git), pero el `what_to_fix` del reviewer sigue sin acotarse a acciones ejecutables por el agente | Restringir `what_to_fix` a ficheros del worktree/stack_exec                                         |
| AUD16-23 | media | Credencial claude_sdk caduca sin renovación automática ni chequeo proactivo (`provider_error` ×17 fue el abort dominante 07-02→07-08: oauth caducado + cuota 429)                                                                                                                 | Verificar credencial antes de relanzar runs; chequeo de sesión OAuth en el healthcheck del provider |
| AUD16-24 | baja  | Plan demo «MVP — API Hello World en PHP» varado en `pending_human_validation` con 4 tasks backlog jamás ejecutadas — es un fixture de `seed-demo-data.mjs` para los manuales, pero infla `backlog=4` en las métricas en cada tick                                                 | Decisión del operador: cancelarlo o reactivarlo                                                     |
| AUD16-25 | baja  | Bandeja de escalado engañosa: 19 executions `needs_human_review` pertenecen todas a tasks ya done                                                                                                                                                                                 | Filtrar por task no-done en el panel                                                                |
| AUD16-26 | baja  | Fricción del runtime-template: `HOME/.composer` no escribible (ruido en cada composer) y bash bloqueado en stack_exec                                                                                                                                                             | Fix de conveniencia en la imagen del template                                                       |
| AUD16-27 | info  | Sin ejercitar (desplegado tras el último tráfico): `plan_comments`=0 jamás, `llm_usage_events`=0, `budget_alert_states`=0                                                                                                                                                         | Verificar tras el próximo ciclo de runs/turnos                                                      |

**Con dueño previo (no se duplican aquí)**: gate de seguridad rojo +
`cortex_conversations`/RLS + embeddings por KB + engines Celery + WS deadline

- readiness (plan remediación 07-14, AUD14-01…08); 15 tablas con
  `rowsecurity=off` — junctions sin tenant_id → prod-14; exporters/alertas de
  app → prod-08; decisión A/B/C del ADR 0108 (canal de veredicto unificado)
  sigue pendiente del operador desde el 07-10 — el fenómeno de escaladas dobles
  del reviewer quedó corregido el 07-03, pero la divergencia estructural de
  canales persiste.

## Candidatos refutados durante la verificación adversarial

- **«La imagen agent-runtime desplegada no contiene los commits del
  07-12/13»** — REFUTADO: la imagen que los workers lanzan (`agent-runtime:v1`,
  `1f5a1554afeb`, build 07-13 12:35, WITH_CLAUDE=1) se construyó DESPUÉS de
  HEAD `ebee9680` y contiene los 8 commits (ADR 0111/0112/0110/0097/0102/
  0103-G10). No hace falta rebuild antes de los próximos runs.
- **«Las tools no cargan en los runs» (literal)** — en los runs vivos
  (claude_sdk) las tools cargan y ejecutan bien: 188 `read_file`, 69
  `write_file`, 52 `stack_exec` en 14 días. El problema real es AUD16-01/03
  (camino HTTP) + AUD16-04/05.
- **«review_sessions es una tabla muerta»** — no: es la sesión de validación
  humana a nivel plan (ADR 0063) y funciona (5 filas, ciclo completo).
- **«done+commit_failed es un estado contradictorio»** — es un marcador
  post-finalización deliberado (fase 5 best-effort); su causa raíz (race
  non-fast-forward) se corrigió el 06-30 sin recurrencia en 95 runs.
- **«runtime_stuck_no_progress es un abort del código»** — el string no existe
  en ningún commit del repo ni en la imagen de la época: sellado manual
  durante un triage. Los defectos subyacentes están cubiertos hoy (sweep de
  huérfanos, timeout duro, finalize de cancel).
- **«Los acceptance_criteria de las tasks que no convergían eran ambiguos»** —
  no: precisos, verificables en sandbox y satisfechos con el tooling
  disponible.

## Priorización propuesta

**P0 — corrige el síntoma reportado y lo invisible-grave** (1-2 días):

1. AUD16-01: envelope de `submit_result`/`submit_verdict` + test wire-format
   de los 3 kinds HTTP (y poner verde `test_model_clients.py`).
2. AUD16-10 + AUD16-11: inbox de plataforma + persistir subject/body en
   in_app — sin esto, toda la cadena de alertas/notifs es un árbol cayendo en
   un bosque vacío.
3. AUD16-15: `claude-opus-4-8` en `model_prices` (coste facturable ciego).
4. AUD16-14: resolver herencia de modelo en el destilador de memorias.

**P1 — evita el siguiente incidente** (2-4 días): AUD16-02 (drenar effects o
des-anunciar), AUD16-04 (retirar `search_code` de los prompts), AUD16-03
(smoke run por kind HTTP), AUD16-07 (gotcha + mitigación cAdvisor), AUD16-23
(chequeo credencial claude_sdk), AUD16-19 (offsite de backups), AUD16-13
(canal externo del operador + e2e de escalado).

**P2 — deuda y pulido**: AUD16-05/06, AUD16-08/09, AUD16-16 (e2e guardrails/
aprobaciones + audit de login, coordinado con prod-03/09), AUD16-17…22,
AUD16-24…26, menores de los frentes B y C.

Esta auditoría no cambia estados del roadmap ni marca tareas; la conversión de
la priorización en plan de remediación formal queda a decisión del operador.
