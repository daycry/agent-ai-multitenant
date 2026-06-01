---
title: RBAC — Matriz de roles por endpoint
audience: backend-dev, architect, security
phase: 06.8-rbac-enforcement
updated: 2026-06-01
---

# RBAC — Matriz de roles por endpoint

Esta página es **el contrato** entre el código de los endpoints y los
tests integration cross-rol (`tests/integration/test_rbac_resources.py`):

- Cualquier endpoint nuevo añadido tras Plan 06.8 debe extender esta
  matriz **y** el test parametrizado correspondiente.
- El gate del backend es la fuente de verdad. La UI puede ocultar
  botones para mejor UX, pero el backend valida igual.

## Roles soportados (Plan 06.8 §Decisiones clave)

| Rol               | Descripción                                                                                                                      |
| ----------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `system_admin`    | Flag global `users.is_system_admin = true`. Pasa todos los gates. Cross-tenant.                                                  |
| `tenant_admin`    | Miembro activo del tenant con `role='tenant_admin'`. Mutaciones de configuración (proyectos, agentes, teams, MCP, KBs, settings) |
| `tenant_user`     | Miembro activo del tenant con `role='tenant_user'`. Lectura + operaciones del día a día (tasks, comentarios, conversaciones)     |
| `system_operator` | Reservado para auditoría/operación a nivel plataforma. Por ahora no se distingue de `tenant_user` en endpoints.                  |

`tenant_member` **no es un rol** — es el predicado "tiene membership
activa, sea cual sea el rol". Se usa en `require_tenant_member()`
como mínimo común.

## Helpers que aplican los gates

Definidos en
[`apps/api-server/src/api_server/auth/deps.py`](../../apps/api-server/src/api_server/auth/deps.py):

```python
require_tenant_member  # active membership in JWT tenant — or system_admin
require_tenant_admin   # tenant_admin role in JWT tenant — or system_admin
require_tenant_role(r) # parametric factory (rarely needed)
require_system_admin   # platform-level system_admin only
```

`system_admin` siempre pasa cada gate; no hace falta listar el rol en
cada celda.

## Matriz por router

Convención de las celdas: el rol **mínimo** requerido. "anon" = sin
auth (login, register, healthz). "agent" = autenticación interna por
`X-Agent-Auth` (containers agent-runtime que llaman a
`/internal/agent/*`, ver
[`internal_agent.py`](../../apps/api-server/src/api_server/routers/internal_agent.py)).

### `auth.py` y `main.py` — sesión

| Endpoint                                             | Método | Rol mínimo |
| ---------------------------------------------------- | ------ | ---------- |
| [`/auth/register`](../03-guides/roles-y-permisos.md) | POST   | anon       |
| `/auth/login`                                        | POST   | anon       |
| `/auth/logout`                                       | POST   | principal  |
| `/auth/me`                                           | GET    | principal  |
| `/me`                                                | GET    | principal  |
| `/me/memberships`                                    | GET    | principal  |
| `/healthz`                                           | GET    | anon       |

"principal" = autenticado, sin chequeo de tenant ni rol. Apropiado para
endpoints que devuelven el contexto del propio usuario.

### `admin.py` — system_admin

| Endpoint               | Método      | Rol mínimo     |
| ---------------------- | ----------- | -------------- |
| `/admin/tenants`       | GET, POST   | `system_admin` |
| `/admin/tenants/{id}`  | GET,PUT,DEL | `system_admin` |
| `/admin/users`         | GET         | `system_admin` |
| `/admin/system-health` | GET         | `system_admin` |

### Platform-global configuration — `system_admin` (ADR 0028)

Los proveedores LLM y el catálogo de precios son **platform-global**: no
tienen `tenant_id`, no llevan RLS y se gestionan **sólo** por
`system_admin` desde los endpoints `/admin/*`, que corren sobre el engine
BYPASSRLS (`get_admin_session`). Un `tenant_admin` NO los crea ni edita —
sólo elige qué modelo asigna a cada agente (`POST /agents`). Las
credenciales viven **sólo en Vault** (`platform/llm/<provider_id>`); la
API nunca las devuelve (write-only).

#### `llm_providers.py` (Plan 11.2)

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/admin/llm-providers`           | GET    | `system_admin` |
| `/admin/llm-providers`           | POST   | `system_admin` |
| `/admin/llm-providers/{id}`      | GET    | `system_admin` |
| `/admin/llm-providers/{id}`      | PUT    | `system_admin` |
| `/admin/llm-providers/{id}`      | DELETE | `system_admin` |
| `/admin/llm-providers/{id}/test` | POST   | `system_admin` |

> `POST` recibe la credencial como `SecretStr` y la escribe en Vault; la
> BD guarda sólo `secret_vault_path`. `PUT` rota la credencial si se
> envía. `DELETE` borra también el secreto de Vault. `/test` hace un
> liveness probe clasificado leyendo el secreto de Vault, sin echo-arlo.

#### `copilot_device_flow.py` — Device Flow de GitHub Copilot (Plan 11.2)

| Endpoint                               | Método | Rol mínimo     |
| -------------------------------------- | ------ | -------------- |
| `/admin/llm/copilot/device-flow/start` | POST   | `system_admin` |
| `/admin/llm/copilot/device-flow/poll`  | POST   | `system_admin` |

> `start` devuelve `user_code` + `verification_uri`; `poll` acuña el token
> OAuth al autorizar y lo escribe en Vault del provider — el token nunca
> aparece en una respuesta.

#### `model_prices.py` (catálogo global — Plan 11, asociación a provider en 11.2)

| Endpoint                   | Método | Rol mínimo     |
| -------------------------- | ------ | -------------- |
| `/model-prices`            | GET    | `tenant_user`  |
| `/model-prices/current`    | GET    | `tenant_user`  |
| `/model-prices/{id}`       | GET    | `tenant_user`  |
| `/admin/model-prices`      | GET    | `system_admin` |
| `/admin/model-prices`      | POST   | `system_admin` |
| `/admin/model-prices/{id}` | PATCH  | `system_admin` |
| `/admin/model-prices/{id}` | DELETE | `system_admin` |
| `/admin/model-prices/sync` | POST   | `system_admin` |

> El catálogo de lectura (`/model-prices`) es read-open para cualquier
> member (RLS de lectura global, espeja `exchange_rates`); las mutaciones
> y el sync son `system_admin`. Desde Plan 11.2 `GET /admin/model-prices`
> acepta `?provider_id=<uuid>` (filtrar por provider) y `POST`/`PATCH`
> aceptan `provider_id` (FK nullable a `llm_providers`).
>
> Los endpoints `/admin/auth-providers` (auth providers globales, ADR 0028) siguen **pendientes del Plan 08** (SSO empresarial).

### `projects.py`

| Endpoint         | Método | Rol mínimo     |
| ---------------- | ------ | -------------- |
| `/projects`      | GET    | `tenant_user`  |
| `/projects`      | POST   | `tenant_admin` |
| `/projects/{id}` | GET    | `tenant_user`  |
| `/projects/{id}` | PUT    | `tenant_admin` |
| `/projects/{id}` | DELETE | `tenant_admin` |

### `agents.py`

| Endpoint               | Método | Rol mínimo     |
| ---------------------- | ------ | -------------- |
| `/agents`              | GET    | `tenant_user`  |
| `/agents`              | POST   | `tenant_admin` |
| `/agents/{id}`         | GET    | `tenant_user`  |
| `/agents/{id}`         | PUT    | `tenant_admin` |
| `/agents/{id}`         | DELETE | `tenant_admin` |
| `/agents/{src}/fork`   | POST   | `tenant_admin` |
| `/agents/{fork}/diff`  | GET    | `tenant_user`  |
| `/agents/{fork}/merge` | POST   | `tenant_admin` |

### `teams.py`

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/teams`                         | GET    | `tenant_user`  |
| `/teams`                         | POST   | `tenant_admin` |
| `/teams/{id}`                    | GET    | `tenant_user`  |
| `/teams/{id}`                    | PUT    | `tenant_admin` |
| `/teams/{id}`                    | DELETE | `tenant_admin` |
| `/teams/{id}/members`            | POST   | `tenant_admin` |
| `/teams/{id}/members/{agent_id}` | PUT    | `tenant_admin` |
| `/teams/{id}/members/{agent_id}` | DELETE | `tenant_admin` |

### `knowledge_bases.py`

| Endpoint                                        | Método | Rol mínimo     |
| ----------------------------------------------- | ------ | -------------- |
| `/knowledge-bases`                              | GET    | `tenant_user`  |
| `/knowledge-bases`                              | POST   | `tenant_admin` |
| `/knowledge-bases/{id}`                         | GET    | `tenant_user`  |
| `/knowledge-bases/{id}`                         | PUT    | `tenant_admin` |
| `/knowledge-bases/{id}`                         | DELETE | `tenant_admin` |
| `/knowledge-bases/{id}/documents`               | GET    | `tenant_user`  |
| `/knowledge-bases/{id}/documents`               | POST   | `tenant_admin` |
| `/knowledge-bases/{id}/documents/{doc}`         | GET    | `tenant_user`  |
| `/knowledge-bases/{id}/documents/{doc}`         | DELETE | `tenant_admin` |
| `/knowledge-bases/{id}/documents/{doc}/reindex` | POST   | `tenant_admin` |
| `/knowledge-bases/{id}/projects`                | POST   | `tenant_admin` |
| `/knowledge-bases/{id}/projects/{project_id}`   | DELETE | `tenant_admin` |
| `/projects/{id}/knowledge-bases`                | GET    | `tenant_user`  |
| `/documents/{id}/citations`                     | GET    | `tenant_user`  |

### `kb_categories.py` (Plan 06.10)

Categorías para organizar KBs en la UI. Built-ins (`tenant_id IS NULL`)
son read-only — PUT/DELETE devuelven 403 explícito incluso para
`tenant_admin`.

| Endpoint              | Método | Rol mínimo     |
| --------------------- | ------ | -------------- |
| `/kb-categories`      | GET    | `tenant_user`  |
| `/kb-categories`      | POST   | `tenant_admin` |
| `/kb-categories/{id}` | PUT    | `tenant_admin` |
| `/kb-categories/{id}` | DELETE | `tenant_admin` |

### `mcp.py` y `mcp_catalog.py`

| Endpoint                             | Método | Rol mínimo     |
| ------------------------------------ | ------ | -------------- |
| `/mcp-catalog`                       | GET    | `tenant_user`  |
| `/projects/{id}/mcp/test-connection` | POST   | `tenant_admin` |

(El config CRUD de los MCP servers vive embebido en `projects.py` —
ver auditoría en `scripts/audit_rbac.py` para confirmar al cambiar.)

### `memories.py`

| Endpoint                     | Método | Rol mínimo                                       |
| ---------------------------- | ------ | ------------------------------------------------ |
| `/memories`                  | GET    | `tenant_user`                                    |
| `/memories`                  | POST   | `tenant_user` (excepto `scope=global` → admin)   |
| `/memories/{id}`             | DELETE | `tenant_user` (excepto memoria con scope=global) |
| `/memories/{id}/similar`     | GET    | `tenant_user`                                    |
| `/memories/{src}/merge-into` | POST   | `tenant_user`                                    |

> El gate dinámico por `scope=global` se aplica dentro del handler,
> no por dependency. Mantiene el comportamiento existente del Plan 04.5.

### `tasks.py` + `task_lifecycle.py` — operaciones del día a día

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/projects/{id}/tasks`           | GET    | `tenant_user`  |
| `/projects/{id}/tasks`           | POST   | `tenant_user`  |
| `/projects/{id}/tasks/{task_id}` | GET    | `tenant_user`  |
| `/projects/{id}/tasks/{task_id}` | PUT    | `tenant_user`  |
| `/projects/{id}/tasks/{task_id}` | DELETE | `tenant_admin` |
| `/tasks/{id}/history`            | GET    | `tenant_user`  |
| `/tasks/{id}/human-action`       | POST   | `tenant_admin` |

> Borrar tareas es operación de admin (escalada); el día a día es
> mover entre columnas (PUT status).

### `plans.py`

| Endpoint                      | Método | Rol mínimo     |
| ----------------------------- | ------ | -------------- |
| `/projects/{id}/plans`        | GET    | `tenant_user`  |
| `/projects/{id}/plans`        | POST   | `tenant_user`  |
| `/plans/{id}`                 | GET    | `tenant_user`  |
| `/plans/{id}`                 | PUT    | `tenant_user`  |
| `/plans/{id}`                 | DELETE | `tenant_admin` |
| `/plans/{id}/approve`         | POST   | `tenant_admin` |
| `/plans/{id}/comments`        | GET    | `tenant_user`  |
| `/plans/{id}/comments`        | POST   | `tenant_user`  |
| `/plans/{id}/cost-breakdown`  | GET    | `tenant_user`  |
| `/plans/{id}/escalated-tasks` | GET    | `tenant_admin` |
| `/plans/{id}/free-task`       | POST   | `tenant_user`  |
| `/plans/{id}/sync-to-kanban`  | POST   | `tenant_user`  |

> Aprobar un plan es un compromiso de ejecución → admin. Crear un
> free-task lo hace cualquier member (operación de día a día).

### `conversations.py`

| Endpoint                       | Método      | Rol mínimo    |
| ------------------------------ | ----------- | ------------- |
| `/projects/{id}/conversations` | GET, POST   | `tenant_user` |
| `/conversations/{id}`          | GET,PUT,DEL | `tenant_user` |
| `/conversations/{id}/messages` | GET, POST   | `tenant_user` |

### `approvals.py` y `approval_policies.py`

| Endpoint                  | Método | Rol mínimo    |
| ------------------------- | ------ | ------------- |
| `/approval-policies`      | GET    | `tenant_user` |
| `/approvals`              | GET    | `tenant_user` |
| `/approvals/{id}/resolve` | POST   | `tenant_user` |

> Resolver requests de aprobación es operación que cualquier member
> puede ejecutar (el sistema le ha pedido confirmar). El admin
> gestiona las **políticas** que las generan.

### `executions.py`

| Endpoint           | Método | Rol mínimo    |
| ------------------ | ------ | ------------- |
| `/executions/{id}` | GET    | `tenant_user` |

### `dep_cache.py`

| Endpoint                              | Método | Rol mínimo     |
| ------------------------------------- | ------ | -------------- |
| `/projects/{id}/dep-cache/invalidate` | POST   | `tenant_admin` |

### `skills.py`

| Endpoint       | Método | Rol mínimo     |
| -------------- | ------ | -------------- |
| `/skills`      | GET    | `tenant_user`  |
| `/skills`      | POST   | `tenant_admin` |
| `/skills/{id}` | GET    | `tenant_user`  |
| `/skills/{id}` | PUT    | `tenant_admin` |
| `/skills/{id}` | DELETE | `tenant_admin` |

### `tools.py` y `tools_diagnostic.py`

| Endpoint                                | Método | Rol mínimo     |
| --------------------------------------- | ------ | -------------- |
| `/tools`                                | GET    | `tenant_user`  |
| `/tools`                                | POST   | `tenant_admin` |
| `/tools/{id}`                           | GET    | `tenant_user`  |
| `/tools/{id}`                           | PUT    | `tenant_admin` |
| `/tools/{id}`                           | DELETE | `tenant_admin` |
| `/projects/{id}/agent-tools-diagnostic` | GET    | `tenant_user`  |

### `tenant_settings.py`

| Endpoint                            | Método | Rol mínimo     |
| ----------------------------------- | ------ | -------------- |
| `/tenant-settings/_registry`        | GET    | `tenant_user`  |
| `/tenant-settings/hourly-rate`      | GET    | `tenant_user`  |
| `/tenant-settings/hourly-rate`      | PUT    | `tenant_admin` |
| `/tenant-settings/{category}`       | GET    | `tenant_user`  |
| `/tenant-settings/{category}/{key}` | GET    | `tenant_user`  |
| `/tenant-settings/{category}/{key}` | PUT    | `tenant_admin` |

### `review.py` — auth por firma HMAC

Los tres endpoints son **sólo HMAC** (no JWT) — el reviewer humano abre
la URL desde un email/Slack y no tiene una sesión JWT del tenant. La
firma incluye `session_id|exp` y se valida con `hmac.compare_digest`.

| Endpoint                       | Método | Auth                                |
| ------------------------------ | ------ | ----------------------------------- |
| `/review/{session_id}`         | GET    | HMAC (`?exp=&sig=`)                 |
| `/review/{session_id}/rerun`   | POST   | HMAC (`?exp=&sig=`)                 |
| `/ws/review/{session_id}/logs` | WS     | HMAC verificado **antes** de accept |

### `internal_agent.py` — auth por `X-Agent-Auth`

Endpoints invocados desde dentro de un container agent-runtime. La auth
es por header `X-Agent-Auth: <token-firmado>`, validada con
`get_agent_principal`. No requieren JWT ni membership — el container
ya tiene la identidad del tenant en su `--env` al spawn.

| Endpoint                           | Método | Auth           |
| ---------------------------------- | ------ | -------------- |
| `/internal/agent/_health`          | GET    | `X-Agent-Auth` |
| `/internal/agent/document-convert` | POST   | `X-Agent-Auth` |
| `/internal/agent/memory-recall`    | POST   | `X-Agent-Auth` |
| `/internal/agent/memory-store`     | POST   | `X-Agent-Auth` |
| `/internal/agent/promote-to-kb`    | POST   | `X-Agent-Auth` |
| `/internal/agent/rag-search`       | POST   | `X-Agent-Auth` |

## Cómo regenerar el audit

```bash
python scripts/audit_rbac.py                  # Markdown a stdout
python scripts/audit_rbac.py --csv > audit.csv
```

El script lista todos los endpoints actuales con su dep chain real y
clasifica cada uno como `no-auth | principal-only | tenant_member |
tenant_admin | system_admin`. Cualquier desviación entre el audit y
esta matriz indica un endpoint sin gate o un gate mal puesto.

## Sobre `system_operator`

Por ahora `system_operator` no se distingue de `tenant_user` en ningún
endpoint — el enum lo soporta pero la matriz no introduce diferencia.
Si en el futuro hace falta (e.g. "puede ver audit_log pero no mutar
recursos"), se documenta aquí + se añade el helper correspondiente.

## Ejemplos curl

Los siguientes ejemplos asumen que tienes un JWT del usuario activo
en `$TOKEN`. La cabecera `X-Tenant-Id` sólo la honora el backend
cuando el usuario es `system_admin`; para usuarios normales el `tid`
del propio JWT manda y el header se ignora silenciosamente.

### Listar proyectos del tenant (cualquier member)

```bash
curl -sS http://localhost:8001/projects \
  -H "Authorization: Bearer $TOKEN"
```

Respuesta esperada:

- `200` + JSON array si el caller es member o admin del tenant.
- `403 {"detail":"user is not a member of this tenant"}` si la
  membership no existe / está soft-deleted / `is_active=false`.
- `403 {"detail":"no active tenant context"}` si el JWT no llevaba
  `tid` (login fresco sin tenant picker).
- `401 {"detail":"missing Authorization header"}` sin el header
  `Bearer`.

### Crear proyecto (sólo `tenant_admin`)

```bash
curl -sS http://localhost:8001/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Nuevo proyecto","status":"active"}'
```

- `201` + JSON del proyecto si admin / system_admin.
- `403 {"detail":"tenant_admin role required"}` si es `tenant_user`.

### Cambiar de tenant siendo system_admin

```bash
curl -sS http://localhost:8001/projects \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-Tenant-Id: 00000000-0000-0000-0000-000000000aaa"
```

El header sobrescribe el `tid` del JWT (sólo si `is_system_admin=true`
en el token). Útil para que el admin-panel actúe "como" un tenant
específico desde el picker.

### Saber qué puede hacer el usuario (`/me`)

```bash
curl -sS http://localhost:8001/me \
  -H "Authorization: Bearer $TOKEN"
```

Respuesta:

```json
{
  "user_id": "5b1...",
  "email": "alice@acme.test",
  "full_name": "Alice",
  "is_system_admin": false,
  "memberships": [
    {
      "tenant_id": "00000000-0000-0000-0000-000000000aaa",
      "tenant_name": "Acme Corp",
      "role": "tenant_admin",
      "is_active": true
    }
  ],
  "active_tenant_id": "00000000-0000-0000-0000-000000000aaa"
}
```

La admin-panel consume este endpoint en cada carga para saber qué
botones renderizar y qué tenants ofrecer en el picker.

### Tests integration que pinean la matriz

```bash
pytest tests/integration/test_auth_role_helpers.py -v
pytest tests/integration/test_rbac_resources.py -v
pytest tests/integration/test_me_endpoint.py -v
```

Cualquier endpoint añadido tras Plan 06.8 que falle en `test_rbac_
resources.py` (o que falte una entrada en la matriz de este doc) es
un PR que NO debería mergearse.
