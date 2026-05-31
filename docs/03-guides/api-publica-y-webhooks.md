---
title: Usar la API pública v1 y registrar webhooks entrantes
audience: integrador, backend-dev
phase: 13-api-publica-webhooks
updated: 2026-05-30
---

# Usar la API pública v1 y registrar webhooks entrantes

Esta guía recorre el flujo completo del Plan 13 desde el punto de vista de quien
**integra** una herramienta externa con la plataforma:

1. Acuñar un `X-API-Token` por tenant.
2. Llamar a la API v1 — con el **SDK Python**, el **SDK TypeScript** y `curl`.
3. Registrar y **asegurar** un webhook entrante por proveedor.

Para el contrato completo (todos los endpoints, scopes, tunables) ver la
referencia [`../04-reference/public-api.md`](../04-reference/public-api.md); para
el porqué de las decisiones ver
[ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md).

> **Prerrequisitos.** El stack está arriba (`docker compose up -d`). Eres
> **Tenant Admin** (acuñar tokens y gestionar webhooks pide ese rol). Sustituye
> `https://platform.example.com` por la URL pública de tu instalación.

## 1. Acuñar un `X-API-Token`

El token es la credencial **por tenant** del API público. Lo acuña el Tenant Admin
(autenticado con su sesión/JWT) en `/auth/api-tokens`. El token claro se devuelve
**exactamente una vez** — guárdalo, no se puede recuperar.

```bash
# Autenticado como Tenant Admin (cookie de sesión / Bearer de la UI).
curl -X POST https://platform.example.com/auth/api-tokens \
  -H "Content-Type: application/json" \
  -b "$SESSION_COOKIE" \
  -d '{
    "name": "ci-readonly",
    "scopes": ["read"],
    "rate_limit": 200,
    "expires_at": "2027-01-01T00:00:00Z",
    "ip_allowlist": ["203.0.113.10"]
  }'
# → 201 { "id": "...", "prefix": "tkn_abc", "token": "tkn_abc...<solo aquí>", ... }
```

- `scopes`: `["read"]` solo permite GET; añade `"write"` para crear recursos.
- `rate_limit` / `expires_at` / `ip_allowlist` son opcionales.
- Lista tokens con `GET /auth/api-tokens` (nunca revela el secreto, solo `prefix`).
- Revoca con `DELETE /auth/api-tokens/{token_id}` (efectivo de inmediato).

## 2. Llamar a la API v1

El token viaja **siempre** en la cabecera `X-API-Token` (nunca query param). Los
GET piden scope `read`, los POST `write`. Toda lista es paginada (`limit`/`offset`).
Un id de otro tenant es un **404**; sobre el rate limit es un **429**.

### Con curl

```bash
export TOKEN="tkn_abc..."

# Listar proyectos del propio tenant (paginado)
curl "https://platform.example.com/api/v1/projects?limit=50&offset=0" \
  -H "X-API-Token: $TOKEN"

# Crear un proyecto (requiere scope write)
curl -X POST https://platform.example.com/api/v1/projects \
  -H "X-API-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Mi proyecto"}'

# Fijar/observar la versión (opcional): un mismatch devuelve 400
curl https://platform.example.com/api/v1/projects \
  -H "X-API-Token: $TOKEN" -H "X-API-Version: v1"
# La respuesta siempre trae 'X-API-Version: v1' y 'X-RateLimit-*'.
```

El contrato OpenAPI 3.1 vive en `/api/v1/openapi.json` y un Swagger UI interactivo
en `/api/v1/docs` (ambos públicos, sin token: léelos antes de integrar).

### Con el SDK Python (`agentic-platform-sdk`)

```bash
pip install -e packages/sdk-python   # desde el monorepo
```

```python
from agentic_platform_sdk import ApiClient, V1ProjectCreateRequest

with ApiClient("https://platform.example.com", "tkn_abc...") as api:
    # listar (paginado)
    for project in api.list_projects(limit=50, offset=0):
        print(project.id, project.name, project.status)

    # crear (requiere un token con scope write)
    created = api.create_project(V1ProjectCreateRequest(name="Mi proyecto"))

    # plans / tasks / conversations son project-scoped
    plans = api.list_plans(created.id)
    tasks = api.list_tasks(created.id)

    # las knowledge bases son tenant-scoped
    kbs = api.list_kbs()
```

Una respuesta non-2xx eleva `agentic_platform_sdk.ApiError` con `status_code` +
`body`, así que puedes ramificar por 401 (token malo), 403 (scope), 404
(cross-tenant) o 429 (rate limit).

### Con el SDK TypeScript (`@agentic-platform/sdk`)

```ts
import { ApiClient, type V1ProjectCreateRequest } from "@agentic-platform/sdk";

const api = new ApiClient({
  baseUrl: "https://platform.example.com",
  apiToken: "tkn_abc...",
});

const projects = await api.listProjects({ limit: 50, offset: 0 });
const created = await api.createProject({ name: "Mi proyecto" } satisfies V1ProjectCreateRequest);
const plans = await api.listPlans(created.id);
const kbs = await api.listKbs();
```

Una respuesta non-2xx lanza `ApiError` con `statusCode` + `body`. Cero
dependencias de runtime: usa el `fetch` de la plataforma (Node 18+ / navegador).

> Ambos SDKs se **generan desde** el OpenAPI v1, así que siempre casan con el
> servidor. Regenéralos tras cambiar el contrato:
> `python packages/sdk-python/scripts/generate.py` /
> `node packages/sdk-typescript/scripts/generate.mjs`.

## 3. Registrar y asegurar un webhook entrante

Un webhook entrante deja que un tool externo (GitHub, GitLab, Jira, Sentry,
Linear, genérico) empuje un evento que se convierte en una acción del sistema
(crear tarea / comentar tarea / escalar).

### 3.1 Crear la config (devuelve el secreto una vez)

```bash
# Autenticado como Tenant Admin.
curl -X POST https://platform.example.com/projects/$PROJECT_ID/incoming-webhooks \
  -H "Content-Type: application/json" \
  -b "$SESSION_COOKIE" \
  -d '{
    "origin": "github",
    "name": "GitHub → tareas de revisión",
    "enabled": true,
    "action_mappings": [
      {"event_type": "github.pull_request_review",
       "action": "create_task",
       "title_template": "Review: {title}",
       "body_template": "{body}\n\nde {actor}"}
    ]
  }'
# → 201 { "id": "<config_id>",
#         "incoming_path": "/webhooks/incoming/github/<config_id>",
#         "signing_secret": "<solo aquí — cópialo al proveedor>" }
```

- El **secreto de firma se devuelve en claro una sola vez**. Guárdalo: se almacena
  solo cifrado y no se puede recuperar (perderlo obliga a **rotar**).
- La URL pública lleva el **`config_id`, nunca el secreto**
  ([ADR 0037](../05-architecture-decisions/0037-api-publica-x-api-token-versionado-path-webhooks-hmac-config-id-sdks-openapi.md) §3).

### 3.2 Configurar el proveedor (por origen)

La plataforma verifica una **HMAC-SHA256 sobre el body crudo**. Cada proveedor
firma en su cabecera nativa:

| `origin`                                 | En el proveedor                                                                                                                           |
| ---------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `github`                                 | Webhook → Payload URL `…/webhooks/incoming/github/<config_id>`, **Secret** = el secreto. GitHub firma `X-Hub-Signature-256: sha256=<hex>` |
| `gitlab`                                 | Webhook → URL `…/gitlab/<config_id>`, **Secret token** = el secreto (modo HMAC), misma cabecera estilo GitHub                             |
| `jira` / `sentry` / `linear` / `generic` | URL `…/<origin>/<config_id>`; el sender (o un proxy normalizador) firma `X-Signature-256: <hex>` (hex bare, sin prefijo)                  |

Verificar manualmente la firma (genérico):

```bash
BODY='{"hello":"world"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -X POST https://platform.example.com/webhooks/incoming/generic/$CONFIG_ID \
  -H "Content-Type: application/json" \
  -H "X-Signature-256: $SIG" \
  -d "$BODY"
# → 202 { "status": "accepted", "event_id": "...", "action": "create_task", "task_id": "..." }
```

### 3.3 Garantías de seguridad

- El endpoint es **público** — la HMAC ES la autenticación. Orden fail-closed:
  body-cap (413) → resolver config (404) → rate limit por config (429) → **HMAC
  (401, sin acción)** → mapear+actuar → persistir.
- **Idempotencia:** una redelivery del mismo evento (mismo `delivery_id`) es un
  no-op — nunca crea una tarea duplicada.
- **Aislamiento:** el `config_id` ata el evento a su `tenant_id`/`project_id`; un
  evento de un proyecto nunca actúa sobre otro tenant.
- **Rotación:** si sospechas que el secreto se filtró,
  `POST …/incoming-webhooks/{config_id}/rotate-secret` (devuelve un nuevo claro una
  vez; el anterior deja de verificar de inmediato — actualiza el proveedor).

### 3.4 Depurar entregas

- `GET …/incoming-webhooks/{config_id}/deliveries` lista las entregas verificadas
  recientes (metadata).
- `POST …/deliveries/{event_id}/replay` re-corre verify+parse+map+action contra el
  payload almacenado (auditado como fila propia). Una firma que ya no verifica
  (secreto rotado) devuelve **422**.

## Errores y códigos

| Código | Significado                                                         |
| ------ | ------------------------------------------------------------------- |
| 400    | `X-API-Version` pin no soportado                                    |
| 401    | API: token inválido/ausente · Webhook: firma HMAC mala/ausente      |
| 403    | API: token válido pero le falta el scope (`read`/`write`)           |
| 404    | Recurso de otro tenant o inexistente (nunca revela si un id existe) |
| 413    | Webhook: body por encima del cap (default 1 MiB)                    |
| 422    | Replay cuya firma almacenada ya no verifica                         |
| 429    | Rate limit excedido (mira `X-RateLimit-Remaining` / `Retry-After`)  |
