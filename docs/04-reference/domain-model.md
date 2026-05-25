---
title: Modelo de dominio mínimo (Plan 01)
audience: backend-dev, architect, technical-writer
phase: 01-dominio-minimo
updated: 2026-05-21
---

# Modelo de dominio — Plan 01 "Dominio Mínimo"

Esta página documenta el esquema introducido por **Plan 01** sobre las
fundaciones técnicas del Plan 00. Es la referencia canónica del modelo
relacional; el código autoritativo vive en
[`apps/api-server/src/api_server/db/domain.py`](../../apps/api-server/src/api_server/db/domain.py)
y las migraciones Alembic
[`apps/api-server/migrations/versions/20260521_0002..0008_*.py`](../../apps/api-server/migrations/versions).

> **Doctrina (CLAUDE.md §1):** toda tabla de dominio lleva
> `tenant_id UUID NOT NULL` y está cubierta por una RLS policy que la
> filtra por `current_setting('app.tenant_id')`. Las únicas tablas sin
> `tenant_id` son las uniones puras (`agent_skills`, `agent_tools`,
> `team_members`, `task_dependencies`), que heredan el ámbito de sus
> padres vía `FK ON DELETE CASCADE`.

## Vista panorámica

```
                           +------------------+
                           |   ApprovalPolicy |  (catálogo de
                           |    Template      |   presets seedeados)
                           +---------+--------+
                                     |
              +----------------------+----------------------+
              |                                             |
       +------v------+   N..1 (linked / forked)      +------v------+
       |   Project   |<------------------------------+    Agent    |
       +------+------+                               +------+------+
              | 1                                          |
              |                                            |
      +-------+-------+                            +-------+-------+
      |       |       |                            |       |       |
      v       v       v                            v       v       v
   Plan    Task    Team                          Skill   Tool   (memberships)
                     |
                     +-- TeamMember (agent_id) ----^
```

Las relaciones N..N llevan FK compuestas que repiten `tenant_id` para
mantener la consistencia con la RLS (sin la duplicidad, una junction
puede acabar referenciando filas de otro tenant tras una RLS bypass
no intencionada).

## Catálogo de tipos enumerados

| Enum                     | Valores                                                                                                                                                                    | Uso                                                                 |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| `AgentScope`             | `global_builtin`, `global_tenant_template`, `project_local`                                                                                                                | Determina visibilidad y editabilidad del agente.                    |
| `AgentRole`              | `project_manager`, `architect`, `backend_dev`, `frontend_dev`, `qa`, `reviewer`, `devops`, `technical_writer`, `data_scientist`, `security_engineer`, `personal_assistant` | Identidad funcional del agente.                                     |
| `AgentType`              | `worker`, `coordinator`, `assistant`                                                                                                                                       | Tipo de ejecución (Plan 02 usa `coordinator` para LangGraph nodes). |
| `SkillCategory`          | `coding`, `review`, `testing`, `analysis`, `communication`, `infrastructure`                                                                                               | Tipado del catálogo de skills.                                      |
| `ToolImplementationType` | `builtin`, `mcp`, `http_api`, `local_command`                                                                                                                              | Cómo se invoca el tool.                                             |
| `ToolSecurityLevel`      | `safe`, `restricted`, `privileged`                                                                                                                                         | Gobierna prompts y validación humana.                               |
| `AgentSkillProficiency`  | `novice`, `intermediate`, `expert`                                                                                                                                         | Match score en orquestación.                                        |
| `ProjectStatus`          | `active`, `paused`, `archived`                                                                                                                                             | Soft state — no afecta a borrado.                                   |
| `BudgetPeriod`           | `daily`, `weekly`, `monthly`, `total`                                                                                                                                      | Reset window de presupuestos.                                       |
| `PlanStatus`             | `pending_approval`, `approved`, `in_progress`, `blocked`, `pending_human_validation`, `completed`, `cancelled`, `rejected`, `archived`                                     | Espejo del frontmatter del roadmap (Plan 02 lo usará).              |
| `TaskStatus`             | `backlog`, `ready`, `in_progress`, `in_review`, `blocked`, `done`, `cancelled`                                                                                             | Columnas del Kanban operativo.                                      |
| `TaskPriority`           | `low`, `medium`, `high`, `critical`                                                                                                                                        | Ordenación dentro de cada columna.                                  |
| `TaskComplexity`         | `xs`, `s`, `m`, `l`, `xl`                                                                                                                                                  | Estimación de esfuerzo.                                             |
| `MemoryScope`            | `private`, `team_shared`, `project_shared`, `global`                                                                                                                       | Plan 03 lo usa para indexación vectorial.                           |

## Entidades

### `agents`

| Columna                                   | Tipo                 | Notas                                                                                                                                  |
| ----------------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                      | `UUID PK`            | uuid7                                                                                                                                  |
| `tenant_id`                               | `UUID NOT NULL`      | NULL no permitido; built-ins viven bajo el tenant plataforma `00000000-0000-0000-0000-000000000001`.                                   |
| `scope`                                   | `agent_scope`        | `global_builtin` (read-only para tenants) / `global_tenant_template` (tenant template) / `project_local` (fork dentro de un proyecto). |
| `project_id`                              | `UUID NULL`          | Solo poblado para scope `project_local`.                                                                                               |
| `parent_agent_id`                         | `UUID NULL`          | Apunta al agente origen cuando se forkea un global a project_local.                                                                    |
| `role`                                    | `agent_role`         | Identidad funcional.                                                                                                                   |
| `agent_type`                              | `agent_type`         | worker por defecto.                                                                                                                    |
| `name`, `description`                     | `text`               | Bilingüe ES/EN: el sufijo `_en` se almacena en `description_en`, `system_prompt_en`.                                                   |
| `system_prompt`                           | `text NOT NULL`      | Prompt base. El fork copia este campo.                                                                                                 |
| `llm_provider`, `llm_model`, `llm_config` | `text / jsonb`       | Provider desacoplado del catálogo cerrado (ADR 0021): `claude_sdk`, `copilot`, `azure_foundry`, `ollama`.                              |
| `temperature`, `max_tokens`               | `numeric / int`      | Defaults sensatos en la migración.                                                                                                     |
| `is_builtin`                              | `bool DEFAULT false` | Bandera redundante con scope=global_builtin que simplifica RLS.                                                                        |
| `deleted_at`                              | `timestamptz NULL`   | Soft delete.                                                                                                                           |

**Linked vs Forked.** Un agente con `scope = project_local` y
`parent_agent_id != NULL` es un **fork**: copia editable creada con
`POST /agents/{id}/fork`. Un team puede añadir agentes globales por
referencia (**linked**) o forkearlos antes para personalizarlos —
ver [ADR 0006](../05-architecture-decisions/0006-linked-vs-forked-agents.md).

### `skills`, `tools`

Mismo patrón catálogo + custom. `is_builtin = true` se sirve via la
policy `<table>_builtin_read` (lectura cross-tenant); las creaciones
custom de un tenant son privadas a ese tenant.

Junctions:

- `agent_skills (agent_id, skill_id, proficiency)`
- `agent_tools (agent_id, tool_id, config_overrides jsonb)`

### `teams`

| Columna       | Tipo            | Notas                                                                                                 |
| ------------- | --------------- | ----------------------------------------------------------------------------------------------------- |
| `id`          | `UUID PK`       |                                                                                                       |
| `tenant_id`   | `UUID NOT NULL` |                                                                                                       |
| `name`        | `varchar(120)`  |                                                                                                       |
| `description` | `text NULL`     |                                                                                                       |
| `is_builtin`  | `bool`          | Si `true` el panel marca el team read-only y el endpoint rechaza PUT/DELETE/POST de miembros con 403. |
| `deleted_at`  | `timestamptz`   |                                                                                                       |

`team_members (team_id, agent_id, role_in_team, is_team_leader,
assignment_priority)` — agent_id puede apuntar a cualquier
scope visible por RLS (built-in o forked del propio tenant).

### `projects`

| Columna                 | Tipo                 | Notas                                                            |
| ----------------------- | -------------------- | ---------------------------------------------------------------- |
| `id`                    | `UUID PK`            |                                                                  |
| `tenant_id`             | `UUID NOT NULL`      |                                                                  |
| `name`, `description`   | `text`               | bilingüe                                                         |
| `status`                | `project_status`     |                                                                  |
| `team_id`               | `UUID NULL`          | Team principal del proyecto (opcional).                          |
| `mcp_servers`           | `jsonb DEFAULT '[]'` | Lista de MCP servers configurados.                               |
| `rag_knowledge_bases`   | `jsonb DEFAULT '[]'` | KBs vectoriales (Plan 03 lo activa).                             |
| `worker_config`         | `jsonb DEFAULT '{}'` | Overrides de cola / runtime template.                            |
| `repository_config`     | `jsonb NULL`         | URL del bare repo + ramas.                                       |
| `human_approval_policy` | `jsonb NULL`         | Snapshot copy del `ApprovalPolicyTemplate` adoptado.             |
| `is_template`           | `bool`               | `true` para las 8 plantillas seedeadas; tenant clona vía wizard. |
| `deleted_at`            | `timestamptz`        | Soft delete.                                                     |

### `plans`, `tasks`

`plans` queda como tabla preparatoria que Plan 02 activará. En Plan 01
las tareas se agrupan por `project_id`; el campo `task.plan_id` está
listo pero queda en `NULL`.

`tasks`:

| Columna                                  | Tipo                            | Notas                          |
| ---------------------------------------- | ------------------------------- | ------------------------------ |
| `id`                                     | `UUID PK`                       |                                |
| `tenant_id`, `project_id`                | `UUID NOT NULL`                 |                                |
| `plan_id`                                | `UUID NULL`                     |                                |
| `title`, `description`                   | `varchar / text`                |                                |
| `status`, `priority`                     | `task_status` / `task_priority` | Defaults: `backlog`, `medium`. |
| `assigned_agent_id`, `reviewer_agent_id` | `UUID NULL`                     |                                |
| `acceptance_criteria`                    | `jsonb DEFAULT '[]'`            | Lista de criterios para QA.    |
| `inputs`                                 | `jsonb DEFAULT '{}'`            |                                |
| `estimated_complexity`                   | `task_complexity NULL`          |                                |
| `retry_count`, `max_retries`             | `int / int default 3`           |                                |
| `started_at`, `completed_at`             | `timestamptz NULL`              |                                |

`task_dependencies (task_id, depends_on_task_id)` — junction simple
con `CHECK (task_id <> depends_on_task_id)` para evitar self-loops.
Ciclos N>1 los detecta el orquestador (Plan 02), no la BD.

### `approval_policy_templates`

| Columna       | Tipo            | Notas                                                                  |
| ------------- | --------------- | ---------------------------------------------------------------------- |
| `id`          | `UUID PK`       |                                                                        |
| `tenant_id`   | `UUID NOT NULL` | Built-ins bajo tenant plataforma; tenant-creados son privados.         |
| `name`        | `varchar(120)`  |                                                                        |
| `description` | `text NULL`     |                                                                        |
| `categories`  | `jsonb`         | Estructura: `{"categories": { "<cat>": "auto" \| "human_required" }}`. |
| `is_builtin`  | `bool`          | Visible cross-tenant via policy `_builtin_read`.                       |
| `deleted_at`  | `timestamptz`   |                                                                        |

Categorías canónicas (orden estable, spec §7.7–7.8): `code_changes`,
`git_commit`, `git_push`, `external_http_get`, `external_http_post`,
`secrets_access`, `data_migration`, `production_deploy`,
`infra_provision`, `secret_rotation`, `external_communication`,
`data_export_pii`, `user_management`.

## Seeds built-in

| Categoría               | Cantidad | Fichero                                                             |
| ----------------------- | -------- | ------------------------------------------------------------------- |
| Agentes                 | 11       | `apps/api-server/src/api_server/seeds/builtin_agents.py`            |
| Skills                  | 33       | `apps/api-server/src/api_server/seeds/builtin_skills.py`            |
| Tools                   | 18       | `apps/api-server/src/api_server/seeds/builtin_tools.py`             |
| Teams                   | 5        | `apps/api-server/src/api_server/seeds/builtin_teams.py`             |
| Project templates       | 8        | `apps/api-server/src/api_server/seeds/builtin_project_templates.py` |
| Approval policy presets | 4        | `apps/api-server/src/api_server/seeds/builtin_approval_policies.py` |

Los seeds usan `INSERT … ON CONFLICT (id) DO UPDATE`, con `id` derivado
de `uuid5(namespace, slug)`. Esto los hace idempotentes: re-correr el
seeder no duplica filas y los IDs son estables en cada instalación.

## Endpoints REST implementados

| Recurso                | Métodos                                                                                                          |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `/agents`              | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}`, `POST /{id}/fork`, `GET /{id}/diff`, `POST /{id}/merge` |
| `/skills`, `/tools`    | `GET`, `POST`, `PUT /{id}`, `DELETE /{id}` (los built-in devuelven 404 ante writes — sin info-leak)              |
| `/teams`               | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}` + `POST/PUT/DELETE /{id}/members[/{agent_id}]`           |
| `/projects`            | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}` (filtros: `include_templates`, `team_id`, `status`)      |
| `/projects/{id}/tasks` | `GET`, `GET /{tid}`, `POST`, `PUT /{tid}`, `DELETE /{tid}`                                                       |
| `/approval-policies`   | `GET` (read-only catálogo, filtro `?builtin_only=true`)                                                          |

## Auth y resolución del tenant activo

`auth/deps.py:get_principal` decodifica el JWT y produce el
`AuthPrincipal`. La fuente del `tenant_id` efectivo depende del rol:

| Caso                                | Sesión SA                   | `app.tenant_id`    | Reads                       | Writes                           |
| ----------------------------------- | --------------------------- | ------------------ | --------------------------- | -------------------------------- |
| Tenant user con `tid` en JWT        | `app_user` (NOBYPASSRLS)    | = `tid`            | RLS filtra por `tid`        | tenant_id auto-inyectado = `tid` |
| Tenant user sin `tid`               | `app_user` (NOBYPASSRLS)    | (sin set)          | sólo `<tabla>_builtin_read` | 400 "active tenant required"     |
| Superadmin sin contexto             | `migrations_user` BYPASSRLS | (sin set)          | **todos los tenants**       | 400 "active tenant required"     |
| Superadmin con `X-Tenant-Id` header | `app_user` (NOBYPASSRLS)    | = valor del header | scoped al tenant elegido    | tenant_id = valor del header     |
| Superadmin con `tid` en JWT         | `app_user` (NOBYPASSRLS)    | = `tid`            | scoped a `tid`              | tenant_id = `tid`                |

Reglas:

- El header `X-Tenant-Id` **sólo se respeta para usuarios con
  `is_system_admin=true`**. Si un tenant user lo manda, se ignora
  silenciosamente y la sesión sigue usando el `tid` del JWT — los
  tenants no pueden escapar de su scope.
- `POST /auth/register` auto-promueve al **primer usuario** a
  `is_system_admin=true` dentro de la misma transacción
  (`SELECT id FROM users LIMIT 1` antes del `INSERT`), garantizando
  que dos registers concurrentes no produzcan dos superadmins.
- El tenant plataforma (`00000000-0000-0000-0000-000000000001`) se
  filtra del selector de tenants del panel: está reservado para
  catálogos built-in.

## Ver también

- [ADR 0006 — Linked vs Forked](../05-architecture-decisions/0006-linked-vs-forked-agents.md)
- [ADR 0007 — Estrategia de seeds idempotentes](../05-architecture-decisions/0007-idempotent-seed-strategy.md)
- [ADR 0008 — Doble Kanban (Planes + Tareas)](../05-architecture-decisions/0008-dual-kanban-planes-tareas.md)
- [ADR 0010 — Superadmin cross-tenant via BYPASSRLS + X-Tenant-Id](../05-architecture-decisions/0010-superadmin-cross-tenant.md)
- [Guía — Crear tu primer proyecto](../03-guides/01-create-first-project.md)
