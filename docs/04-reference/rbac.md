---
title: RBAC — Matriz de roles por endpoint
audience: backend-dev, architect, security
phase: cross-cutting
updated: 2026-06-03
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

| Endpoint                                   | Método      | Rol mínimo     |
| ------------------------------------------ | ----------- | -------------- |
| `/admin/tenants`                           | GET, POST   | `system_admin` |
| `/admin/tenants/{id}`                      | GET,PUT,DEL | `system_admin` |
| `/admin/users`                             | GET         | `system_admin` |
| `/admin/users/{user_id}/memberships`       | GET, POST   | `system_admin` |
| `/admin/users/{user_id}/memberships/{mid}` | PATCH, DEL  | `system_admin` |
| `/admin/system-health`                     | GET         | `system_admin` |

> **Administración de usuarios y acceso por membership (ADR 0047).** Tras
> re-arquitecturar auth a platform-global, el acceso de un usuario a un
> tenant lo concede **exclusivamente** una `UserOrganizationMembership`
> que asigna el System Admin desde `/admin/users` (no hay claiming por
> email-domain ni `default_tenant_id`; deny-by-default). `POST` asigna
> usuario↔tenant+rol (revive una membership previamente revocada en vez de
> chocar con el `UNIQUE(user_id, tenant_id)`); `PATCH` cambia rol y/o
> `is_active` (desactivar quita acceso sin borrar); `DELETE` revoca
> (soft-delete + `is_active=false`). Corren sobre el engine BYPASSRLS
> (`get_admin_session`) porque el System Admin actúa cross-tenant; cada
> mutación deja `audit_log` con el `tenant_id` afectado. El `role` de la
> membership se limita a roles **per-tenant** (`tenant_admin` /
> `tenant_user` / `system_operator`) — nunca otorga `system_admin`.

### Platform-global configuration — `system_admin` (ADR 0028)

Los proveedores LLM y el catálogo de precios son **platform-global**: no
tienen `tenant_id`, no llevan RLS y se gestionan **sólo** por
`system_admin` desde los endpoints `/admin/*`, que corren sobre el engine
BYPASSRLS (`get_admin_session`). Un `tenant_admin` NO los crea ni edita —
sólo elige qué modelo asigna a cada agente (`POST /agents`). Las
credenciales viven **sólo en Vault** (`platform/llm/<provider_id>`); la
API nunca las devuelve (write-only).

#### `llm_providers.py` (Plan 11.2)

| Endpoint                                | Método | Rol mínimo     |
| --------------------------------------- | ------ | -------------- |
| `/admin/llm-providers`                  | GET    | `system_admin` |
| `/admin/llm-providers`                  | POST   | `system_admin` |
| `/admin/llm-providers/{id}`             | GET    | `system_admin` |
| `/admin/llm-providers/{id}`             | PUT    | `system_admin` |
| `/admin/llm-providers/{id}`             | DELETE | `system_admin` |
| `/admin/llm-providers/{id}/test`        | POST   | `system_admin` |
| `/admin/llm-providers/{id}/sync-models` | POST   | `system_admin` |

> `POST` recibe la credencial como `SecretStr` y la escribe en Vault; la
> BD guarda sólo `secret_vault_path`. `PUT` rota la credencial si se
> envía. `DELETE` borra también el secreto de Vault. `/test` hace un
> liveness probe clasificado leyendo el secreto de Vault, sin echo-arlo.
> `/sync-models` refresca el catálogo de modelos del provider.

#### `platform_settings.py` — ajustes de plataforma (añadido tras Plan 06.8)

| Endpoint                                 | Método | Rol mínimo     |
| ---------------------------------------- | ------ | -------------- |
| `/admin/platform-settings`               | GET    | `system_admin` |
| `/admin/platform-settings/_registry`     | GET    | `system_admin` |
| `/admin/platform-settings/model-options` | GET    | `system_admin` |
| `/admin/platform-settings/{key}`         | PUT    | `system_admin` |

> Los cuatro llevan `require_system_admin` sobre la sesión BYPASSRLS
> (`get_admin_session`): los ajustes son **platform-global**, sin
> `tenant_id` ni RLS. `_registry` devuelve el catálogo de claves
> permitidas (`PUT` de una clave fuera del registry es 4xx, no un
> ajuste nuevo); `model-options` alimenta los selectores de modelo del
> panel. `PUT` deja `audit_log`.
>
> **No estaba en esta matriz hasta prod-15** (hallazgo docsroadmap-5).
> La guardia que lo evita en adelante es
> `tests/unit/test_rbac_matrix_drift.py`, que falla si aparece un prefijo
> o una ruta `/admin/*` sin fila aquí.

#### `ollama.py` — gestión de modelos Ollama (ADR 0056)

| Endpoint                    | Método | Rol mínimo     |
| --------------------------- | ------ | -------------- |
| `/admin/ollama/models`      | GET    | `system_admin` |
| `/admin/ollama/models/pull` | POST   | `system_admin` |
| `/admin/ollama/models`      | DELETE | `system_admin` |

> Reutiliza a propósito la auth de la plataforma (`require_system_admin`)
> en vez de montar una auth paralela no-tenant-aware contra el Ollama del
> stack. `pull` y `delete` mutan el host: nunca `tenant_admin`.
> Tampoco estaba en la matriz hasta prod-15.

#### `embeddings.py` — catálogo de modelos de embedding

| Endpoint                             | Método | Rol mínimo     |
| ------------------------------------ | ------ | -------------- |
| `/admin/embeddings/available-models` | GET    | `system_admin` |

> Hallazgo **nuevo** de prod-15, que la auditoría 2026-06 no listó: este
> router tampoco figuraba en la matriz. Sólo lee el catálogo disponible.

#### `copilot_device_flow.py` — Device Flow de GitHub Copilot (Plan 11.2)

| Endpoint                               | Método | Rol mínimo     |
| -------------------------------------- | ------ | -------------- |
| `/admin/llm/copilot/device-flow/start` | POST   | `system_admin` |
| `/admin/llm/copilot/device-flow/poll`  | POST   | `system_admin` |

> `start` devuelve `user_code` + `verification_uri`; `poll` acuña el token
> OAuth al autorizar y lo escribe en Vault del provider — el token nunca
> aparece en una respuesta.

#### `model_prices.py` (catálogo global — Plan 11, asociación a provider en 11.2)

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/model-prices`                  | GET    | `tenant_user`  |
| `/model-prices/current`          | GET    | `tenant_user`  |
| `/model-prices/{id}`             | GET    | `tenant_user`  |
| `/admin/model-prices`            | GET    | `system_admin` |
| `/admin/model-prices`            | POST   | `system_admin` |
| `/admin/model-prices/{id}`       | PATCH  | `system_admin` |
| `/admin/model-prices/{id}`       | DELETE | `system_admin` |
| `/admin/model-prices/sync`       | POST   | `system_admin` |
| `/admin/model-prices/sync/diff`  | POST   | `system_admin` |
| `/admin/model-prices/sync/apply` | POST   | `system_admin` |
| `/admin/model-prices/sync/audit` | GET    | `system_admin` |

> El catálogo de lectura (`/model-prices`) es read-open para cualquier
> member (RLS de lectura global, espeja `exchange_rates`); las mutaciones
> y el sync son `system_admin`. Desde Plan 11.2 `GET /admin/model-prices`
> acepta `?provider_id=<uuid>` (filtrar por provider) y `POST`/`PATCH`
> aceptan `provider_id` (FK nullable a `llm_providers`).
>
> Los **auth providers** (OIDC/SAML) son platform-global desde **ADR 0047**
> (supersede la parte per-tenant de ADR 0031, re-alinea con 0028): la
> config vive en `sso_configurations` global y se gestiona vía
> `/auth/sso/config` + `/auth/sso/saml/config` (ver la sección SSO de esta
> matriz y [auth-sso.md](./auth-sso.md)); la administración de usuarios y su
> acceso por membership está en `/admin/users/{id}/memberships`.

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
| `/agents/{id}/tools`   | GET    | `tenant_user`  |
| `/agents/{id}/tools`   | PUT    | `tenant_admin` |

> `GET /agents/{id}/tools` lista las tools asignadas vía la junction
> `agent_tools` (incluye `is_builtin` + `implementation_type` para la
> taxonomía derivada básica/avanzada). `PUT` reemplaza el conjunto
> entero (declarativo). Scope (Plan 06.15, ADR 0044): built-in
> asignable a cualquier agente; custom sólo del propio tenant (cross-
> tenant → 422); MCP sólo si el proyecto del agente declara ese MCP
> server (→ 422). Un agente `global_builtin` rechaza la escritura con
> 403 (forkéalo y asigna sobre el fork). Sin filas ⇒ sin restricción
> por agente (backward-compatible).

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

### `human_agents.py` — Human Agents (Plan 16)

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/human-agents`                  | GET    | `tenant_user`  |
| `/human-agents/templates`        | GET    | `tenant_user`  |
| `/human-agents/assignable-users` | GET    | `tenant_user`  |
| `/human-agents/{id}`             | GET    | `tenant_user`  |
| `/human-agents`                  | POST   | `tenant_admin` |
| `/human-agents/{id}`             | PUT    | `tenant_admin` |
| `/human-agents/{id}`             | DELETE | `tenant_admin` |
| `/human-agents/{id}/fork`        | POST   | `tenant_admin` |

> Crear / editar / forkear un Human Agent (quién es, su `assigned_user_id`,
> rate, timeout de aceptación) es config → admin. Listar y resolver los
> usuarios asignables es lectura → cualquier member.

### `human_inbox.py` — bandeja del Human Agent (Plan 16)

Cualquier member usa su propia bandeja (`require_tenant_member`); el
handler valida que el assignment es del propio usuario.

| Endpoint                           | Método | Rol mínimo    |
| ---------------------------------- | ------ | ------------- |
| `/inbox/assignments`               | GET    | `tenant_user` |
| `/inbox/history`                   | GET    | `tenant_user` |
| `/inbox/metrics`                   | GET    | `tenant_user` |
| `/inbox/assignments/{id}/accept`   | POST   | `tenant_user` |
| `/inbox/assignments/{id}/reject`   | POST   | `tenant_user` |
| `/inbox/assignments/{id}/complete` | POST   | `tenant_user` |
| `/inbox/assignments/{id}/escalate` | POST   | `tenant_user` |
| `/inbox/reviews`                   | GET    | `tenant_user` |
| `/inbox/reviews/{id}/approve`      | POST   | `tenant_user` |
| `/inbox/reviews/{id}/reject`       | POST   | `tenant_user` |

> `complete` envía el entregable: en `auto_approve` la tarea va a `done`;
> en `peer_human_reviewer` queda `in_review` y se crea un assignment de
> revisión para otro Human Agent (los endpoints `/inbox/reviews/*`). Ver
> [referencia del modelo](./domain-model.md#human-agents-plan-16) +
> [runbook de tareas humanas](../06-runbooks/human-tasks-operations.md).

### `marketplace.py` (Plan 09)

Detalle de trust tiers + consentimiento en
[marketplace.md](./marketplace.md). `/admin/marketplace/*` (curado del
catálogo oficial) corre sobre el engine BYPASSRLS.

| Endpoint                             | Método | Rol mínimo     |
| ------------------------------------ | ------ | -------------- |
| `/marketplace/listings`              | GET    | `tenant_user`  |
| `/marketplace/listings/{id}`         | GET    | `tenant_user`  |
| `/marketplace/private/listings`      | POST   | `tenant_admin` |
| `/marketplace/private/listings/{id}` | PUT    | `tenant_admin` |
| `/marketplace/private/listings/{id}` | DELETE | `tenant_admin` |
| `/marketplace/installations`         | GET    | `tenant_user`  |
| `/marketplace/listings/{id}/install` | POST   | `tenant_admin` |
| `/marketplace/installations/{id}`    | DELETE | `tenant_admin` |
| `/marketplace/shares`                | GET    | `tenant_admin` |
| `/marketplace/shares/{id}`           | DELETE | `tenant_admin` |
| `/admin/marketplace/*`               | GET    | `system_admin` |

**Visibilidad del catálogo (ADR 0142 D6).** `GET /marketplace/listings` y
`GET /marketplace/listings/{id}` aplican, **encima de la RLS**, el filtro de
revisión: solo se ve lo `published`, más lo propio del tenant en cualquier
estado (el autor necesita leer el motivo de su rechazo). Un listing en
`pending_review` ajeno es un **404**, no un 403 — un 403 confirmaría que existe.

#### Cola de revisión — System Admin (ADR 0142 D6)

Sobre la sesión BYPASSRLS, porque revisar es por definición mirar lo de otro
tenant: un `pending_review` es invisible para cualquier sesión que no sea la de
su autor, así que la cola no se puede servir desde una sesión de tenant.

| Endpoint                                    | Método | Rol mínimo     |
| ------------------------------------------- | ------ | -------------- |
| `/admin/marketplace/review-queue`           | GET    | `system_admin` |
| `/admin/marketplace/listings/{id}/versions` | GET    | `system_admin` |
| `/admin/marketplace/listings/{id}/approve`  | POST   | `system_admin` |
| `/admin/marketplace/listings/{id}/reject`   | POST   | `system_admin` |
| `/admin/marketplace/listings/{id}/promote`  | POST   | `system_admin` |

Un rechazo **sin motivo escrito es un 422** (`ListingRejectRequest.reason`
tiene `min_length=1` y `review.reject_listing` lo vuelve a comprobar tras el
`strip()`): un rechazo mudo es indistinguible de un borrado y no se puede
recurrir. `promote` exige que el listing esté ya `published`, y admite **bajar**
además de subir — degradar un `verified` estropeado sin despublicarlo, porque
despublicar rompería las instalaciones vivas.

### `marketplace_deployments.py` (ADR 0142, plan marketplace-v2-despliegue)

El despliegue de una instalación en un proyecto concreto —la entidad que el
[ADR 0142](../05-architecture-decisions/0142-marketplace-despliegue-tres-capas.md)
introduce— vive en su propio router porque `marketplace.py` ya sostiene toda la
superficie del plan 09. Mismo reparto de siempre: **mutaciones `tenant_admin`,
lecturas `tenant_member`**.

Un id de otro tenant devuelve **404**, no 403: la RLS de
`marketplace_deployments` (ENABLE + FORCE + `tenant_isolation`) lo esconde, y un
403 confirmaría que existe.

| Endpoint                                      | Método | Rol mínimo     |
| --------------------------------------------- | ------ | -------------- |
| `/marketplace/installations/{id}/deployments` | POST   | `tenant_admin` |
| `/marketplace/installations/{id}/deployments` | GET    | `tenant_user`  |
| `/marketplace/deployments/{id}/retire`        | POST   | `tenant_admin` |
| `/projects/{id}/marketplace/available`        | GET    | `tenant_user`  |

### `auth/api-tokens`, SSO, MFA, SCIM (Plan 08 / Plan 13 / ADR 0047)

Contratos completos en [auth-sso.md](./auth-sso.md) (SSO/MFA/SCIM) y
[public-api.md](./public-api.md) (tokens). **Auth providers
platform-global desde ADR 0047** (supersede la parte per-tenant de ADR
0031): login global **por provider** (no por tenant), callback OIDC + ACS
SAML **globales**, lista pública de providers para el `/login`, y
resolución de tenant **por membership** después del login. Resumen de
gates:

| Router / Endpoint                            | Método            | Rol mínimo                         |
| -------------------------------------------- | ----------------- | ---------------------------------- |
| `/auth/api-tokens`                           | GET, POST         | `tenant_admin`                     |
| `/auth/api-tokens/{id}`                      | DELETE            | `tenant_admin`                     |
| `/auth/sso/providers`                        | GET               | anon (lista pública, sin secretos) |
| `/auth/sso/{provider_id}/oidc/login`         | GET               | anon (inicio del flujo)            |
| `/auth/sso/oidc/callback`                    | GET               | anon (callback del IdP)            |
| `/auth/sso/{provider_id}/saml/login`         | GET               | anon (inicio del flujo SAML)       |
| `/auth/sso/saml/acs`                         | POST              | anon (ACS global del IdP)          |
| `/auth/discover?email=`                      | GET               | anon (login discovery)             |
| `/auth/session/resolve`                      | GET               | principal (post-login)             |
| `/auth/session/select-tenant`                | POST              | principal (tenant-picker)          |
| `/auth/sso/config`, `/auth/sso/saml/config`  | GET               | `tenant_member`                    |
| `/auth/sso/config`, `/auth/sso/saml/config`  | POST,PUT,DEL      | `tenant_admin`                     |
| `/auth/mfa/totp*`, `/auth/mfa/webauthn*`     | GET/POST/DEL      | `tenant_member` (gestión propia)   |
| `/auth/mfa/totp/verify`, `/webauthn/login/*` | POST              | anon (segundo factor en login)     |
| `/scim/v2/Users*`                            | \*                | token SCIM (`Bearer`)              |
| `/auth/sso/scim/tokens`                      | GET, POST, DELETE | `tenant_admin`                     |

> **Rutas viejas retiradas (ADR 0047).** Las rutas per-tenant
> `/auth/sso/{tenant_id}/oidc|saml/login` y `/auth/sso/{tenant_id}/saml/acs`
> **se retiran sin redirección** (decisión del operador). El login es ahora
> por `provider_id` global y el ACS SAML es único para toda la plataforma.
>
> **`/auth/sso/providers` es público y NO expone secretos.** Devuelve solo
> `id` / `kind` / `display_name` / `button_label` / `login_url` de cada
> provider habilitado, para que `/login` pinte un botón de marca por
> provider. El `client_secret` OIDC y la clave privada SP siguen cifrados
> en reposo (Fernet `sso_encryption_key` / Vault) y **nunca** se devuelven.
>
> **Resolución por membership.** Tanto el login local (`/auth/login`) como
> el SSO acuñan primero una sesión de **identidad sin tenant**. El cliente
> llama a `/auth/session/resolve`: **0 memberships activas** →
> `state="no_access"` (pantalla "sin permisos, contacta al administrador",
> sin token de tenant); **1** → `state="single"` (token tenant-scoped
> directo); **>1** → `state="multiple"` (tenant-picker →
> `/auth/session/select-tenant`, que re-valida la membership antes de
> acuñar el token). Un `system_admin` sin memberships no se trata distinto
> aquí: su poder cross-tenant viene del override `X-Tenant-Id` + BYPASSRLS.
>
> La gestión MFA del propio usuario es `tenant_member`; los pasos de
> `verify`/`login` son anónimos porque ocurren **durante** el login
> (aún sin sesión completa). MFA (TOTP/WebAuthn) y SCIM **siguen
> funcionando sin cambios** (ortogonales al scope del provider): SCIM
> per-tenant se autentica por su propio token Bearer (no JWT de usuario).
>
> **CRUD de config SSO** (`/auth/sso/config`, `/auth/sso/saml/config`): el
> gate sigue siendo `tenant_admin` en código (la superficie System Admin
> del admin-panel vive bajo el grupo **Plataforma**); la tabla
> `sso_configurations` es **platform-global** (sin RLS / `tenant_id`) y hay
> a lo sumo **una** config por `provider` para toda la plataforma (un
> segundo `POST` → 409).

### Webhooks entrantes (Plan 13)

| Endpoint                                               | Método      | Auth / Rol           |
| ------------------------------------------------------ | ----------- | -------------------- |
| `/projects/{id}/incoming-webhooks`                     | GET, POST   | `tenant_admin`       |
| `/projects/{id}/incoming-webhooks/{cfg}`               | PUT, DELETE | `tenant_admin`       |
| `/projects/{id}/incoming-webhooks/{cfg}/rotate-secret` | POST        | `tenant_admin`       |
| `/webhooks/incoming/{origin}/{config_id}`              | POST        | firma HMAC (sin JWT) |

> El endpoint público de recepción (`/webhooks/incoming/...`) se valida
> por firma HMAC del proveedor externo, igual que `review.py`. La
> **configuración** de los webhooks es admin.

### `guardrail_alerts.py` / `guardrail_events.py` (Plan 11)

| Endpoint                       | Método           | Rol mínimo     |
| ------------------------------ | ---------------- | -------------- |
| `/guardrails/alert-rules`      | GET, POST        | `tenant_admin` |
| `/guardrails/alert-rules/{id}` | GET,PATCH,DELETE | `tenant_admin` |
| `/guardrails/events`           | GET              | `tenant_admin` |
| `/guardrails/dashboard`        | GET              | `tenant_admin` |

### `budget_pause.py` (Plan 11)

| Endpoint         | Método | Rol mínimo     |
| ---------------- | ------ | -------------- |
| `/budgets/pause` | GET    | `tenant_admin` |
| `/budgets/pause` | POST   | `tenant_admin` |

### `evals.py` / `eval_quality.py` (Plan 14)

Todos los recursos de evals (datasets, criteria, items, runs, dashboard)
son `tenant_admin`. Detalle en [evals-stats.md](./evals-stats.md).

| Endpoint                                                             | Método | Rol mínimo     |
| -------------------------------------------------------------------- | ------ | -------------- |
| `/eval-datasets`, `/eval-criteria`, `/eval-dataset-items` (+`/{id}`) | \*     | `tenant_admin` |
| `/eval-runs/{id}`, `/eval-runs/diff`, `/eval-quality/*`              | GET    | `tenant_admin` |

### `tenant_stats.py` / `cross_tenant_stats.py` (Plan 14)

| Endpoint                    | Método | Rol mínimo     |
| --------------------------- | ------ | -------------- |
| `/tenant-stats/dashboard`   | GET    | `tenant_admin` |
| `/tenant-stats/consumption` | GET    | `tenant_admin` |
| `/tenant-stats/runs`        | GET    | `tenant_admin` |
| `/tenant-stats/runs/export` | GET    | `tenant_admin` |
| `/admin/cross-tenant-stats` | GET    | `system_admin` |

> El dashboard agregado por tenant es `tenant_admin`; el cross-tenant
> (todas las orgs) corre sobre BYPASSRLS y es `system_admin`.

### `notifications.py` / `assistant.py` (Plan 10)

| Endpoint                                 | Método          | Rol mínimo                 |
| ---------------------------------------- | --------------- | -------------------------- |
| `/notifications/platform/channel-types`  | GET             | `tenant_member`            |
| `/notifications/platform/channel-types`  | PUT             | `system_admin`             |
| `/notifications/channels` (+`/{id}`)     | GET             | `tenant_member`            |
| `/notifications/channels` (+`/{id}`)     | POST,PUT,DELETE | `tenant_admin`             |
| `/notifications/preferences`             | GET             | `tenant_member`            |
| `/notifications/preferences` (+`/{id}`)  | PUT, DELETE     | `tenant_admin`             |
| `/notifications/logs`, `/logs/*/read`    | GET, POST       | `tenant_member`            |
| `/assistant/chat`, `/assistant/identity` | \*              | `require_assistant_access` |

> `require_assistant_access` gobierna el acceso al asistente personal
> (habilitado por tenant). Las preferencias y canales propios son lectura
> de member; crearlos/editarlos es admin.

### `backup.py` (Plan 12)

| Endpoint                                 | Método | Rol mínimo      |
| ---------------------------------------- | ------ | --------------- |
| `/admin/backup/schedule`                 | GET    | `tenant_member` |
| `/admin/backup/schedule`                 | PUT    | `system_admin`  |
| `/admin/backup/destinations`             | GET    | `tenant_member` |
| `/admin/backup/destinations`             | PUT    | `system_admin`  |
| `/admin/backup/destinations/{name}/test` | POST   | `system_admin`  |
| `/admin/backup/restore/*`                | \*     | `system_admin`  |

> Restaurar es estrictamente `system_admin` (corre sobre BYPASSRLS). Ver
> [backup-restore.md](./backup-restore.md) +
> [runbooks de DR](../06-runbooks/dr-full-restore.md).

### `invitations.py` — invitaciones de registro (ADR 0134)

| Endpoint                         | Método | Rol mínimo     |
| -------------------------------- | ------ | -------------- |
| `/admin/invitations`             | POST   | `system_admin` |
| `/admin/invitations`             | GET    | `system_admin` |
| `/admin/invitations/{id}/revoke` | POST   | `system_admin` |

> El operador cerró el registro público en el
> [ADR 0134](../05-architecture-decisions/0134-auto-registro-en-produccion.md)
> (opción C): se entra con un token de invitación. Emitir uno es, en la práctica,
> **conceder acceso a la plataforma**, así que los tres verbos son
> `system_admin` — no `tenant_admin`.
>
> El token se guarda **hasheado**, nunca en claro, y solo se muestra una vez al
> emitirlo (mismo trato que `api_tokens` y `scim_tokens`).
>
> **La excepción de arranque**: si la tabla `users` está vacía, `POST /auth/register`
> acepta sin invitación y promociona a ese primer usuario — si no, una instalación
> nueva quedaría inaccesible para siempre, y con ella el rol System Owner, que es
> el único que abre el córtex.

### `docs_viewer.py` — visor de docs por proyecto (Plan 15)

El visor lee el árbol `docs/` del repo del proyecto (no muta nada).

| Endpoint                               | Método | Rol mínimo      |
| -------------------------------------- | ------ | --------------- |
| `/projects/{id}/docs/tree`             | GET    | `tenant_member` |
| `/projects/{id}/docs/content`          | GET    | `tenant_member` |
| `/projects/{id}/docs/diff`             | GET    | `tenant_member` |
| `/projects/{id}/docs/search`           | GET    | `tenant_member` |
| `/projects/{id}/docs/semantic-search`  | GET    | `tenant_member` |
| `/projects/{id}/docs/export/{zip,pdf}` | GET    | `tenant_member` |

> El System Admin tiene además su propio visor en el admin-panel
> (`/admin/docs`), que renderiza esta misma carpeta `docs/` desde el
> repositorio del sistema — se actualiza solo al cambiar los `.md`.

### API pública v1 (`api_v1/`) — auth por token de scope (Plan 13)

Los endpoints `/api/v1/*` **no** usan los roles de tenant: se autentican
por **API token** con scope `read` o `write` (`require_scope`), no por
JWT de usuario. Contrato completo + lista de recursos en
[public-api.md](./public-api.md).

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
