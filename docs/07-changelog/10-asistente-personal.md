---
plan_id: 10-asistente-personal
title: Asistente Personal y Notificaciones Multicanal
completed_at: null
docs_language: es
---

# Plan 10 — Asistente Personal y Notificaciones Multicanal

## Resumen

Abre el sistema de notificaciones —hasta aquí solo in-app— a los canales
que la gente usa de verdad: **Telegram, Email, Slack, Microsoft Teams,
Discord, WhatsApp, SMS y webhooks salientes**, además del histórico
**in-app**. La unidad es el **canal configurado** (`notification_channels`)
sobre un modelo de **tres capas** plataforma → tenant → usuario: una fila
con `scope='platform'` (`tenant_id` NULL) es un canal tenant-agnóstico del
System Admin; `scope='tenant'` / `scope='user'` (`tenant_id` NOT NULL) son
canales propios del tenant aislados por RLS. Las **preferencias**
(`notification_preferences`) enrutan qué `event_type` va por qué
`channel_type`, opt-in/opt-out, con ventana de quiet-hours; el dispatcher
las resuelve **más-específica-gana** (usuario → tenant → plataforma). El
**log** (`notification_logs`) es append-only y registra cada intento de
envío.

El envío lo orquesta un servicio **notification-dispatcher** centralizado
(Celery dedicado), con un **`ChannelAdapter` Protocol** por transporte. La
convención de canales es **sin SDK pesado**: cada adaptador habla la API
HTTP documentada del proveedor con `httpx` (Telegram Bot API, WhatsApp
Cloud/Graph API, Teams/Discord incoming webhooks, Twilio REST, SendGrid v3),
salvo el Email primario por SMTP con `aiosmtplib`. Los **secretos de canal
nunca se guardan en claro**: viven en exactamente una de dos formas
(`secret_ref` Vault o `secret_encrypted` Fernet-at-rest) y solo se resuelven
en memoria en el momento del envío. Los **webhooks salientes** se firman con
**HMAC-SHA256 + nonce + timestamp** (anti-replay). El envío fallido reintenta
con **backoff exponencial + jitter** y, agotados los reintentos, cae a una
**dead-letter queue** con reintento manual desde la UI.

El **asistente personal** es un agente conversacional cross-proyecto
**accesible SOLO a Tenant Admins** y **doblemente verjado**: el rol
(`require_tenant_admin` 403ea a un `tenant_user`) más un toggle por tenant
`Organization.personal_assistant_enabled` (**default false**) que, apagado,
deniega incluso a un Tenant Admin. Reutiliza la infraestructura de chat
(LangGraph) y la capa `shared-llm` del Plan 03 — **no es un stack LLM
nuevo** (ADR 0033). Sus tools de lectura cross-proyecto corren sobre la
sesión RLS-bound del request, así que **nunca devuelven datos de otro
tenant**.

Las 17 tareas se desarrollaron en cuatro fases (A — modelo y dispatcher,
B — canales primarios, C — canales secundarios y webhooks, D — asistente y
UI), cada canal con su test de adaptador y el aislamiento cross-tenant del
asistente cubierto por `@pytest.mark.cross_tenant`.

## Cambios por tarea

### Fase A — Modelo y Dispatcher

- ✅ **`task_10_01`** — **Modelos** `NotificationChannel` /
  `NotificationPreference` / `NotificationLog` con el modelo de **tres
  capas** (`NotificationScope` = platform/tenant/user) en
  `db/notification.py`, con sus enums (`NotificationChannelType` —
  catálogo cerrado de 9 transportes incl. `in_app`; `NotificationLocale`
  es/en; `NotificationStatus` queued/sent/delivered/failed/retrying/
  dead_letter). Canal/preferencia son **híbridos** (`tenant_id` NULLABLE);
  el log es **tenant-owned append-only**. Contrato de secreto
  never-plaintext: `secret_ref` XOR `secret_encrypted`, `config` nunca
  lleva el secreto en claro.
- ✅ **`task_10_02`** — **Servicio notification-dispatcher** (Celery
  dedicado, colas `notifications.default` / `notifications.priority`) +
  **migración 0045** que crea las tres tablas y la **RLS** (patrón híbrido
  de `marketplace_listings`: política `FOR ALL` de aislamiento tenant +
  política `FOR SELECT` que expone las filas `tenant_id IS NULL`), más los
  CHECK de "como mucho un secreto" y de coherencia scope↔tenant. El
  dispatcher es **BYPASSRLS** y por eso valida `row.tenant_id ==
request.tenant_id` en el límite de la tarea Celery. `ChannelAdapter`
  Protocol + adaptador `in_app` no-op como base de la ruta de envío.
- ✅ **`task_10_03`** — **Plantillas Jinja2 SANDBOXED**
  (`jinja2.sandbox.SandboxedEnvironment`, nunca el `Environment` por
  defecto) con plantillas pre-cargadas por `(event_type, channel_type,
locale)`. Override por tenant en `notification_templates` (**migración
  0046**, tenant-owned + RLS); resolución más-específica-gana (override
  vivo del tenant > builtin en código). Autoescape por canal de markup.
- ✅ **`task_10_04`** — **Mapeo eventos → notificaciones**
  (`event_mapping.py`): taxonomía de eventos del sistema (`task_blocked`,
  `plan_approved`, `review_needed`, `budget_alert`, escalado a humano…)
  resuelta a canal+plantilla+locale aplicando las preferencias de las tres
  capas y las quiet-hours (defer acotado por `quiet_hours_max_defer_s`).

### Fase B — Canales Primarios

- ✅ **`task_10_05`** — **Canal Telegram**: adaptador que POSTea
  `sendMessage` a la Bot API con `httpx` (`{base}/bot{token}/sendMessage`),
  `parse_mode` HTML por defecto (alineado con el autoescape del template).
  Tests con `httpx.MockTransport` (sin red real). El SDK pesado no se usa.
- ✅ **`task_10_06`** — **Canal Email**: ruta **primaria SMTP** con
  `aiosmtplib` (STARTTLS / TLS implícito, MIME con el `email` stdlib); ruta
  **opcional SendGrid** sobre su API v3 HTTP con `httpx`
  (`config.provider='sendgrid'`), sin añadir el SDK `sendgrid`.
- ✅ **`task_10_07`** — **Canal Slack**: POST `chat.postMessage` sobre
  `httpx` con **Block Kit** (blocks). `slack_sdk` queda como dep dev (pina
  el contrato de Block Kit y permite introspección local) — el envío en
  caliente NO usa su cliente aiohttp dentro del `asyncio.run` por-envío.
- ✅ **`task_10_08`** — **Canal Microsoft Teams**: incoming-webhook +
  **Adaptive Card** (versión `1.4` por defecto, override por
  `config.card_version`) sobre `httpx`.
- ✅ **`task_10_09`** — **Canal Discord**: webhook con **embeds** sobre
  `httpx`; color del embed mapeado por severidad, con fallback blurple
  configurable.

### Fase C — Canales Secundarios y Webhooks

- ✅ **`task_10_10`** — **Canal WhatsApp Cloud API**: mensaje de plantilla
  pre-aprobada sobre la Graph API de Meta con `httpx`
  (`{base}/{version}/{phone_number_id}/messages`), versión Graph y locale
  de plantilla pineados/configurables. Cloud API (no Twilio), por coste.
- ✅ **`task_10_11`** — **Canal SMS (Twilio)**: POST a la REST API de Twilio
  con `httpx` (HTTP Basic `AccountSid:AuthToken`,
  `.../Accounts/{Sid}/Messages.json`) en vez del SDK pesado `twilio`; cuerpo
  truncado defensivamente a `sms_max_body_len`.
- ✅ **`task_10_12`** — **Webhooks salientes** firmados **HMAC-SHA256 +
  nonce + timestamp** (`webhook_signing.py`): el timestamp y el nonce se
  pliegan DENTRO del material firmado (`ts.nonce.<body>`), cabeceras
  `X-Signature` / `X-Timestamp` / `X-Nonce`, comparación en tiempo constante
  y ventana de frescura `webhook_signature_max_skew_s`. Incluye el
  `verify_webhook()` reutilizable (lo consumirá el verificador inbound del
  Plan 13).
- ✅ **`task_10_13`** — **Reintentos exponenciales + DLQ + reintento
  manual**: backoff exponencial con jitter (`max_retries`,
  `retry_base_backoff_s`, `retry_max_backoff_s`, `retry_jitter`); agotados,
  el envío cae a `status=dead_letter` + stream `dlq:notifications`. Endpoint
  `POST /notifications/logs/{id}/retry` (Tenant Admin) re-encola un envío
  dead-letter por el camino normal: flip atómico fuera de `dead_letter`
  (idempotencia → 409 al doble-click), nueva fila append-only `attempt+1` y
  audit `notification.retry` en la misma transacción; solo un log
  dead-letter es reintentable (no-DLQ → 409; cross-tenant → 404).

### Fase D — Asistente Personal y UI

- ✅ **`task_10_14`** — **Asistente personal conversacional** dentro de
  `api-server` (ADR 0033), reutilizando la fontanería de chat (LangGraph) +
  `shared-llm`. Paquete `api_server.assistant` (`config.py`, `tools.py`,
  `graph.py`, `llm.py`) + router `routers/assistant.py`
  (`POST /assistant/chat`, `GET/PUT /assistant/identity`). **Acceso
  doblemente verjado** (`require_assistant_access` = `require_tenant_admin`
  - toggle): un `tenant_user` recibe 403; un Tenant Admin con el toggle en
    false recibe 403. **Toggle** `Organization.personal_assistant_enabled`
    (**migración 0047**, default false); **identidad** por tenant (nombre,
    avatar, tono, idioma es|en, override de system*prompt, lista de tools
    habilitadas) como blob JSONB en `tenant_settings` (categoría `assistant`,
    sin migración). **Tools de lectura cross-proyecto** (`tenant_projects*
status`, `tenant_plans_summary`, `tenant_recent_activity`,
    `tenant_budget_status`) sobre la sesión RLS-bound del request — nunca
    devuelven datos de otro tenant. `tenant_budget_status` es un **marcador
    tipado "no disponible todavía"** (el motor de presupuesto es el Plan 11,
    §28.7), nunca cifras inventadas. El modelo se inyecta por dependencia
    (`get_assistant_model`), sobreescrita en tests por un
    `ScriptedAssistantModel`; el factory por defecto devuelve 503 hasta que se
    cablee un proveedor real.
- ✅ **`task_10_15`** — **UI de configuración de canales en 3 capas**
  (admin-panel) sobre los endpoints `/notifications/platform/channel-types`
  (System Admin habilita transportes), `/notifications/channels` y
  `/notifications/preferences` (Tenant Admin). El secreto se envía en claro
  al crear/rotar pero **nunca se devuelve** (la API expone solo `has_secret`
  - `secret_source`). e2e Playwright `notification-config.spec.ts`
    **escrito, no ejecutado** (sin navegador en el entorno).
- ✅ **`task_10_16`** — **Inbox in-app** con histórico de notificaciones:
  `GET /notifications/logs` paginado (filtros status/channel_type/
  event_type/unread_only) con marcador read/unread por usuario (tabla
  `notification_log_reads`, **migración 0048**, recibo per-user idempotente),
  `POST /notifications/logs/{id}/read` y `/logs/read-all`. RLS aísla el
  histórico por tenant; cada Tenant Admin mantiene su propia bandeja. e2e
  Playwright `notification-inbox.spec.ts` **escrito, no ejecutado**.
- ✅ **`task_10_17`** — **Documentación del plan** (este changelog, la
  referencia `docs/04-reference/notifications.md` y la ADR 0034). El acceso
  del asistente ya estaba registrado en la **ADR 0033** (creada en
  `task_10_14`).

## Canales soportados

| Canal    | `channel_type` | Transporte (sin SDK pesado salvo nota)                | Dependencia         |
| -------- | -------------- | ----------------------------------------------------- | ------------------- |
| Telegram | `telegram`     | Bot API `sendMessage` vía `httpx`                     | httpx               |
| Email    | `email`        | SMTP (primario) / SendGrid v3 HTTP (opcional)         | aiosmtplib, httpx   |
| Slack    | `slack`        | Web API `chat.postMessage` + Block Kit vía `httpx`    | httpx (slack_sdk\*) |
| Teams    | `teams`        | Incoming webhook + Adaptive Card vía `httpx`          | httpx               |
| Discord  | `discord`      | Webhook + embeds vía `httpx`                          | httpx               |
| WhatsApp | `whatsapp`     | Cloud (Graph) API, plantilla pre-aprobada vía `httpx` | httpx               |
| SMS      | `sms`          | Twilio REST `Messages.json` (HTTP Basic) vía `httpx`  | httpx               |
| Webhook  | `webhook`      | POST firmado HMAC+nonce+timestamp vía `httpx`         | httpx               |
| In-app   | `in_app`       | No-op: la fila `notification_logs` ES la entrega      | —                   |

> \* `slack_sdk` es dependencia **dev** (pina el contrato de Block Kit /
> introspección); el envío en caliente usa `httpx`, no su cliente aiohttp.

## Endpoints nuevos

| Endpoint                                | Método      | Rol mínimo      |
| --------------------------------------- | ----------- | --------------- |
| `/notifications/platform/channel-types` | GET         | `tenant_member` |
| `/notifications/platform/channel-types` | PUT         | `system_admin`  |
| `/notifications/channels`               | GET, POST   | ver detalle\*   |
| `/notifications/channels/{id}`          | PUT, DELETE | `tenant_admin`  |
| `/notifications/preferences`            | GET, PUT    | ver detalle\*   |
| `/notifications/preferences/{id}`       | DELETE      | `tenant_admin`  |
| `/notifications/logs`                   | GET         | `tenant_member` |
| `/notifications/logs/{id}/read`         | POST        | `tenant_member` |
| `/notifications/logs/read-all`          | POST        | `tenant_member` |
| `/notifications/logs/{id}/retry`        | POST        | `tenant_admin`  |
| `/assistant/chat`                       | POST        | asistente\*\*   |
| `/assistant/identity`                   | GET, PUT    | asistente\*\*   |

> \* `GET` (listar) es `tenant_member`; la escritura (`POST channels`, `PUT
preferences`) es `tenant_admin`. \*\* Los endpoints `/assistant/*` exigen
> `require_assistant_access` = `tenant_admin` **+** toggle
> `personal_assistant_enabled` ON. Detalle completo (forma de request/
> response, RBAC, RLS y notas de seguridad) en
> [`docs/04-reference/notifications.md`](../04-reference/notifications.md).

## Migraciones (todas reversibles, single head)

| Revisión | Contenido                                                                                                                                                           |
| -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **0045** | `notification_channels` / `notification_preferences` (híbridos) + `notification_logs` (append-only) + RLS + CHECK "como mucho un secreto" / coherencia scope↔tenant |
| **0046** | `notification_templates` (override por tenant, tenant-owned + RLS)                                                                                                  |
| **0047** | `organizations.personal_assistant_enabled` (boolean, server_default `false`)                                                                                        |
| **0048** | `notification_log_reads` (recibo de lectura per-user del inbox, tenant-owned + RLS)                                                                                 |

Single head `0048_notification_log_reads`. El objetivo de downgrade para
probar el rollback completo del plan es la revisión pre-Plan-10
`0040_sso_email_domains` (también el ancla del Plan 09). La identidad del
asistente NO añade migración: va como blob JSONB en `tenant_settings`.

## Configuración / variables / dependencias nuevas

| Item                                 | Tipo      | Para qué                                                                                                                                                                                                                                                                                                                                                       |
| ------------------------------------ | --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `notification-dispatcher`            | app nueva | Servicio Celery dedicado que entrega notificaciones y escribe `notification_logs`                                                                                                                                                                                                                                                                              |
| `aiosmtplib>=3.0,<4`                 | dep       | Transporte SMTP async del canal Email (ruta primaria)                                                                                                                                                                                                                                                                                                          |
| `slack_sdk>=3.27,<4`                 | dev-dep   | Pina el contrato de Block Kit del canal Slack (el envío en caliente es httpx)                                                                                                                                                                                                                                                                                  |
| `httpx>=0.27,<1`                     | dep       | Transporte HTTP común de TODOS los canales API (Telegram/WhatsApp/SMS/SendGrid/Teams/Discord/webhook)                                                                                                                                                                                                                                                          |
| `jinja2>=3.1,<4`                     | dep       | Render SANDBOXED de plantillas (nunca el `Environment` por defecto)                                                                                                                                                                                                                                                                                            |
| `cryptography>=42,<49`               | dep       | Fernet-at-rest de secretos de canal + HMAC-SHA256 de webhooks                                                                                                                                                                                                                                                                                                  |
| `NOTIFY_*`                           | env vars  | Tunables del dispatcher: `NOTIFY_DEFAULT_QUEUE`, `NOTIFY_MAX_RETRIES`, `NOTIFY_RETRY_BASE_BACKOFF_S`, `NOTIFY_RETRY_MAX_BACKOFF_S`, `NOTIFY_RETRY_JITTER`, `NOTIFY_DEAD_LETTER_STREAM`, `NOTIFY_CHANNEL_SEND_TIMEOUT_S`, `NOTIFY_WEBHOOK_SIGNATURE_MAX_SKEW_S`, los `*_API_BASE_URL` / `*_REQUEST_TIMEOUT_S` por canal, … (ninguno es un número mágico inline) |
| `NOTIFY_NOTIFICATION_ENCRYPTION_KEY` | secreto   | Clave del cifrado Fernet-at-rest de secretos de canal (== `API_SERVER_NOTIFICATION_ENCRYPTION_KEY` para que lo que cifra la api-server lo descifre el dispatcher). El `model_validator` rechaza el default `dev-only` en staging/prod                                                                                                                          |

`pydantic-settings`, `redis` y `celery[redis]` ya eran del stack.

## Decisiones

- **Notification-dispatcher centralizado + `ChannelAdapter` Protocol +
  modelo de 3 capas.** Un servicio Celery dedicado orquesta todos los
  envíos; cada transporte implementa el mismo `ChannelAdapter` Protocol;
  canales y preferencias son híbridos plataforma/tenant/usuario (resolución
  más-específica-gana). Registrado en **ADR 0034**.
- **Convención sin SDK pesado: API HTTP del proveedor con `httpx`.** Salvo
  el Email primario (SMTP/`aiosmtplib`), cada canal habla la API HTTP
  documentada del proveedor con `httpx`, no su SDK vendor. Mantiene
  uniforme la ruta async-Celery y permite inyectar `httpx.MockTransport` en
  tests (sin red real). Registrado en **ADR 0034**.
- **Webhooks salientes firmados HMAC-SHA256 + nonce + timestamp.** El
  timestamp y el nonce se pliegan dentro del material firmado
  (tamper-evidence); el timestamp acota frescura (anti-replay diferido), el
  nonce acota uso único dentro de la ventana; comparación en tiempo
  constante. Registrado en **ADR 0034**.
- **Secretos de canal nunca en claro.** Vault (`secret_ref`) o Fernet-at-
  rest (`secret_encrypted`), exactamente uno; resueltos en memoria al
  enviar, nunca logueados, nunca devueltos por la API (mismo precedente
  SSO/marketplace). Registrado en **ADR 0034**.
- **Asistente personal: solo Tenant Admin + toggle default false +
  aislamiento por construcción.** Acceso doblemente verjado (rol + toggle);
  tools de lectura sobre la sesión RLS-bound del request, así que nunca ven
  otro tenant; reutiliza el chat de Plan 03 y `shared-llm` en `api-server`,
  no un stack nuevo. Registrado en **ADR 0033** (creada en `task_10_14`).
- **Reintentos exponenciales + dead-letter queue.** Backoff exponencial con
  jitter acotado por `max_retries`; agotado, `status=dead_letter` + stream
  DLQ; reintento manual idempotente desde la UI con audit append-only.

## Pendiente

- **e2e Playwright** — `notification-config.spec.ts`,
  `notification-inbox.spec.ts` y `personal-assistant.spec.ts` están
  **escritos pero PENDIENTES DE VERIFICACIÓN HUMANA**: el runtime
  node-playwright de este entorno no tiene navegador. El typecheck/lint/build
  del admin-panel sí pasan y el backend está cubierto por pytest.
- **Resolución Vault en el dispatcher** — el camino `secret_ref` (Vault)
  está dejado como hook: el dispatcher aún no empaqueta el `VaultResolver`
  de `shared-mcp`, así que un `secret_ref` lanza un error claro. El camino
  **Fernet-at-rest funciona hoy**, de modo que el default cifrado-en-reposo
  opera sin Vault.
- **`tenant_budget_status` es un stub tipado** hasta el Plan 11 (motor de
  presupuesto, §28.7): devuelve `available: false` en vez de cifras
  inventadas.
- **Proveedor LLM real del asistente** — `get_assistant_model` devuelve 503
  hasta que se cablee un proveedor; los tests usan `ScriptedAssistantModel`.

## Tests humanos pendientes

Los `human_10_01`…`human_10_04` (notificaciones por Telegram, preferencias
granulares, webhooks salientes con firma + anti-replay + DLQ, y el asistente
respondiendo queries cross-proyecto con contexto) quedan **pendientes de
ejecutar por un humano** antes de pasar el plan a `completed`.

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier) ✅ por tarea.
- Suite completa de notificaciones + asistente en verde:

  ```bash
  pytest tests/unit/test_notification_models.py tests/unit/test_templates.py
  pytest tests/integration/test_dispatcher.py tests/integration/test_event_mapping.py \
    tests/integration/test_channel_telegram.py tests/integration/test_channel_email.py \
    tests/integration/test_channel_slack.py tests/integration/test_channel_teams.py \
    tests/integration/test_channel_discord.py tests/integration/test_channel_whatsapp.py \
    tests/integration/test_channel_sms.py tests/integration/test_outbound_webhooks.py \
    tests/integration/test_retries_dlq.py tests/integration/test_personal_assistant.py
  ```

  (incl. las regresiones `@pytest.mark.cross_tenant` del acceso al asistente
  y del aislamiento de sus tools de lectura).

- Migraciones 0045..0048 reversibles (up/down/up) con single head; downgrade
  completo del plan a `0040_sso_email_domains`.
- admin-panel: `npm run typecheck && lint && build` ✅; e2e Playwright
  **pendiente de verificación humana**.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
