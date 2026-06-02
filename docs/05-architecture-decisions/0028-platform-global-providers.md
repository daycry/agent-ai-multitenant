---
adr_id: "0028"
title: "Auth + LLM providers + catálogo de precios viven a nivel plataforma (global), no por tenant"
status: accepted
date: 2026-05-28
authors: [system_architect]
plan_referenced: 08-sso-empresarial
docs_language: es
---

# ADR 0028 — Providers y catálogo de precios son globales (no per-tenant)

## Contexto

El producto tiene dos familias de configuración con esta pregunta abierta:
**¿se configuran a nivel plataforma (una sola vez, system_admin) o por
tenant (cada tenant define los suyos)?**

1. **Auth providers** — password (Plan 00, hecho), SSO empresarial
   (Plan 08, pending), eventualmente Google / Microsoft / GitHub.
2. **LLM providers + catálogo de modelos + precios** — Claude Agent
   SDK / GitHub Copilot / Azure AI Foundry / Ollama (ADR 0021,
   cerrados a cuatro), con sus API keys, endpoints y la tabla de
   precios `cost per 1M tokens` por modelo.

El flujo de auth actual es **`login → pick tenant`**, no
`pick tenant → login`. Concretamente:

- `users` es una tabla global (no tiene `tenant_id`).
- El usuario abre el document root (`http://localhost:3000`), introduce
  email + password, recibe un JWT. El JWT puede llevar `tid` (si tenía
  un único tenant o reusa el último picker) o no.
- El selector de tenant (`tenant-picker`) sólo aparece para
  `system_admin`; los tenant_admin / tenant_user ven directamente su
  tenant.

Esto significa que **el tenant no existe como concepto antes de
autenticarse**. Si los auth providers fueran per-tenant, haría falta
identificar el tenant antes (subdominio `acme.platform.com`, claim
del email-domain `@acme.com → tenant Acme`, etc.) — todo eso es
arquitectura nueva que hoy no existe ni el roadmap pide.

Sobre **LLM providers + precios**, la realidad de los tenants es
"departamentos internos de la misma organización" (CLAUDE.md §"Qué es
Este Sistema"): no son clientes externos con compliance per-tenant
ni con contratos comerciales independientes con Anthropic / Azure.
El operador del stack es uno y mismo, y le interesa configurar las
API keys una sola vez en Vault.

El código ya prefigura un catálogo global:
[`apps/api-server/src/api_server/chat/cost.py`](../../apps/api-server/src/api_server/chat/cost.py)
tiene `DEFAULT_AI_PRICE_CATALOG` como **placeholder único** en código.
Plan 11 (`guardrails-precios`, pending) tiene que decidir si lo
materializa como tabla per-tenant o como tabla global — este ADR fija
"global".

## Decisión

**Tanto los auth providers como los LLM providers (incluyendo el
catálogo de modelos y la tabla de precios) son globales
(platform-level), gestionados sólo por `system_admin`.** El consumo
de modelos lo eligen los tenants (per-agent), y el coste se atribuye
al tenant que ejecutó.

### División clara

| Capa                                                    | Vive en          | Quién lo gestiona           |
| ------------------------------------------------------- | ---------------- | --------------------------- |
| `auth_providers` (password / SSO / OIDC)                | Platform-global  | `system_admin`              |
| `llm_providers` (Claude / Copilot / Azure / Ollama)     | Platform-global  | `system_admin`              |
| `llm_model_catalog` (model_id + precio + capabilities)  | Platform-global  | `system_admin`              |
| `agents.model_provider` + `agents.model_id`             | Per-tenant       | `tenant_admin` (Plan 06.8)  |
| `executions` (tokens consumidos + coste snapshotted)    | Per-tenant (RLS) | sistema (auto)              |
| Tenant usage summary (`/tenant-settings/usage-summary`) | Per-tenant view  | `tenant_admin` (ve la suya) |

### Snapshot del precio en cada ejecución

El coste se calcula y persiste **al cerrar la ejecución**, no en
tiempo de consulta:

```
executions.cost_amount = tokens_in * catalog.price_in_at(t)
                       + tokens_out * catalog.price_out_at(t)
executions.cost_currency = catalog.currency_at(t)
executions.cost_model_id = catalog.model_id    # para auditoría
```

Cambiar el precio en el catálogo **no recomputa el histórico**. La
factura de abril queda congelada con los precios de abril aunque en
mayo se renegocie el tarifa. Justificación: invoicing estable +
auditabilidad legal (operadores enterprise lo exigen).

### Allowlist y override como escape hatches futuros

Dos extensiones quedan documentadas pero **no se implementan hoy**:

1. **`tenant_model_allowlist`** — limita qué modelos del catálogo
   global puede usar un tenant ("Acme sólo Claude Sonnet, no GPT-4o").
   Cuando aparezca el primer caso, se añade una tabla
   `tenant_id, model_id, allowed=bool`. Si no hay rows para el tenant,
   se asume "todos permitidos".

2. **`tenant_pricing_override`** — descuentos negociados ("Beta tiene
   contrato Anthropic 20% off Claude"). Tabla
   `tenant_id, model_id, input_per_million_override,
output_per_million_override`. Si no hay override, se aplica el
   precio del catálogo global.

Ambos son `system_admin` (los configura la plataforma) y no requieren
cambios al modelo de hoy — son aditivos cuando aparezca el primer caso.

## Consecuencias

### En RBAC (matriz `docs/04-reference/rbac.md`)

Cuando se implementen, los endpoints nuevos son todos `system_admin`:

```
GET, POST       /admin/auth-providers              system_admin
GET, PUT, DEL   /admin/auth-providers/{id}         system_admin
POST            /admin/auth-providers/{id}/test    system_admin

GET, POST       /admin/llm-providers               system_admin
GET, PUT, DEL   /admin/llm-providers/{id}          system_admin
POST            /admin/llm-providers/{id}/test     system_admin

GET, POST       /admin/llm-models                  system_admin  (catálogo)
GET, PUT, DEL   /admin/llm-models/{id}             system_admin
POST            /admin/llm-models/{id}/test        system_admin

GET             /tenant-settings/usage-summary     tenant_admin  (su factura)
```

El `tenant_admin` **NO** crea ni edita providers ni catálogo. Sólo
elige qué modelo asigna a cada agente (vía
`POST /agents { model_provider, model_id }`, ya cubierto por la
matriz de Plan 06.8).

### En Plan 08 (SSO empresarial, pending)

Plan 08 se diseña asumiendo:

- Providers globales (Azure AD / Google / GitHub) registrados como
  rows en `auth_providers` con sus credenciales en Vault.
- **Allowlist por email-domain global**: cada `auth_provider` lleva
  un campo `allowed_email_domains TEXT[]` para forzar que sólo
  `@empresa.com` pase por el Azure AD corporativo.
- Asignación automática de tenant: opcional `auth_provider →
default_tenant_id`, que añade al usuario como `tenant_user` del
  tenant indicado en su primer login. Si está nulo, el usuario llega
  sin memberships y el `system_admin` se las da.

NO se diseña para SSO per-tenant con `acme.platform.com`. Si en el
futuro hace falta, se hace via email-domain claiming (lo cubre el
campo `allowed_email_domains` cuando empareja con la tabla
`tenant_email_domains` que se añadirá entonces).

### En Plan 09 (marketplace, pending)

El catálogo de modelos global simplifica el marketplace: cualquier
paquete del marketplace puede asumir que los providers básicos
están disponibles globalmente. Las plantillas de proyecto pueden
referenciar `claude-sonnet-4-6` con la garantía de que la plataforma
tiene credenciales válidas — el tenant no necesita configurar nada.

### En Plan 11 (guardrails-precios, pending)

Plan 11 materializa:

- La tabla `llm_model_catalog` (sustituye el `DEFAULT_AI_PRICE_CATALOG`
  en código).
- El campo `executions.cost_amount` + `cost_currency` + `cost_model_id`
  (snapshot).
- El endpoint `/tenant-settings/usage-summary` con agregación mensual
  del tenant activo.
- Budget enforcement: cuando la suma de `executions.cost_amount` del
  mes excede `Project.budget_amount`, marcar `paused_by_budget=true`.

Plan 11 NO toca la decisión global vs per-tenant — viene fijada por
este ADR.

## Alternativas consideradas

### Alt-1: Providers per-tenant con subdominio

`acme.platform.com` resolve a la misma app pero con `Host` header
identificando tenant Acme. La pantalla de login muestra sólo los
providers de Acme.

- ❌ Arquitectura nueva (DNS, certs wildcard, reverse proxy con
  routing por host).
- ❌ Conflicto con la promesa "Docker Compose en una máquina"
  (CLAUDE.md §"Cosas que NO Hacer": "asumir Kubernetes /
  multi-máquina").
- ❌ Plan 08 estimado en 2-3 semanas se va a 4-5.
- ✅ Cada tenant trae sus credenciales sin que el platform admin las
  toque (privacy de claims, no de tokens).

Rechazada: el coste estructural no compensa para el escenario de
"departamentos internos".

### Alt-2: LLM providers per-tenant con BYOK

Cada tenant configura sus API keys de Anthropic / Azure en su propio
path Vault. Las ejecuciones se cobran a su factura externa
directamente, no aparecen en la facturación de la plataforma.

- ✅ Real isolation de billing.
- ✅ Compliance per-tenant (Azure Foundry en su propia suscripción).
- ❌ El platform-admin pierde visibilidad de costes agregados.
- ❌ El tenant_admin tiene que entender Vault paths, rotation, etc.
- ❌ Plan 11 (guardrails-precios) se complica: hay que decidir qué
  hacer cuando un tenant agota su quota de Anthropic
  (¿bloquea sus ejecuciones? ¿hace fallback a otro provider?).

Rechazada como default. Se conserva como escape hatch futuro vía
`tenant_pricing_override` + un eventual
`tenant_provider_credential_override` que use Vault paths per-tenant
cuando aparezca el primer caso real.

### Alt-3: Live recompute del coste en cada consulta

`cost = SUM(execution.tokens × catalog.price_actual)` calculado a
demanda, sin guardar `cost_amount` en `executions`.

- ✅ Cambios de precio se reflejan en cualquier reporte sin recomputar.
- ❌ Bajar el precio en mayo cambia retroactivamente los costes de
  abril → facturas inestables.
- ❌ Si el modelo desaparece del catálogo (e.g. Anthropic retira
  Sonnet 3), las ejecuciones viejas dejan de tener precio.
- ❌ Auditoría legal pide costes inmutables después del cierre del
  mes.

Rechazada: snapshot at execution time es el patrón estándar
(Stripe, AWS, GCP).

### Alt-4: Catálogo en código (no en BD)

Mantener `DEFAULT_AI_PRICE_CATALOG` como hoy (constante Python),
sin tabla.

- ✅ Cero migrations, cero endpoints nuevos.
- ❌ Cambiar un precio requiere deploy del api-server.
- ❌ El operador no puede añadir un modelo nuevo sin tocar código.
- ❌ Sin endpoint para que el frontend liste modelos disponibles.

Rechazada: viable hoy mientras Plan 11 no llegue, pero el ADR fija
"a partir de Plan 11, el catálogo vive en BD".

## Esquema (cuando se implemente)

### `auth_providers`

```sql
CREATE TABLE auth_providers (
    id                    UUID PRIMARY KEY,
    kind                  TEXT NOT NULL,    -- 'password' | 'oidc' | 'saml'
    display_name          TEXT NOT NULL,
    config                JSONB NOT NULL,   -- endpoints, client_id, etc.
    secret_vault_path     TEXT,             -- vault path para client_secret
    allowed_email_domains TEXT[],           -- e.g. ['@empresa.com']
    default_tenant_id     UUID REFERENCES organizations(id),
    is_active             BOOLEAN NOT NULL DEFAULT true,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Sin tenant_id, sin RLS. Tabla global. Acceso sólo via BYPASSRLS
-- engine (system_admin endpoints).
```

### `llm_providers`

```sql
CREATE TABLE llm_providers (
    id                  UUID PRIMARY KEY,
    kind                TEXT NOT NULL,      -- 'claude_sdk' | 'copilot' | 'azure_foundry' | 'ollama'
    display_name        TEXT NOT NULL,
    base_url            TEXT,                -- e.g. https://apim.example.com
    secret_vault_path   TEXT,                -- vault path para API key
    is_active           BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `llm_model_catalog`

```sql
CREATE TABLE llm_model_catalog (
    id                    UUID PRIMARY KEY,
    provider_id           UUID NOT NULL REFERENCES llm_providers(id),
    model_id              TEXT NOT NULL,     -- 'claude-sonnet-4-6', 'gpt-4o', ...
    display_name          TEXT NOT NULL,
    context_window_tokens INTEGER NOT NULL,
    input_per_million     NUMERIC(10, 4) NOT NULL,
    output_per_million    NUMERIC(10, 4) NOT NULL,
    currency              TEXT NOT NULL,     -- 'USD' | 'EUR'
    modalities            TEXT[] NOT NULL,   -- ['text', 'vision', ...]
    is_active             BOOLEAN NOT NULL DEFAULT true,
    valid_from            TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until           TIMESTAMPTZ,       -- NULL = vigente
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (provider_id, model_id, valid_from)
);
-- valid_from + valid_until permiten histórico de precios para
-- auditar facturas viejas. La query "qué precio aplicaba el 2026-04-15"
-- es: WHERE valid_from <= '2026-04-15' AND (valid_until IS NULL OR
--                                            valid_until > '2026-04-15').
```

### Cambios en `executions`

```sql
ALTER TABLE executions
    ADD COLUMN cost_amount    NUMERIC(12, 6),
    ADD COLUMN cost_currency  TEXT,
    ADD COLUMN cost_model_id  TEXT,
    ADD COLUMN cost_catalog_id UUID REFERENCES llm_model_catalog(id);
-- Llenados al cerrar la execution. cost_catalog_id apunta a la
-- versión exacta del precio que se aplicó (auditoría legal).
```

## Riesgos

| Riesgo                                                                 | Probabilidad | Impacto | Mitigación                                                                                               |
| ---------------------------------------------------------------------- | ------------ | ------- | -------------------------------------------------------------------------------------------------------- |
| Un cliente futuro pide BYOK y rechaza compartir keys con la plataforma | Media        | Medio   | `tenant_pricing_override` + provider override per-tenant (escape hatches documentados arriba).           |
| Un tenant abusa de modelos caros sin allowlist                         | Alta         | Medio   | `tenant_model_allowlist` (escape hatch). Mientras tanto: budget enforcement de Plan 11.                  |
| Cambio de precio rompe reports en curso                                | Baja         | Bajo    | Snapshot at execution time + `valid_from/valid_until` en `llm_model_catalog`.                            |
| SSO per-tenant aparece como requisito comercial                        | Media        | Alto    | Email-domain claiming es viable sin subdomain routing — extiende `auth_providers.allowed_email_domains`. |

## Estado: implementado en Plan 11.2 (LLM providers)

> **La parte de LLM providers de este ADR ya está implementada** (rama
> `plan/11.2-llm-provider-admin-ui`). La parte de `auth_providers` /
> `/admin/auth-providers` sigue pendiente del Plan 08 (SSO empresarial).
> La tabla `llm_model_catalog` quedó materializada como `model_prices`
> en el Plan 11 (ver más abajo) — Plan 11.2 sólo le añadió la asociación
> `provider_id`.

### Tabla `llm_providers` (migración 0070)

Platform-global, **sin `tenant_id` y sin RLS** (ADR §Esquema): el acceso
es exclusivamente vía los endpoints `system_admin` que corren sobre el
engine BYPASSRLS (`get_admin_session`); la migración concede la tabla
sólo al rol de migraciones, nunca a `app_user`, así que una sesión tenant
no la ve. Columnas reales:

| Columna                     | Notas                                                                                                                   |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `id` (UUID PK)              | v7, time-ordered (newest-first por `id desc`)                                                                           |
| `kind`                      | `TEXT` + CHECK `ck_llm_providers_kind` ∈ {`claude_sdk`,`copilot`,`azure_foundry`,`ollama`} (ADR 0021, catálogo cerrado) |
| `display_name`              | etiqueta del operador                                                                                                   |
| `base_url`                  | endpoint APIM / Ollama; `NULL` para `claude_sdk` (suscripción, sin URL)                                                 |
| `secret_vault_path`         | **puntero** Vault `platform/llm/<provider_id>`; `NULL` hasta escribir un secreto. NUNCA contiene el secreto             |
| `config` (JSONB)            | knobs NO secretos (defaults de modelo, flags)                                                                           |
| `is_active`                 | toggle; un provider inactivo no gana sobre el fallback de env/installer                                                 |
| `created_at` / `updated_at` | timestamps                                                                                                              |

Índice parcial `ix_llm_providers_kind_active` sobre `kind WHERE is_active`
(camino de búsqueda del factory de runtime). No hay columna que pueda
albergar un secreto: el secreto vive **sólo en Vault**.

### Endpoints reales (todos `system_admin`, BYPASSRLS)

```
GET    /admin/llm-providers                       system_admin   (lista, newest-first)
POST   /admin/llm-providers                       system_admin   (crea; credencial → Vault)
GET    /admin/llm-providers/{id}                  system_admin
PUT    /admin/llm-providers/{id}                  system_admin   (edita / rota credencial)
DELETE /admin/llm-providers/{id}                  system_admin   (borra row + secreto de Vault)
POST   /admin/llm-providers/{id}/test             system_admin   (liveness clasificado)

POST   /admin/llm/copilot/device-flow/start       system_admin   (user_code + verification_uri)
POST   /admin/llm/copilot/device-flow/poll        system_admin   (1 poll; al autorizar → token a Vault)
```

(El gate es `require_system_admin`; un caller tenant recibe `403`.)

### Manejo de credenciales (write-only, sólo Vault)

- La credencial llega al endpoint como `pydantic.SecretStr` y se escribe
  en Vault (mount KV v2 `secret`) en la ruta lógica
  **`platform/llm/<provider_id>`**. La BD persiste **sólo**
  `secret_vault_path` (el puntero). El valor NUNCA va a una columna, NUNCA
  se loguea, y NUNCA se devuelve en ninguna respuesta (`LLMProviderResponse`
  expone sólo campos no-secretos + un booleano derivado `has_credential`).
- Campos de credencial por `kind` (espejan el instalador, `vault_bootstrap.py`):
  - `claude_sdk` / `copilot` → `oauth_token`.
  - `azure_foundry` → `api_key` + `base_url` (gateway APIM, requerido).
  - `ollama` → `base_url` (requerido) + `bearer_token` opcional (Ollama Cloud).
- Si Vault no está cableado (`API_SERVER_VAULT_TOKEN` ausente) la creación
  responde `503` en vez de persistir un provider sin sitio donde guardar
  su credencial. Un fallo de transporte de Vault al escribir hace rollback
  (`502`) — nunca queda una fila sin su secreto ni viceversa.
- `DELETE` borra primero el secreto de Vault (idempotente) y luego la
  fila; `POST /{id}/test` lee el secreto de Vault para el probe y devuelve
  un estado clasificado (`ok` / `auth_error` / `connection_error` /
  `config_error` / `upstream_error`) que por construcción nunca filtra el
  secreto.

### Device Flow de Copilot

`POST …/device-flow/start` arranca el flujo OAuth de GitHub para un
provider `copilot` existente y devuelve `user_code` + `verification_uri`
(+ `device_code` / `interval` / `expires_in` para el navegador). `POST
…/device-flow/poll` hace **un** intento de poll; mientras el operador no
autoriza devuelve `status=pending`/`slow_down`; al autorizar acuña el
token OAuth de GitHub, lo escribe en Vault (`oauth_token` en
`platform/llm/<provider_id>`), fija `secret_vault_path` y **nunca**
devuelve el token. La máquina de device-flow vive en
`shared_llm.providers.copilot` (no se reimplementa).

### Catálogo de modelos enlazado a providers (migración 0071)

El `llm_model_catalog` del ADR es `model_prices` (Plan 11). Plan 11.2 le
añadió una columna **`provider_id` FK nullable → `llm_providers.id`** con
`ON DELETE SET NULL` (borrar un provider no borra el precio ni rompe su
histórico). `GET /admin/model-prices?provider_id=<uuid>` filtra por
provider y `POST`/`PATCH /admin/model-prices` aceptan `provider_id`
(validado contra una fila real). No se reconstruyó el catálogo ni el sync
desde LiteLLM — la asociación es puramente aditiva.

### Sync de precios acotado a las familias de proveedores activos (plan `price-sync-active-providers`)

El sync de `/admin/model-prices` desde el feed comunitario de LiteLLM ya **no
importa los ~2000 modelos sin filtrar**: solo trae las familias
`litellm_provider` de los `llm_providers` con `is_active=true`. Las familias se
derivan de los kinds activos vía el mapa constante `KIND_TO_LITELLM_FAMILIES`
(`api_server.pricing.litellm_sync`), espejo del catálogo cerrado de ADR 0021:

| `kind`          | Familias `litellm_provider`   |
| --------------- | ----------------------------- |
| `claude_sdk`    | `anthropic`                   |
| `azure_foundry` | `azure`, `azure_ai`, `openai` |
| `copilot`       | `openai`, `anthropic`         |
| `ollama`        | `ollama`                      |

Reglas: el `allowed_families` se calcula por-sync (unión de los kinds activos),
con un override opcional `price_sync.allowed_families` en `platform_settings`
(System Admin) que manda si está presente; **sin fallback** (0 proveedores
activos ⇒ el sync no añade nada y trata todo el catálogo como fuera de ámbito);
y **no destructivo** — las familias que salen del allowlist **cierran su periodo
abierto** (descontinuadas), nunca se borran, preservando el histórico y los
snapshots de coste. Las entradas del feed fuera del allowlist se cuentan como
omitidas (`reason = family_not_active`). La pantalla `/admin/model-prices`
muestra el ámbito ("Sincronizando solo: …") y avisa cuando no hay proveedores
activos.

### Cableado del runtime (precedencia DB > env)

`api_server.llm_providers.factory_resolver.resolve_provider_config(kind)`
lee la fila ACTIVA más reciente de ese `kind` + su credencial de Vault y
devuelve `ResolvedProviderConfig(base_url, secret)`; el factory de runtime
(`agent_runtime.providers` / `model_from_spec`) la superpone sobre el spec
de env/installer. Si no hay fila activa devuelve `None` y se mantiene el
fallback de env/installer actual (sin romper call sites). Un blip de Vault
degrada a "sin credencial" (deja la de env) en vez de fallar el run.

## Notas de implementación (decisión original)

- Este ADR fijó la decisión; los planes 08 (auth) / 09 / 11 / **11.2**
  (LLM providers) implementan sus partes con esta decisión como guía.
- El `DEFAULT_AI_PRICE_CATALOG` en código fue volcado a `model_prices`
  por el Plan 11 (la tabla es la fuente de verdad desde entonces).
- La página `/admin/llm-providers` se construyó en el **Plan 11.2** (UI
  System Admin, `RoleGuard system_admin`); la de `/admin/auth-providers`
  sigue pendiente del Plan 08.
- Vault paths (forma lógica bajo el mount KV v2 `secret`):
  - LLM (implementado): `platform/llm/<provider_id>` (e.g.
    `platform/llm/<uuid-de-claude>` ó `.../<uuid-de-azure-foundry>`).
  - Auth (pendiente Plan 08): `platform/auth/<provider_id>`.
  - **NO** se usan rutas tenant-scoped (`<tenant>/...`) para estos
    providers — eso queda reservado para los MCP servers per-proyecto
    (ADR 0025).

## Trazabilidad

- Discusión inicial: conversación con el operador del 2026-05-28
  tras cerrar Plan 06.8 (RBAC enforcement).
- Roadmap impactado: Plan 08 (SSO empresarial), Plan 09 (marketplace),
  Plan 11 (guardrails-precios).
- RBAC matriz: añade sección "Platform-global configuration" cuando
  los endpoints `/admin/llm-providers` y `/admin/auth-providers`
  existan.
