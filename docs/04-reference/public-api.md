---
title: API pública v1, tokens y webhooks entrantes — Referencia
audience: backend-dev, architect, security, integrator
phase: 13-api-publica-webhooks
updated: 2026-05-30
---

# API pública v1, tokens y webhooks entrantes — Referencia

Esta página documenta la superficie pública del Plan 13: la **API REST v1**
(`/api/v1`) con su autenticación por `X-API-Token`, scope y rate limit; el
**modelo de token** y su gestión; los **webhooks entrantes** (orígenes
soportados + seguridad HMAC); y los **SDKs oficiales** Python y TypeScript. Para
la matriz de roles general ver [`rbac.md`](./rbac.md); para el ADR de fondo ver
[ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md),
y para el firmado HMAC reusado [ADR 0034](../05-architecture-decisions/0034-notificaciones-dispatcher-channeladapter-tres-capas-webhooks-firmados.md)
y [ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md).
Para una guía paso a paso ver [`../03-guides/api-publica-y-webhooks.md`](../03-guides/api-publica-y-webhooks.md).

## El modelo de token (`api_tokens`)

| Campo          | Para qué                                                                        |
| -------------- | ------------------------------------------------------------------------------- |
| `token_hash`   | **Solo** el digest SHA-256 del token crudo (UNIQUE). El token nunca se persiste |
| `prefix`       | Segmento claro inicial, para desambiguar en listados (nunca revela el secreto)  |
| `scopes`       | `["read"]` y/o `["write"]`. GET pide `read`, POST pide `write`                  |
| `expires_at`   | Vigencia opcional. Un token expirado no autentica nada                          |
| `rate_limit`   | Budget por minuto del token (override del default de plataforma)                |
| `ip_allowlist` | Lista opcional de IPs/CIDR desde las que el token es válido                     |
| `revoked_at`   | Soft-revoke. La fila queda para auditoría; un token revocado no autentica       |

- **Tenant-owned** (`tenant_id` NOT NULL + política FOR ALL de RLS): un Tenant
  Admin gestiona **solo** los tokens de su tenant.
- El token claro se devuelve **exactamente una vez** al acuñar; no se puede
  recuperar (perderlo obliga a re-acuñar). Mismo precedente que SCIM (ADR 0031) y
  marketplace (ADR 0032).
- Migración: `0054_api_tokens`.

### Gestión del token (JWT + `tenant_admin`)

| Endpoint                      | Método | Rol mínimo     | Para qué                                  |
| ----------------------------- | ------ | -------------- | ----------------------------------------- |
| `/auth/api-tokens`            | GET    | `tenant_admin` | Listar tokens (nunca el secreto)          |
| `/auth/api-tokens`            | POST   | `tenant_admin` | Acuñar (devuelve el claro **1 vez**)      |
| `/auth/api-tokens/{token_id}` | DELETE | `tenant_admin` | Revocar (soft) + invalidar el cache Redis |

## La API v1 (`/api/v1`)

### Autenticación, scope y aislamiento

- Cabecera **`X-API-Token: <token>`** en **toda** request (nunca query param —
  [ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md) §1).
- La resolución token → tenant corre **una vez sobre el rol BYPASSRLS** (cacheada
  en Redis, TTL corto); cada consulta posterior corre sobre el rol de app
  (NOBYPASSRLS) con `app.tenant_id` fijado. **RLS — no el código del endpoint —
  garantiza el aislamiento**: un token de tenant A nunca lee ni escribe filas de B
  (un id ajeno es un **404** limpio).
- **Scope:** GET → `read`, POST → `write`. Un token válido al que le falta el scope
  recibe **403**; un token inválido/ausente recibe **401**. Un `write` **no**
  concede `read` implícitamente.
- **Rate limit:** sliding-window por token en Redis (default 100 req/min), con
  cabeceras `X-RateLimit-*`; sobre presupuesto → **429**.
- **Paginación:** toda lista toma `limit`/`offset` con cotas (`ge`/`le`); una
  respuesta nunca es ilimitada.

### Endpoints

Convención: el scope **mínimo** requerido.

| Endpoint                                        | Método     | Scope                |
| ----------------------------------------------- | ---------- | -------------------- |
| `/api/v1/projects`                              | GET / POST | `read` / `write`     |
| `/api/v1/projects/{project_id}`                 | GET        | `read`               |
| `/api/v1/projects/{project_id}/plans`           | GET / POST | `read` / `write`     |
| `/api/v1/plans/{plan_id}`                       | GET        | `read`               |
| `/api/v1/projects/{project_id}/tasks`           | GET / POST | `read` / `write`     |
| `/api/v1/projects/{project_id}/tasks/{task_id}` | GET        | `read`               |
| `/api/v1/projects/{project_id}/conversations`   | GET / POST | `read` / `write`     |
| `/api/v1/conversations/{conversation_id}`       | GET        | `read`               |
| `/api/v1/kbs`                                   | GET / POST | `read` / `write`     |
| `/api/v1/kbs/{kb_id}`                           | GET        | `read`               |
| `/api/v1/openapi.json`                          | GET        | público (sin auth)   |
| `/api/v1/docs`                                  | GET        | público (Swagger UI) |

La v1 es una **fachada fina** sobre el dominio: reusa modelos ORM + los mismos
schemas de respuesta de los routers interactivos, sin lógica duplicada ni fugas de
campos internos.

### Versionado

- El **path** (`/api/v1`) es la fuente de verdad (más explícito que negociar por
  cabecera — [ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md) §2).
- Cabecera **`X-API-Version`** opcional para fijar/observar: un mismatch con el set
  soportado (`{v1}`) es un **400** limpio; la versión servida se anuncia de vuelta
  en `X-API-Version: v1` en cada respuesta.
- Uso por versión trackeado con un contador diario en Redis
  (`apiusage:v1:<yyyymmdd>`, retención ~10 días) — observabilidad best-effort, sin
  tabla.

### Contrato OpenAPI 3.1

`build_v1_openapi()` produce un documento **autocontenido** solo de las rutas v1,
con `3.1.0` **pineado** y el esquema de seguridad `apiKey`/`X-API-Token`
(`ApiTokenAuth`) inyectado + aplicado globalmente (la dependencia de cabecera de
Fase A es opaca a la generación automática de FastAPI). Se sirve en
`/api/v1/openapi.json` (+ Swagger UI en `/api/v1/docs`); ambos son **públicos** (un
dev lee el contrato antes de tener token). El documento se puede construir **en
proceso** sin servidor vivo — así es como lo consumen los SDKs.

## Webhooks entrantes

La dirección **inversa** del firmado saliente del Plan 10: un tool externo hace
POST de un evento firmado con HMAC y el sistema lo verifica y lo mapea a una acción.

### Orígenes soportados (catálogo cerrado)

| `origin`  | Cabecera de firma                   | Eventos típicos                           |
| --------- | ----------------------------------- | ----------------------------------------- |
| `github`  | `X-Hub-Signature-256: sha256=<hex>` | push, PR review → crear/actualizar tarea  |
| `gitlab`  | `X-Hub-Signature-256: sha256=<hex>` | merge request → crear tarea               |
| `jira`    | `X-Signature-256: <hex>` (bare)     | issue creado → crear tarea                |
| `sentry`  | `X-Signature-256: <hex>` (bare)     | error → crear bug task / escalar          |
| `linear`  | `X-Signature-256: <hex>` (bare)     | issue → crear tarea                       |
| `generic` | `X-Signature-256: <hex>` (bare)     | integración a medida / proxy normalizador |

Todas las firmas son **HMAC-SHA256 sobre el body crudo**, comparadas en **tiempo
constante**. Extender el catálogo = añadir un miembro a `IncomingWebhookOrigin`;
nunca renombrar uno existente (una fila `origin` persistida lo referencia).

### El endpoint público

| Endpoint                                  | Método | Auth                     |
| ----------------------------------------- | ------ | ------------------------ |
| `/webhooks/incoming/{origin}/{config_id}` | POST   | **HMAC** (sin token/JWT) |

**Orden de checks = contrato de seguridad** ([ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md) §3):

1. **Body cap (413)** — antes de leer el body (guarda anti-DDoS; default 1 MiB).
2. **Resolver config (404)** — `config_id` → fila (BYPASSRLS, la request no está
   autenticada aún). Config inexistente / soft-deleted / deshabilitada / con
   `origin` que no casa la URL → 404 (nunca revela si un id existe).
3. **Rate limit por config (429)** — sliding-window keyed por `config_id` (default
   120/min); una config nunca throttlea a otra.
4. **Verificar HMAC (401, SIN acción)** — recomputa el MAC con el secreto por
   proyecto (descifrado en memoria, Fernet at rest) y compara en tiempo constante.
   Firma mala/ausente/manipulada → 401, nada persistido.
5. **Mapear + actuar** — normaliza el payload (plantilla del origen) y resuelve
   `action_mappings` → acción (**crear tarea** / **comentar tarea** / **escalar**),
   ejecutada en la **misma transacción** que registra el evento.
6. **Persistir** — registra el evento (raw body + headers) con UNIQUE parcial
   `(config_id, delivery_id)`: una redelivery colisiona, así que **ni el evento ni
   su acción se reaplican** (idempotente).

### Seguridad de los webhooks

- **`config_id` en la URL, NO el secreto.** El id resuelve a `tenant_id` +
  `project_id`; un evento de proyecto A nunca actúa sobre tenant B. El secreto vive
  **solo cifrado** (Fernet) y se devuelve en claro **una vez** al crear/rotar — una
  URL pública nunca debe llevar un secreto ([ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md) §3).
- **Aislamiento por tenant.** El config es tenant + project scoped (RLS); la acción
  corre bajo `app.tenant_id` del tenant resuelto.
- **Idempotencia + replay auditado.** La redelivery es no-op; el replay
  operador-iniciado re-verifica + re-mapea + re-ejecuta contra el payload almacenado
  y se audita como fila propia (`replayed_from_event_id`). Una firma que ya no
  verifica (secreto rotado) → 422, no re-run silencioso.

### Gestión de configs (JWT + `tenant_admin`)

| Endpoint                                                                            | Método       | Para qué                                      |
| ----------------------------------------------------------------------------------- | ------------ | --------------------------------------------- |
| `/projects/{project_id}/incoming-webhooks`                                          | GET / POST   | Listar / crear (POST → secreto **1 vez**)     |
| `/projects/{project_id}/incoming-webhooks/{config_id}`                              | PUT / DELETE | Editar (name/enabled/mappings) / soft-delete  |
| `/projects/{project_id}/incoming-webhooks/{config_id}/rotate-secret`                | POST         | Rotar el secreto HMAC (nuevo claro **1 vez**) |
| `/projects/{project_id}/incoming-webhooks/{config_id}/deliveries`                   | GET          | Entregas verificadas recientes (metadata)     |
| `/projects/{project_id}/incoming-webhooks/{config_id}/deliveries/{event_id}/replay` | POST         | Replay de una entrega almacenada (debugging)  |

> `origin` es **inmutable** (la URL pública lo embebe); para cambiarlo se crea otra
> config. Migraciones: `0055_incoming_webhooks`, `0056_webhook_action_mappings`,
> `0057_webhook_event_replay`.

## Los SDKs oficiales

Ambos SDKs se **generan DESDE** el OpenAPI v1 construido **en proceso**
(`build_v1_openapi()`, sin servidor vivo) y escrito a `openapi-v1.json`. Patrón:
**modelos generados + cliente fino escrito a mano** que fija `X-API-Token` una vez
y eleva un error tipado (401/403/404/429). El dir generado se **excluye de los
linters** (documentado en cada `README.md`); el **test** de cada SDK no se excluye.

| SDK                       | Paquete                 | Codegen (modelos)                        | Cliente               |
| ------------------------- | ----------------------- | ---------------------------------------- | --------------------- |
| `packages/sdk-python`     | `agentic-platform-sdk`  | `datamodel-code-generator` (Pydantic v2) | `httpx` fino (a mano) |
| `packages/sdk-typescript` | `@agentic-platform/sdk` | `openapi-typescript-codegen` v0.30.0     | `fetch` fino (a mano) |

> Sustituciones de generador documentadas en [ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md) §4
> y en el `README.md` de cada paquete: Python usa `datamodel-code-generator` en
> lugar de `openapi-python-client` (salida Pydantic v2, no `attrs`); TS usa
> `openapi-typescript-codegen` solo para los tipos + cliente a mano (el generador no
> respeta el esquema `apiKey`/`X-API-Token`).

## Tunables relacionados

| Setting                                      | Default           | Para qué                                    |
| -------------------------------------------- | ----------------- | ------------------------------------------- |
| `api_token_default_rate_limit`               | `100`             | Budget/min por token sin override           |
| `api_token_rate_limit_window_seconds`        | `60`              | Ventana del rate limit por token            |
| `api_token_cache_ttl_seconds`                | `30`              | TTL del cache `X-API-Token` → tenant        |
| `incoming_webhook_encryption_key`            | dev-only          | Deriva la clave Fernet del secreto de firma |
| `incoming_webhook_max_body_bytes`            | `1048576` (1 MiB) | Cap del body del webhook entrante (413)     |
| `incoming_webhook_rate_limit`                | `120`             | Budget/min por config del endpoint público  |
| `incoming_webhook_rate_limit_window_seconds` | `60`              | Ventana del rate limit por config           |
