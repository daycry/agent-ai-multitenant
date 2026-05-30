---
title: Notificaciones multicanal y asistente personal — Referencia de endpoints y seguridad
audience: backend-dev, architect, security
phase: 10-asistente-personal
updated: 2026-05-30
---

# Notificaciones multicanal y asistente personal — Referencia

Esta página documenta el Plan 10: los canales de notificación, el modelo de
configuración en tres capas (plataforma/tenant/usuario), los endpoints con su
RBAC, las garantías de seguridad (RLS, secretos cifrados en reposo, firma de
webhooks salientes), los tunables `NOTIFY_*` del dispatcher y las reglas de
acceso del asistente personal. Para la matriz de roles general ver
[`rbac.md`](./rbac.md); para los ADRs de fondo ver
[ADR 0034](../05-architecture-decisions/0034-notificaciones-dispatcher-channeladapter-tres-capas-webhooks-firmados.md)
(notificaciones),
[ADR 0033](../05-architecture-decisions/0033-personal-assistant-en-api-server-reutilizando-chat.md)
(asistente) y [ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md)
(RLS).

## Modelo de datos (resumen)

| Tabla                                      | Tenancy                                                                                  |
| ------------------------------------------ | ---------------------------------------------------------------------------------------- |
| `notification_channels`                    | **Híbrida** por `scope`: `platform` → `tenant_id` NULL; `tenant`/`user` → NOT NULL (RLS) |
| `notification_preferences`                 | **Híbrida** por `scope`, mismo modelo que los canales                                    |
| `notification_logs`                        | Tenant-owned, **append-only** (`tenant_id` nullable: un envío de plataforma se registra) |
| `notification_templates`                   | Tenant-owned (RLS): override de una plantilla builtin (en código)                        |
| `notification_log_reads`                   | Tenant-owned (RLS): recibo de lectura **per-user** del inbox in-app                      |
| `organizations.personal_assistant_enabled` | Columna booleana (default `false`): toggle del asistente por tenant                      |

## Modelo de tres capas (plataforma → tenant → usuario)

La configuración de canales y preferencias sigue un modelo de **tres capas**
sobre un discriminador `scope` (ADR 0034 §1, mismo patrón híbrido que
`marketplace_listings`):

| Capa       | `scope`    | `tenant_id`                    | Quién la configura                        |
| ---------- | ---------- | ------------------------------ | ----------------------------------------- |
| Plataforma | `platform` | NULL                           | System Admin (tenant-agnóstica)           |
| Tenant     | `tenant`   | NOT NULL                       | Tenant Admin (visible a todos sus admins) |
| Usuario    | `user`     | NOT NULL (`owner_user_id` set) | el propio Tenant Admin individual         |

- El System Admin define qué **transportes están habilitados globalmente**
  (`platform_settings`, clave `notification_enabled_channel_types`); un tenant
  solo puede configurar un canal cuyo transporte esté en esa lista (409 si no).
- Las **preferencias** enrutan `(event_type, channel_type)` opt-in/opt-out con
  ventana de quiet-hours. El dispatcher resuelve la preferencia efectiva
  **más-específica-gana** (usuario → tenant → plataforma) — el primitivo
  detrás del "silenciar `budget_alert` en Slack pero mantenerlo en email".

## Canales soportados

| Canal    | `channel_type` | Transporte                                                  |
| -------- | -------------- | ----------------------------------------------------------- |
| Telegram | `telegram`     | Bot API `sendMessage` (httpx)                               |
| Email    | `email`        | SMTP primario (`aiosmtplib`) / SendGrid v3 opcional (httpx) |
| Slack    | `slack`        | Web API `chat.postMessage` + Block Kit (httpx)              |
| Teams    | `teams`        | Incoming webhook + Adaptive Card (httpx)                    |
| Discord  | `discord`      | Webhook + embeds (httpx)                                    |
| WhatsApp | `whatsapp`     | Cloud (Graph) API, plantilla pre-aprobada (httpx)           |
| SMS      | `sms`          | Twilio REST `Messages.json` (httpx, HTTP Basic)             |
| Webhook  | `webhook`      | POST firmado HMAC+nonce+timestamp (httpx)                   |
| In-app   | `in_app`       | No-op: la fila `notification_logs` ES la entrega            |

Salvo el Email primario (SMTP), cada canal habla la **API HTTP documentada del
proveedor con `httpx`** (sin SDK pesado, ADR 0034 §2). Las URLs base y timeouts
son tunables `NOTIFY_*`.

## Garantías de seguridad transversales

- **RLS por tenant.** Un canal/preferencia `tenant`/`user` (NOT NULL) está
  aislado; un tenant NUNCA ve ni muta los de otro (404 limpio). Las filas
  `platform` (`tenant_id` NULL) son visibles por una política `FOR SELECT` y
  solo escribibles por roles BYPASSRLS (System Admin). El histórico
  (`notification_logs`) y el inbox per-user están RLS-scoped al tenant del
  caller.
- **Dispatcher BYPASSRLS valida en el límite de la tarea.** El dispatcher
  entrega cross-tenant, así que RLS no puede atrapar un payload de Celery
  manipulado: valida `row.tenant_id == request.tenant_id` antes de enviar y de
  escribir el log.
- **Secretos cifrados en reposo, nunca en claro.** El secreto de un canal
  (bot token, password SMTP, auth token Twilio, clave de firma de webhook…)
  vive en **exactamente una** forma never-plaintext (CHECK "como mucho uno"):
  `secret_ref` (puntero Vault) o `secret_encrypted` (Fernet-at-rest, clave
  derivada de `NOTIFY_NOTIFICATION_ENCRYPTION_KEY`). Se resuelve a texto plano
  **en memoria** al enviar, nunca se loguea, nunca aterriza en `config`, y la
  API **nunca lo devuelve** (solo `has_secret` + `secret_source`). El camino
  Vault es un hook aún no cableado en el dispatcher; el Fernet-at-rest funciona
  hoy.
- **Firma de webhooks salientes (HMAC-SHA256 + nonce + timestamp).** Cada POST
  se firma con `HMAC-SHA256(secret, ts + "." + nonce + "." + body)` y envía
  `X-Signature` / `X-Timestamp` / `X-Nonce`. El timestamp acota frescura
  (ventana `NOTIFY_WEBHOOK_SIGNATURE_MAX_SKEW_S`); el nonce acota uso único; la
  verificación compara en tiempo constante. Helper `verify_webhook()`
  reutilizable (lo usará el inbound del Plan 13).
- **Log append-only.** Un reintento escribe una NUEVA fila (`attempt+1`), nunca
  muta la anterior; el reintento manual desde la UI escribe un audit
  `notification.retry` en la misma transacción.

## Endpoints — configuración de canales y preferencias (task_10_15)

| Endpoint                                | Método      | Rol mínimo      |
| --------------------------------------- | ----------- | --------------- |
| `/notifications/platform/channel-types` | GET         | `tenant_member` |
| `/notifications/platform/channel-types` | PUT         | `system_admin`  |
| `/notifications/channels`               | GET         | `tenant_member` |
| `/notifications/channels`               | POST        | `tenant_admin`  |
| `/notifications/channels/{id}`          | PUT, DELETE | `tenant_admin`  |
| `/notifications/preferences`            | GET         | `tenant_member` |
| `/notifications/preferences`            | PUT         | `tenant_admin`  |
| `/notifications/preferences/{id}`       | DELETE      | `tenant_admin`  |

- **`GET/PUT /platform/channel-types`** — lista de transportes habilitados
  globalmente. La lectura la hace cualquier miembro (para que la UI sepa qué
  puede configurar un admin); la escritura es System Admin (sesión BYPASSRLS).
  Sin lista, todos los transportes del catálogo cuentan como habilitados
  (default permisivo que el System Admin estrecha).
- **`POST /channels`** — crea un canal `tenant`/`user` (Tenant Admin). El
  transporte debe estar habilitado platform-wide (409 si no). El `secret` en
  claro se cifra en reposo antes de tocar la BD y **no se devuelve**; un canal
  `user` queda en propiedad del admin que lo crea.
- **`PUT /channels/{id}`** — patch; un `secret` no vacío **rota** la clave
  (re-cifrada); omitirlo conserva la actual.
- **`PUT /preferences`** — upsert de una regla de enrutado keyed en
  `(tenant, owner, event_type, channel_type)`.

## Endpoints — inbox in-app (task_10_16)

| Endpoint                        | Método | Rol mínimo      |
| ------------------------------- | ------ | --------------- |
| `/notifications/logs`           | GET    | `tenant_member` |
| `/notifications/logs/{id}/read` | POST   | `tenant_member` |
| `/notifications/logs/read-all`  | POST   | `tenant_member` |

- **`GET /logs`** — histórico del tenant, más nuevo primero, paginado
  (`limit` 1..200, `offset` ≥0), con filtros opcionales `status` /
  `channel_type` / `event_type` / `unread_only` y un marcador read/unread
  **por usuario** (left-join a `notification_log_reads` del caller). Cada
  Tenant Admin mantiene una bandeja independiente. Ningún secreto se expone
  (un log solo lleva el `target` no-secreto + metadatos de transporte).
- **`POST /logs/{id}/read`** y **`/logs/read-all`** — marcan leído de forma
  idempotente (ON CONFLICT DO NOTHING). Un log de otro tenant es 404 (RLS).

## Endpoints — dead-letter queue y reintento manual (task_10_13)

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/notifications/logs/{id}/retry` | POST   | `tenant_admin` |

Re-encola un envío **dead-letter** por el camino normal del dispatcher. RLS
scopea el SELECT al tenant (otro tenant → 404); solo un log `dead_letter` es
reintentable (no-DLQ → 409); un doble-click pierde la carrera del flip atómico
fuera de `dead_letter` → 409 (idempotencia). Escribe una fila append-only
`attempt+1` + un audit `notification.retry` en la misma transacción, y publica
en la lane `notifications.default`. La api-server **no importa** el paquete del
dispatcher: re-encola por nombre de tarea sobre el broker compartido.

## Asistente personal (task_10_14)

| Endpoint              | Método   | Acceso                     |
| --------------------- | -------- | -------------------------- |
| `/assistant/chat`     | POST     | `require_assistant_access` |
| `/assistant/identity` | GET, PUT | `require_assistant_access` |

### Reglas de acceso (vinculantes)

- **Solo Tenant Admin.** `require_assistant_access` = `require_tenant_admin`
  (un `tenant_user` / member recibe **403**) **+** comprobación del toggle.
- **Toggle por tenant, default false.** `Organization.personal_assistant_enabled`
  por defecto está en `false`; apagado, **incluso un Tenant Admin recibe 403**
  ("disabled"). Solo un admin que lo encienda habilita el asistente para su
  tenant.
- **Aislamiento por construcción.** Las tools de lectura cross-proyecto corren
  sobre la **sesión RLS-bound del request**, así que PostgreSQL filtra cada
  query al tenant del admin que pregunta — una tool **nunca** devuelve datos de
  otro tenant ni más de lo que el admin puede ver, sea cual sea el argumento
  que el modelo invente. Las tools son **READ-ONLY** (solo `SELECT`).

### Identidad por tenant

Personalizable (nombre, avatar, tono, idioma **es|en**, override de
`system_prompt`, lista de tools habilitadas) como blob JSONB en
`tenant_settings` (categoría `assistant`), sin migración. El idioma fuera de
`{es, en}` cae al default; un nombre de tool desconocido se intersecta con el
catálogo (no puede ensanchar la superficie).

### Tools de lectura cross-proyecto

| Tool                     | Devuelve                                                                                                     |
| ------------------------ | ------------------------------------------------------------------------------------------------------------ |
| `tenant_projects_status` | Conteo total + estado por proyecto del tenant                                                                |
| `tenant_plans_summary`   | Planes cross-proyecto por estado, incl. pendientes de aprobación                                             |
| `tenant_recent_activity` | Tareas no terminales más recientes + total de tareas abiertas                                                |
| `tenant_budget_status`   | **Marcador tipado "no disponible"** — el motor de presupuesto es el Plan 11 (§28.7); nunca cifras inventadas |

El LLM se inyecta por dependencia (`get_assistant_model`); el factory por
defecto devuelve **503** hasta cablear un proveedor real (los tests usan un
`ScriptedAssistantModel`, sin contactar ningún proveedor). El asistente reutiliza
la infraestructura de chat (LangGraph) y `shared-llm` del Plan 03 — **no es un
stack LLM nuevo** (ADR 0033).

## Tunables `NOTIFY_*` del dispatcher

Toda perilla operativa vive en la config (prefijo `NOTIFY_`, pydantic-settings);
ningún número mágico inline. Las principales:

| Variable                                               | Para qué                                                                                                                                                |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NOTIFY_DEFAULT_QUEUE` / `NOTIFY_PRIORITY_QUEUE`       | Topología de colas Celery (lane común / lane de alertas)                                                                                                |
| `NOTIFY_MAX_RETRIES`                                   | Reintentos automáticos antes del dead-letter (default 5)                                                                                                |
| `NOTIFY_RETRY_BASE_BACKOFF_S`                          | Base del backoff exponencial (default 2 s)                                                                                                              |
| `NOTIFY_RETRY_MAX_BACKOFF_S`                           | Clamp superior de un backoff (default 600 s)                                                                                                            |
| `NOTIFY_RETRY_JITTER`                                  | Fracción de full-jitter [0..1] (default 0.5)                                                                                                            |
| `NOTIFY_DEAD_LETTER_STREAM` / `_MAXLEN`                | Stream DLQ Redis (`dlq:notifications`) + su cap                                                                                                         |
| `NOTIFY_CHANNEL_SEND_TIMEOUT_S`                        | Presupuesto wall-clock por envío de canal                                                                                                               |
| `NOTIFY_WEBHOOK_SIGNATURE_MAX_SKEW_S`                  | Ventana de frescura de la firma del webhook (anti-replay, 300 s)                                                                                        |
| `NOTIFY_*_API_BASE_URL` / `NOTIFY_*_REQUEST_TIMEOUT_S` | URL base + timeout por canal (Telegram/Slack/Teams/Discord/WhatsApp/Twilio/SendGrid/webhook)                                                            |
| `NOTIFY_NOTIFICATION_ENCRYPTION_KEY`                   | **Secreto**: clave del Fernet-at-rest (== `API_SERVER_NOTIFICATION_ENCRYPTION_KEY`). El `model_validator` rechaza el default `dev-only` en staging/prod |

## Migraciones

| Revisión | Contenido                                                             |
| -------- | --------------------------------------------------------------------- |
| **0045** | Canales / preferencias (híbridos) + logs (append-only) + RLS + CHECKs |
| **0046** | `notification_templates` (override por tenant, RLS)                   |
| **0047** | `organizations.personal_assistant_enabled` (boolean, default false)   |
| **0048** | `notification_log_reads` (recibo de lectura per-user del inbox, RLS)  |

Single head `0048_notification_log_reads`; downgrade completo del plan a
`0040_sso_email_domains`.
