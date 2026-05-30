---
adr: "0034"
title: Notificaciones — dispatcher centralizado, ChannelAdapter Protocol, modelo de 3 capas, canales sobre HTTP (sin SDK pesado) y webhooks firmados HMAC+nonce+timestamp
status: accepted
date: 2026-05-30
deciders: System Architect, Security
phase: 10-asistente-personal
---

# ADR 0034 — Notificaciones: dispatcher centralizado, ChannelAdapter Protocol, modelo de 3 capas, canales sobre HTTP y webhooks firmados

> **Estado: `accepted`.** Recoge cuatro decisiones arquitectónicas tomadas
> durante el Plan 10 que no estaban registradas en un ADR previo: el
> **servicio notification-dispatcher centralizado** con un **`ChannelAdapter`
> Protocol** y el **modelo de tres capas** plataforma/tenant/usuario; la
> convención de **canales sobre la API HTTP del proveedor con `httpx` (sin
> SDK pesado)**; la firma de **webhooks salientes con HMAC-SHA256 + nonce +
> timestamp** anti-replay; y el contrato de **secretos de canal nunca en
> claro** (Vault o Fernet-at-rest). El acceso del **asistente personal** —
> solo Tenant Admin + toggle default false — se registra aparte en la
> **ADR 0033**.

## Contexto

Hasta el Plan 10 las notificaciones eran solo in-app. El plan abre el sistema
a Telegram, Email, Slack, Teams, Discord, WhatsApp, SMS y webhooks salientes.
Varias cuestiones de diseño no quedaban cerradas por ADRs previos:

1. **¿Dónde y cómo se entrega una notificación?** Hay múltiples productores
   de eventos (orchestrator, workers, api-server) y múltiples transportes con
   contratos muy distintos. La pregunta es si cada productor llama al
   transporte directamente o si hay un punto único de entrega.

2. **¿Cómo se modela la configuración de canales y preferencias?** El sistema
   es multi-tenant con RLS desde el día uno (ADR 0001) e introduce
   configuración a nivel de plataforma (System Admin), de tenant y de usuario
   (Tenant Admin individual). Había que decidir cómo conviven esas tres capas
   con la frontera de tenant.

3. **¿Con qué cliente se habla cada canal?** Cada proveedor (Telegram, Slack,
   Twilio, Meta, SendGrid…) publica un SDK propio, muchos con dependencias
   pesadas (aiohttp, grpc) que chocarían con la ruta async-Celery del envío.

4. **¿Cómo se autentica un webhook saliente y se evita su replay?**

5. **¿Cómo se guardan los secretos de canal** (bot tokens, passwords SMTP,
   auth tokens Twilio, claves de firma de webhook) sin violar el principio
   "ningún secreto en claro en la BD" (CLAUDE.md)?

## Decisión

### 1. Dispatcher centralizado + `ChannelAdapter` Protocol + modelo de 3 capas

Un **servicio `notification-dispatcher`** (Celery dedicado, colas
`notifications.default` / `notifications.priority`) es el **único punto de
entrega**: los productores publican un envío al broker; el dispatcher resuelve
canal + plantilla + preferencias, llama al adaptador y escribe el
`notification_logs`. Cada transporte implementa el mismo **`ChannelAdapter`
Protocol** (`channel_type` + `async send(ChannelMessage) -> DeliveryResult`),
registrado en un registry que el dispatcher resuelve por `channel_type`; el
adaptador `in_app` no-op cierra la ruta de envío desde la Fase A.

La configuración sigue un **modelo de tres capas** plataforma → tenant →
usuario, **híbrido sobre un discriminador `scope`** (mismo patrón que el
catálogo `marketplace_listings`, ADR 0032):

- `scope='platform'` → `tenant_id` NULL: canal/preferencia tenant-agnóstico
  del System Admin (como `platform_settings`). RLS lo expone por una política
  `FOR SELECT` y solo lo escribe un rol BYPASSRLS.
- `scope='tenant'` / `scope='user'` → `tenant_id` NOT NULL: propio del tenant,
  aislado por la política `FOR ALL` de RLS exactamente como toda tabla tenant.

Las preferencias se resuelven **más-específica-gana** (usuario → tenant →
plataforma). El dispatcher es **BYPASSRLS** (entrega cross-tenant), así que
**valida `row.tenant_id == request.tenant_id` en el límite de la tarea
Celery**, porque RLS no puede atrapar un payload de Celery manipulado.

### 2. Canales sobre la API HTTP del proveedor con `httpx` (sin SDK pesado)

Salvo el Email primario (SMTP con `aiosmtplib`), **cada canal habla la API
HTTP documentada del proveedor con `httpx`**, no su SDK vendor: Telegram Bot
API, Slack Web API (`chat.postMessage` + Block Kit), Teams/Discord incoming
webhooks, WhatsApp Cloud/Graph API, Twilio REST (`Messages.json`), SendGrid v3
(opcional). Esto mantiene **uniforme** la ruta async-Celery, evita arrastrar
SDKs con dependencias pesadas/transitivas, y permite inyectar un
`httpx.MockTransport` en tests para que **ningún adaptador toque la red real**.
`slack_sdk` se conserva como dependencia **dev** solo para pinar el contrato de
Block Kit. Todas las URLs base y timeouts son tunables `NOTIFY_*`, nunca
números mágicos inline.

### 3. Webhooks salientes firmados HMAC-SHA256 + nonce + timestamp

Cada POST saliente se firma con
`HMAC-SHA256(secret, timestamp + "." + nonce + "." + body)` (hex), enviando
`X-Signature` / `X-Timestamp` / `X-Nonce`. El timestamp y el nonce se pliegan
**dentro** del material firmado (tamper-evidence). El timestamp acota
**frescura** (una ventana `max_skew_s` configurable, anti-replay diferido); el
nonce acota **uso único** dentro de esa ventana (el receptor recuerda nonces
vistos). La verificación recomputa el MAC y compara en **tiempo constante**
(`hmac.compare_digest`). El helper `verify_webhook()` es reutilizable y será el
check exacto del verificador inbound del Plan 13.

### 4. Secretos de canal nunca en claro (Vault o Fernet-at-rest)

El secreto de un canal vive en **exactamente una** de dos formas
never-plaintext (un CHECK lo garantiza): `secret_ref` (puntero Vault) o
`secret_encrypted` (Fernet-at-rest, clave derivada de
`NOTIFY_NOTIFICATION_ENCRYPTION_KEY`). Se resuelve a texto plano **en memoria**
en el momento del envío, nunca se loguea, nunca aterriza en `config` y la API
nunca lo devuelve (solo `has_secret` + `secret_source`). Mismo precedente que
SSO (ADR 0031) y marketplace (ADR 0032).

## Alternativas consideradas

- **Cada productor llama al transporte directamente.** Duplicaría la
  resolución de preferencias/plantillas, la política de reintentos/DLQ y el
  logueo en cada productor, y acoplaría el orchestrator/workers a cada SDK de
  canal. Descartado a favor del dispatcher único.
- **Tablas separadas por capa (una para plataforma, otra para tenant).**
  Triplicaría el esquema y la lógica de resolución. El discriminador `scope`
  con `tenant_id` NULLABLE reusa el patrón híbrido ya probado del marketplace.
- **SDK vendor por canal.** Dependencias pesadas (aiohttp/grpc) que chocan con
  la ruta async-Celery y dificultan el mock en tests. Descartado salvo donde un
  protocolo lo exige (SMTP con `aiosmtplib`).
- **Enviar timestamp/nonce solo en cabeceras (fuera de la firma).** No serían
  tamper-evidentes. Descartado: deben firmarse junto al body.

## Consecuencias

- Un canal nuevo es un `ChannelAdapter` más en el registry + (si lleva
  secreto) el contrato Vault/Fernet ya definido — sin tocar productores ni el
  esquema. Ampliar el catálogo de transportes pide solo un miembro en
  `NotificationChannelType` + su adaptador.
- El dispatcher hereda una superficie de envío testeable de punta a punta
  (httpx mockeado) y una política de reintentos/DLQ centralizada.
- La **resolución Vault** queda como hook en el dispatcher (no empaqueta aún el
  `VaultResolver` de `shared-mcp`); el camino **Fernet-at-rest funciona hoy**,
  así que el default cifrado-en-reposo opera sin Vault. Cablear Vault es un
  follow-up que no cambia el contrato.
- El receptor de un webhook saliente puede autenticar y rechazar replays con
  el `verify_webhook()` reutilizable; el Plan 13 (webhooks entrantes) usará el
  mismo helper.
