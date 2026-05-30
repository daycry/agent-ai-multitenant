---
plan_id: 13-api-publica-webhooks
title: API Pública, Webhooks Entrantes y Eventos Externos
completed_at: null
docs_language: es
---

# Plan 13 — API Pública, Webhooks Entrantes y Eventos Externos

## Resumen

Conecta la plataforma —hasta ahora una isla— a las herramientas del tenant (CI,
issue trackers, monitoring) con **tres superficies nuevas** y **dos SDKs
oficiales generados desde el contrato**.

La **API REST pública v1** (`/api/v1/...`) es una **fachada fina, scope-checked**
sobre el dominio existente: reusa los modelos ORM y los **mismos schemas Pydantic
de respuesta** que los routers interactivos (`to_project_response`, etc.), sin
lógica de negocio duplicada ni fugas de campos internos. Se autentica
**exclusivamente** con un **`X-API-Token` en la cabecera** (nunca query param —
Decisiones Clave del plan), una credencial **por tenant** acuñada por el Tenant
Admin. El token resuelve a su tenant en una consulta única sobre el rol BYPASSRLS
(la request está sin autenticar hasta casar el hash); cada consulta `/api/v1`
posterior corre sobre el rol de app (NOBYPASSRLS) con `app.tenant_id` fijado al
tenant resuelto, de modo que **RLS garantiza que un token de tenant A nunca lee
ni escribe filas de tenant B**. Los GET piden scope `read`, los POST piden
`write` (403 si falta el scope; 401 si el token es inválido/ausente). Toda lista
es **paginada** (`limit`/`offset` con cotas) y cada token tiene un **rate limit
por sliding-window en Redis** (default 100 req/min) que adjunta cabeceras
`X-RateLimit-*` (429 al exceder). El contrato se publica como **OpenAPI 3.1** en
`/api/v1/openapi.json` (+ Swagger UI en `/api/v1/docs`), con el **esquema de
seguridad `apiKey`/`X-API-Token` (`ApiTokenAuth`)** inyectado a mano porque la
dependencia de cabecera de Fase A es opaca a la generación automática de FastAPI.
El **versionado vive en el path** (`/api/v1`, fuente de verdad); una cabecera
opcional `X-API-Version` permite **fijar/observar** la versión (mismatch → 400
limpio), se **anuncia** de vuelta en cada respuesta y se **trackea** uso por
versión con un contador diario en Redis (sin tabla nueva).

Los **webhooks entrantes** son el inverso del firmado saliente del Plan 10: un
tool externo (GitHub, GitLab, Jira, Sentry, Linear, genérico) hace POST a
`/webhooks/incoming/{origin}/{config_id}` y estampa una **firma HMAC-SHA256**
sobre el body crudo con un secreto compartido; el endpoint la **reverifica** con
el secreto por proyecto y solo acepta el evento ante una **comparación en tiempo
constante**. El endpoint es **PÚBLICO** (la HMAC ES la autenticación), así que el
orden de checks es el contrato de seguridad: **body-cap (413) → resolver config
(404) → rate limit por config (429) → verificar HMAC (401, sin acción) → mapear +
actuar → persistir para replay**. La URL lleva el **`config_id` (no el secreto)**:
el id resuelve a una fila y, a través de ella, a su `tenant_id` + `project_id`, de
modo que un evento de proyecto A nunca puede actuar sobre tenant B. El secreto de
firma se guarda **solo como ciphertext Fernet** y se devuelve en claro **una sola
vez** al crear/rotar. Las **plantillas pre-configuradas** normalizan el payload de
cada origen a un evento canónico; el **mapeo `action_mappings`** (JSONB por config)
decide qué acción del sistema dispara cada tipo de evento (**crear tarea**,
**comentar tarea**, **escalar**) con plantillas de título/cuerpo. El evento se
**persiste** (raw body + headers) con un **UNIQUE parcial `(config_id,
delivery_id)`** que hace la redelivery **idempotente** (ni el evento ni su acción
se reaplican), y el **replay** operador-iniciado re-corre verify+parse+map+action
contra el payload almacenado, auditado como una fila propia
(`replayed_from_event_id`). Una **UI por proyecto** crea/lista/edita/rota/deshabilita
configs y muestra entregas recientes + botón de replay.

Los **SDKs oficiales** se generan **DESDE** ese OpenAPI v1: un script construye el
documento **en proceso** (`build_v1_openapi()`, sin servidor vivo) y lo escribe a
`openapi-v1.json`, que el codegen consume. El **SDK Python**
(`packages/sdk-python`) genera modelos **Pydantic v2** con
`datamodel-code-generator` + un **cliente `httpx` fino** escrito a mano. El **SDK
TypeScript** (`packages/sdk-typescript`) genera los **tipos de modelo** con
`openapi-typescript-codegen` + un **cliente `fetch` fino** escrito a mano. Ambos
fijan la cabecera `X-API-Token` una sola vez y exponen métodos tipados que reflejan
los endpoints v1; el código **generado** se excluye de los linters (documentado) y
cada SDK se verifica con un test que NO se excluye.

Las 15 tareas se desarrollaron en cuatro fases (A — tokens + auth + rate limit;
B — endpoints v1 + OpenAPI + versionado; C — webhooks entrantes; D — SDKs +
cierre).

> **⚠ Gaps conocidos que NO cierran en este plan.** Los specs Playwright e2e de la
> UI de webhooks están **escritos pero NO ejecutados** (el runtime node-playwright
> de este entorno no trae navegador); los checks `curl` del OpenAPI/Swagger viven
> necesitan un **stack VIVO** (humano/CI con stack); el codegen usa **sustituciones
> de generador documentadas**; y los tests humanos `human_13_*` + el **PR a
> `main`** son **human-owned**. Ver [Pendiente](#pendiente).

## Cambios por tarea

### Fase A — Tokens y Autorización

- ✅ **`task_13_01`** — **Modelo `ApiToken`** (`db/models.py` + migración
  `0054_api_tokens`). Una fila es una credencial **por tenant** del API público:
  `scope` (`read`/`write`), `expires_at` (vigencia), `rate_limit` (override del
  default), `ip_allowlist` opcional. **Solo se guarda el digest SHA-256** del
  token crudo (`token_hash`, UNIQUE — identifica al tenant en una request sin
  autenticar) + el `prefix` claro para desambiguar en listados; nunca el token.
  Tabla **tenant-owned** (`tenant_id` NOT NULL + política FOR ALL de RLS).
- ✅ **`task_13_02`** — **Endpoint admin del tenant** (`routers/api_tokens.py`,
  `/auth/api-tokens`). El Tenant Admin (`require_tenant_admin`, sesión RLS
  tenant-scoped) **acuña / lista / revoca** tokens. El token claro se devuelve
  **exactamente una vez** al crear; la lista nunca revela el secreto (solo
  `prefix`). Revocar es soft-revoke (`revoked_at`) + invalidación inmediata del
  cache de resolución.
- ✅ **`task_13_03`** — **Middleware `X-API-Token` con cache Redis**
  (`auth/api_token_auth.py`). Resuelve un token presentado → su tenant en una
  consulta por-hash sobre el rol BYPASSRLS, **cacheada en Redis** (TTL corto,
  default 30 s; la revocación borra la clave directamente, así que el caso común
  es inmediato y el TTL es solo el techo de staleness). Inyecta una
  `ApiTokenPrincipal` (tenant + scopes) y abre una sesión RLS con `app.tenant_id`
  fijado.
- ✅ **`task_13_04`** — **Rate limiting por token, sliding window en Redis**.
  El budget por token (`rate_limit` de la fila, o `api_token_default_rate_limit`)
  se cuenta sobre `api_token_rate_limit_window_seconds` (default 60 s) y adjunta
  las cabeceras `X-RateLimit-*`; la request sobre presupuesto recibe **429**.

### Fase B — Endpoints v1

- ✅ **`task_13_05`** — **Endpoints REST públicos** (`routers/api_v1/router.py`):
  proyectos (`/api/v1/projects` + `/{id}`), planes
  (`/api/v1/projects/{id}/plans` y `/api/v1/plans/{id}`), tareas
  (`/api/v1/projects/{id}/tasks` + `/{task_id}`), conversaciones
  (`/api/v1/projects/{id}/conversations` y `/api/v1/conversations/{id}`) y KBs
  (`/api/v1/kbs` + `/{id}`). Fachada **fina**: reusa modelos ORM + los mismos
  schemas de respuesta de la UI. Auth por `require_scope` (GET→`read`,
  POST→`write`); todo bajo la sesión RLS de Fase A (sin filtro `tenant_id`
  explícito — la sesión lo fija). Listas paginadas (`limit`/`offset` con cotas).
  Un id cross-tenant / inexistente es un **404** limpio.
- ✅ **`task_13_06`** — **OpenAPI 3.1 + Swagger UI** (`routers/api_v1/openapi.py`).
  `build_v1_openapi()` genera un documento **autocontenido** solo de las rutas
  `/api/v1` (no toda la app), con **3.1.0 pineado explícitamente** (no heredar un
  default mutable del framework) y el **esquema `apiKey`/`X-API-Token`
  (`ApiTokenAuth`)** inyectado + aplicado como requisito de seguridad global
  (la dependencia de cabecera de Fase A es opaca a la generación automática). Se
  publica en `/api/v1/openapi.json` + Swagger UI en `/api/v1/docs` (ambos
  públicos, sin auth: un dev lee el contrato antes de tener token).
- ✅ **`task_13_07`** — **Versionado + tracking de uso** (`routers/api_v1/_versioning.py`).
  El path (`/api/v1`) es la fuente de verdad; `enforce_api_version` (dependencia a
  nivel de router) **negocia** la cabecera opcional `X-API-Version` (mismatch →
  400 limpio que nombra la versión servida + el set soportado), **anuncia**
  `X-API-Version: v1` en la respuesta y **trackea** uso con un contador diario en
  Redis (`apiusage:v1:<yyyymmdd>`, TTL ~10 días — observabilidad best-effort, sin
  tabla/migración nueva).

### Fase C — Webhooks Entrantes

- ✅ **`task_13_08`** — **Endpoint `/webhooks/incoming/{origin}/{config_id}` con
  HMAC** (`routers/incoming_webhooks.py` + `webhooks/signatures.py` + migración
  `0055_incoming_webhooks`). Endpoint **público** (la HMAC es la auth); orden de
  checks = contrato de seguridad: body-cap (413) → resolver config (404) → rate
  limit por config (429) → verificar HMAC (401, sin acción) → persistir.
  HMAC-SHA256 sobre el body crudo, comparada en **tiempo constante**; GitHub/GitLab
  firman `X-Hub-Signature-256: sha256=<hex>`, los demás (Jira/Sentry/Linear/genérico)
  el `X-Signature-256: <hex>` bare. El secreto se guarda **solo cifrado** (Fernet
  at rest) y nunca se loguea/echo. La URL lleva el **`config_id`, no el secreto**.
- ✅ **`task_13_09`** — **Plantillas pre-configuradas** (`webhooks/templates.py`).
  Catálogo cerrado de orígenes (`IncomingWebhookOrigin`: GitHub push/PR review,
  GitLab MR, Jira issue creado, Sentry error, Linear issue, genérico) que
  **normalizan** el payload verificado a un evento canónico. Un payload
  verificado-pero-no-normalizable se **registra sin acción** (no 500ea el
  endpoint).
- ✅ **`task_13_10`** — **Mapeo webhook → acción** (`webhooks/mapping.py` +
  `webhooks/actions.py` + migración `0056_webhook_action_mappings`). La columna
  JSONB `action_mappings` declara — por proyecto, por tipo de evento — qué acción
  dispara cada evento: **crear tarea** / **comentar tarea** / **escalar**, con
  plantillas de título/cuerpo renderizadas. Se interpreta en el tenant/proyecto de
  la config (RLS-scoped). La acción se ejecuta en la **misma transacción** que
  registra el evento (atómica, exactamente una vez por delivery).
- ✅ **`task_13_11`** — **UI de configuración por proyecto**
  (`routers/incoming_webhook_configs.py`, `/projects/{id}/incoming-webhooks` +
  frontend). `require_tenant_admin`, sesión RLS tenant-scoped + filtro `project_id`.
  Crea (devuelve el secreto claro **una vez**), lista, edita (name/enabled/mappings;
  `origin` inmutable), **rota secreto**, lista **entregas** recientes y
  **deshabilita/soft-delete**. Spec Playwright `webhooks-incoming.spec.ts`
  **escrito, no ejecutado**.
- ✅ **`task_13_12`** — **Replay desde audit** (`webhooks/replay.py` + migración
  `0057_webhook_event_replay`). `replayed_from_event_id` (self-FK NULLABLE) marca
  una fila como **replay** operador-iniciado apuntando al evento original. El
  replay **re-verifica** la firma almacenada contra el secreto actual de la config,
  re-mapea y re-ejecuta la acción, y es **él mismo auditado** (fila nueva,
  `delivery_id = NULL` para no colisionar con la idempotencia inbound). Una firma
  que ya no verifica (p.ej. secreto rotado) es un **422**, no un re-run silencioso.

### Fase D — SDKs y Cierre

- ✅ **`task_13_13`** — **SDK Python** (`packages/sdk-python`). `scripts/generate.py`
  construye el OpenAPI v1 **en proceso** (`build_v1_openapi()`, sin servidor) →
  `openapi-v1.json` → `datamodel-code-generator` reescribe `models.py`
  (**Pydantic v2**). El cliente `httpx` fino (`client.py`, escrito a mano) cablea
  esos modelos a los endpoints v1, fija `X-API-Token` una vez y eleva `ApiError`
  tipado (401/403/404/429). **Sustitución de generador documentada**: la hoja de
  ruta nombra `openapi-python-client`; se usa `datamodel-code-generator` porque su
  salida es Pydantic v2 (la misma librería del proyecto) en lugar de un cliente
  `attrs` que choca con `ruff-format`/`mypy strict`. Dir generado **excluido** de
  los linters (`pyproject.toml` + `.pre-commit-config.yaml`); verificado por
  `tests/integration/test_sdk_python.py` (NO excluido).
- ✅ **`task_13_14`** — **SDK TypeScript** (`packages/sdk-typescript`).
  `scripts/generate.mjs` reconstruye el spec en proceso → `openapi-typescript-codegen`
  v0.30.0 (preset `fetch`) genera los **tipos de modelo** en `src/generated/`. El
  cliente `fetch` fino (`src/client.ts`, escrito a mano) se queda con los modelos
  generados y fija `X-API-Token` **una vez**, porque `openapi-typescript-codegen`
  **no** respeta el esquema `apiKey`/`X-API-Token` (solo emite `Authorization:
Bearer`). Dir generado `src/generated/` **excluido** de eslint (`.eslintrc.json`)
  - prettier (`.pre-commit-config.yaml`); verificado por
    `test/sdk-typescript.test.ts` + `tsc`/`build` en verde.
- ✅ **`task_13_15`** — **Documentación + ADRs + changelog** (esta entrada, la
  **ADR 0037**, la guía
  [`api-publica-y-webhooks.md`](../03-guides/api-publica-y-webhooks.md) y la
  referencia [`public-api.md`](../04-reference/public-api.md)). Documenta lo
  implementado y **flagea los gaps conocidos** (e2e escritos-no-ejecutados, checks
  `curl` con stack vivo, sustituciones de generador, tests humanos + PR pendientes).

## Endpoints nuevos

### Gestión (JWT + `tenant_admin`)

| Endpoint                                                                            | Método       | Para qué                                                    |
| ----------------------------------------------------------------------------------- | ------------ | ----------------------------------------------------------- |
| `/auth/api-tokens`                                                                  | GET / POST   | Listar / acuñar tokens del API público (POST → claro 1 vez) |
| `/auth/api-tokens/{token_id}`                                                       | DELETE       | Revocar (soft) un token + invalidar cache                   |
| `/projects/{project_id}/incoming-webhooks`                                          | GET / POST   | Listar / crear config de webhook (POST → secreto 1 vez)     |
| `/projects/{project_id}/incoming-webhooks/{config_id}`                              | PUT / DELETE | Editar (no-secreto) / soft-delete config                    |
| `/projects/{project_id}/incoming-webhooks/{config_id}/rotate-secret`                | POST         | Rotar el secreto HMAC (devuelve nuevo claro 1 vez)          |
| `/projects/{project_id}/incoming-webhooks/{config_id}/deliveries`                   | GET          | Entregas verificadas recientes (metadata)                   |
| `/projects/{project_id}/incoming-webhooks/{config_id}/deliveries/{event_id}/replay` | POST         | Replay de una entrega almacenada (debugging)                |

### API pública v1 (`X-API-Token`; GET→`read`, POST→`write`)

| Endpoint                                        | Método     | Scope                |
| ----------------------------------------------- | ---------- | -------------------- |
| `/api/v1/projects`                              | GET / POST | read / write         |
| `/api/v1/projects/{project_id}`                 | GET        | read                 |
| `/api/v1/projects/{project_id}/plans`           | GET / POST | read / write         |
| `/api/v1/plans/{plan_id}`                       | GET        | read                 |
| `/api/v1/projects/{project_id}/tasks`           | GET / POST | read / write         |
| `/api/v1/projects/{project_id}/tasks/{task_id}` | GET        | read                 |
| `/api/v1/projects/{project_id}/conversations`   | GET / POST | read / write         |
| `/api/v1/conversations/{conversation_id}`       | GET        | read                 |
| `/api/v1/kbs`                                   | GET / POST | read / write         |
| `/api/v1/kbs/{kb_id}`                           | GET        | read                 |
| `/api/v1/openapi.json`                          | GET        | público (sin auth)   |
| `/api/v1/docs`                                  | GET        | público (Swagger UI) |

### Webhooks entrantes (público — la HMAC es la auth)

| Endpoint                                  | Método | Para qué                                                  |
| ----------------------------------------- | ------ | --------------------------------------------------------- |
| `/webhooks/incoming/{origin}/{config_id}` | POST   | Recibir + verificar (HMAC) + mapear + persistir un evento |

## Migraciones nuevas

| Revision                       | Tabla / columna                                                | Para qué                                                                                                                    |
| ------------------------------ | -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `0054_api_tokens`              | `api_tokens` (tabla + RLS FOR ALL)                             | Credencial `X-API-Token` por tenant: scope/vigencia/rate_limit/ip_allowlist; solo digest SHA-256                            |
| `0055_incoming_webhooks`       | `incoming_webhook_configs` + `incoming_webhook_events` (+ RLS) | Config por (project, origin) con secreto Fernet + eventos recibidos para replay (UNIQUE parcial `(config_id, delivery_id)`) |
| `0056_webhook_action_mappings` | `incoming_webhook_configs.action_mappings` (JSONB)             | Mapeo por evento → acción (crear/comentar/escalar) + plantillas de título/cuerpo                                            |
| `0057_webhook_event_replay`    | `incoming_webhook_events.replayed_from_event_id` (self-FK)     | Marca + enlaza una fila de replay a su evento original (auditoría)                                                          |

> Cadena de migraciones: head previo `0053_guardrail_alert_rules` →
> `0054` → `0055` → `0056` → `0057`. Todas reversibles (probado por un ciclo
> up / down a `0040_sso_email_domains` / up). Single head intacto en
> **`0057_webhook_event_replay`**.

## Configuración nueva (env / settings)

| Variable / setting                           | Default                     | Para qué                                                                                |
| -------------------------------------------- | --------------------------- | --------------------------------------------------------------------------------------- |
| `api_token_default_rate_limit`               | `100`                       | Budget por minuto de un token recién acuñado (la columna `rate_limit` gana si se fija)  |
| `api_token_rate_limit_window_seconds`        | `60`                        | Ventana del sliding-window del rate limit por token                                     |
| `api_token_cache_ttl_seconds`                | `30`                        | TTL del cache Redis `X-API-Token` → tenant (techo de staleness; revocar borra la clave) |
| `incoming_webhook_encryption_key`            | dev-only (override en prod) | Secreto del que se deriva la clave Fernet del secreto de firma de webhook entrante      |
| `incoming_webhook_max_body_bytes`            | `1048576` (1 MiB)           | Cap del body del webhook entrante (413 si se excede — guarda anti-DDoS)                 |
| `incoming_webhook_rate_limit`                | `120`                       | Budget por config del endpoint público de webhooks (429 si se excede)                   |
| `incoming_webhook_rate_limit_window_seconds` | `60`                        | Ventana del sliding-window del rate limit por config                                    |

> El versionado de `task_13_07` usa un contador Redis (`apiusage:v1:<yyyymmdd>`,
> retención ~10 días) — **sin variable de entorno ni tabla nueva** (observabilidad
> best-effort).

## SDKs nuevos

| Paquete                                             | Lenguaje    | Codegen usado                                          | Cliente               | Test de verificación                                  |
| --------------------------------------------------- | ----------- | ------------------------------------------------------ | --------------------- | ----------------------------------------------------- |
| `packages/sdk-python` (`agentic-platform-sdk`)      | Python 3.12 | `datamodel-code-generator` (modelos Pydantic v2)       | `httpx` fino (a mano) | `tests/integration/test_sdk_python.py`                |
| `packages/sdk-typescript` (`@agentic-platform/sdk`) | TypeScript  | `openapi-typescript-codegen` v0.30.0 (tipos de modelo) | `fetch` fino (a mano) | `packages/sdk-typescript/test/sdk-typescript.test.ts` |

> Ambos SDKs se generan **DESDE** el OpenAPI v1 construido **en proceso**
> (`build_v1_openapi()`, sin servidor vivo) y escrito a `openapi-v1.json`. El dir
> generado de cada uno se **excluye de los linters** (documentado en su `README.md`);
> el test de cada SDK **no** se excluye.

### Herramientas de codegen efectivamente usadas (+ sustituciones)

- **Python:** `datamodel-code-generator` **en lugar de** el `openapi-python-client`
  que nombra la hoja de ruta. Razón: su salida es **Pydantic v2** (la librería de
  modelado del proyecto) en vez de un cliente `attrs` que choca con
  `ruff-format`/`mypy strict`. Sustitución sancionada por el brief («generador
  equivalente, anotado»). Documentado en `packages/sdk-python/README.md`.
- **TypeScript:** `openapi-typescript-codegen` (el generador nombrado por la hoja
  de ruta) **sí** se usa para los **tipos de modelo**; el **cliente se escribe a
  mano** porque el generador no respeta el esquema `apiKey`/`X-API-Token` (solo
  emite `Authorization: Bearer`). Documentado en
  `packages/sdk-typescript/README.md`.

## Decisiones

- **`X-API-Token` en cabecera + API pública scoped por tenant.** La credencial
  viaja en la cabecera (nunca query param), solo se persiste su digest SHA-256, y
  resuelve a su tenant para que RLS aísle cada request al tenant del token (GET→
  `read`, POST→`write`). Registrado en **ADR 0037**.
- **Versionado en el path (`/api/v1`).** El path es la fuente de verdad; la
  cabecera `X-API-Version` es un pin/observe opcional (mismatch → 400) y se anuncia
  de vuelta. Más explícito que negociar por cabecera. Registrado en **ADR 0037**.
- **Webhook entrante: HMAC-verify + `config_id` en la URL (no el secreto).** El
  endpoint es público y la HMAC es la auth; la URL lleva el id de config (resuelve
  a tenant/proyecto), nunca el secreto (que solo vive cifrado y se devuelve en claro
  una vez). Orden de checks fail-closed. Registrado en **ADR 0037**.
- **SDKs generados desde el OpenAPI v1, in-process, con cliente fino a mano.** El
  spec se construye con `build_v1_openapi()` (sin servidor) y alimenta el codegen;
  los modelos son generados, el cliente es fino + escrito a mano (fija el token una
  vez), y el código generado se excluye de los linters. Registrado en **ADR 0037**.

## Verificación

- `pre-commit run --files <cambiados>` (black/ruff/mypy/prettier/markdown/yaml) ✅
  por tarea. Los dirs de SDK generados están **excluidos** de los linters
  (documentado en cada `README.md` + `pyproject.toml`/`.eslintrc.json`/
  `.pre-commit-config.yaml`); los tests de cada SDK **no** lo están y corren en CI.
- Suites pytest de Fase A/B/C en verde por tarea: modelo de token, admin de tokens,
  middleware, rate limit, endpoints v1, versionado, firma/plantillas/mapeo/replay de
  webhooks (`tests/unit/test_api_token_model.py`,
  `tests/integration/test_api_tokens_admin.py`, `..._api_token_middleware.py`,
  `..._api_rate_limit.py`, `..._api_v1_endpoints.py`, `..._api_versioning.py`,
  `..._webhook_signature.py`, `..._webhook_templates.py`, `..._webhook_mapping.py`,
  `..._webhook_replay.py`).
- SDKs: `tests/integration/test_sdk_python.py` (imports + paridad modelo⇄schema +
  construcción de cliente + header `X-API-Token` con transport mockeado + errores
  tipados) y `packages/sdk-typescript/test/sdk-typescript.test.ts` (+ `tsc`/`build`).
- `build_v1_openapi()` produce un documento OpenAPI **3.1.0** autocontenido con el
  esquema `ApiTokenAuth`; el check `curl http://api-server:8000/api/v1/openapi.json`
  de `task_13_06` (y el Swagger UI) necesitan un **stack VIVO** ⇒ marcados como
  check humano/CI-con-stack.
- Single head de migraciones intacto en **`0057_webhook_event_replay`**.

## Pendiente

### Gaps conocidos (reportados por las fases A–D)

1. **Specs Playwright e2e escritos-no-ejecutados.** `webhooks-incoming.spec.ts`
   (task_13_11) está **escrito pero PENDIENTE DE VERIFICACIÓN HUMANA**: el runtime
   node-playwright de este entorno no tiene navegador.
2. **Checks `curl` del OpenAPI/Swagger con stack vivo.** El check generic-shell
   `curl .../api/v1/openapi.json` (task_13_06) y la inspección manual del Swagger UI
   (`/api/v1/docs`) necesitan un **stack en marcha** ⇒ verificación humana/CI-con-stack.
   En CI el documento se valida **en proceso** vía `build_v1_openapi()`.
3. **Sustituciones de generador documentadas.** El SDK Python usa
   `datamodel-code-generator` (no `openapi-python-client`); el SDK TS usa
   `openapi-typescript-codegen` solo para los tipos + cliente a mano. Razonado en
   cada `README.md` y en [Decisiones](#decisiones) / ADR 0037.
4. **Tests humanos `human_13_*` pendientes.** `human_13_01` (token + scope + IP
   allowlist), `human_13_02` (webhook GitHub real crea tareas + 401 si HMAC falla),
   `human_13_03` (rate limiting + 429 + `X-RateLimit-Remaining`), `human_13_04` (SDK
   Python instalable + ejemplo del README). Requieren un stack vivo + un proveedor
   externo real.

### Cierre del plan

El plan pasa a `pending_human_validation` (no `completed`): faltan los tests
humanos `human_13_*` con un stack vivo y el **PR a `main`**, ambos **human-owned**.
Las 15 tareas tienen su checkbox `[x]` y su test automático en verde (o, para los
checks live de stack, marcado como verificación humana/CI).

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los tests
humanos del plan y cerrar — o aceptar explícitamente — los gaps de arriba).
