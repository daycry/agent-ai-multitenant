---
title: Modelo de dominio
audience: backend-dev, architect, technical-writer
phase: cross-cutting
updated: 2026-06-02
---

# Modelo de dominio

Esta página es la **referencia canónica del modelo relacional** del
sistema. El núcleo (agentes, skills, tools, teams, proyectos, planes,
tareas, políticas de aprobación) lo introdujo el **Plan 01** sobre las
fundaciones del Plan 00; las fases posteriores añadieron memoria, RAG,
ejecuciones, presupuestos, agentes humanos, proveedores LLM, marketplace,
etc. El código autoritativo vive en
[`apps/api-server/src/api_server/db/domain.py`](../../apps/api-server/src/api_server/db/domain.py)
(+ los módulos `db/llm_providers.py`, `db/model_prices.py`,
`db/marketplace.py`, `db/exchange_rates.py`, `db/human_metrics.py`, …) y
las migraciones Alembic en
[`apps/api-server/migrations/versions/`](../../apps/api-server/migrations/versions).
Para la matriz de endpoints y los gates de cada recurso, ver
[rbac.md](./rbac.md).

> **Doctrina (CLAUDE.md §1):** toda tabla de dominio lleva
> `tenant_id UUID NOT NULL` y está cubierta por una RLS policy que la
> filtra por `current_setting('app.tenant_id')`. Las únicas tablas sin
> `tenant_id` son: (a) las uniones puras (`agent_skills`, `agent_tools`,
> `team_members`, `task_dependencies`), que heredan el ámbito de sus
> padres vía `FK ON DELETE CASCADE`; y (b) las tablas **platform-global**
> de la sección final (`llm_providers`, `model_prices`,
> `exchange_rates`, fuentes del marketplace), gestionadas solo por
> `system_admin` sobre el engine BYPASSRLS — ver
> [ADR 0028](../05-architecture-decisions/0028-platform-global-providers.md).

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

> Los valores son los del código (`db/domain.py`); las StrEnum se
> persisten como TEXT y se validan con CHECK en migración (no PG ENUM).

| Enum                        | Valores                                                                                                                                                                             | Uso                                                  |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `AgentType`                 | `ai`, `human`                                                                                                                                                                       | IA vs Human Agent (CHECK `ck_agents_agent_type`).    |
| `AgentScope`                | `global_builtin`, `global_tenant_template`, `project_local`                                                                                                                         | Visibilidad y editabilidad del agente.               |
| `AgentRole`                 | `project_manager`, `architect`, `backend_dev`, `frontend_dev`, `qa`, `reviewer`, `leader`, `worker`, `specialist`, `researcher`, `devops`, `security`, `technical_writer`, `custom` | Identidad funcional del agente.                      |
| `SkillCategory`             | `coding`, `review`, `planning`, `research`, `devops`, `data`, `docs`, `qa`, `security`                                                                                              | Tipado del catálogo de skills (también texto libre). |
| `ToolImplementationType`    | `builtin`, `python_function`, `http_endpoint`, `mcp_tool`, `docker_command`                                                                                                         | Cómo se invoca el tool.                              |
| `ToolSecurityLevel`         | `safe`, `sandboxed`, `privileged`                                                                                                                                                   | Gobierna prompts y validación humana.                |
| `AgentSkillProficiency`     | `basic`, `standard`, `expert`                                                                                                                                                       | Match score en orquestación.                         |
| `MemoryScope`               | `private`, `team_shared`, `project_shared`, `global`                                                                                                                                | Scope de indexación vectorial.                       |
| `MemoryType`                | `episodic`, `semantic`                                                                                                                                                              | Tipo de memoria que destila el memorizer.            |
| `ProjectStatus`             | `active`, `paused`, `archived`                                                                                                                                                      | Soft state.                                          |
| `BudgetPeriod`              | `weekly`, `monthly`, `quarterly`, `yearly`, `custom`                                                                                                                                | Ventana de reset de presupuestos.                    |
| `PlanStatus`                | `draft`, `pending_approval`, `pending_second_approval`, `approved`, `in_progress`, `blocked`, `pending_human_validation`, `completed`, `cancelled`, `rejected`, `archived`          | Ciclo de vida del plan (firma simple/doble).         |
| `TaskStatus`                | `backlog`, `ready`, `assigned_to_human`, `in_progress`, `awaiting_human_approval`, `in_review`, `blocked`, `done`, `cancelled`                                                      | Columnas del Kanban + estados humanos/aprobación.    |
| `TaskPriority`              | `low`, `medium`, `high`, `critical`                                                                                                                                                 | Ordenación dentro de cada columna.                   |
| `TaskComplexity`            | `xs`, `s`, `m`, `l`, `xl`                                                                                                                                                           | Estimación de esfuerzo.                              |
| `ExecutionStatus`           | `running`, `done`, `aborted`, `failed`, `awaiting_human_approval`                                                                                                                   | Estado de un run del agente IA.                      |
| `DocumentStatus`            | `pending`, `processing`, `indexed`, `failed`                                                                                                                                        | Ingestión de documentos en una KB.                   |
| `ApprovalRequestStatus`     | `pending`, `approved`, `rejected`, `timed_out`                                                                                                                                      | Request de validación humana.                        |
| `AssignmentMode`            | `specific_user` (MVP), `role_queue`, `team_pool`                                                                                                                                    | Cómo se rutea una tarea humana (Plan 16).            |
| `HumanTaskReviewMode`       | `auto_approve`, `peer_human_reviewer`                                                                                                                                               | Revisión del entregable humano (por proyecto).       |
| `HumanTaskAssignmentStatus` | `pending_acceptance`, `accepted`, `reassigned`, `declined`, `expired`                                                                                                               | Ciclo de aceptación de una asignación humana.        |
| `LLMProviderKind`           | `claude_sdk`, `copilot`, `azure_foundry`, `ollama`                                                                                                                                  | Catálogo cerrado de proveedores (ADR 0021).          |
| `MarketplaceListingKind`    | `skill`, `tool`, `mcp_server`                                                                                                                                                       | Qué instala un listing del marketplace.              |
| `MarketplaceTrustLevel`     | `verified`, `community`, `experimental`                                                                                                                                             | Nivel de confianza (gobierna guardrails).            |
| `InstallationStatus`        | `enabled`, `disabled`, `revoked`                                                                                                                                                    | Estado de una instalación del marketplace.           |

## Entidades

### `agents`

| Columna                                   | Tipo                  | Notas                                                                                                                                  |
| ----------------------------------------- | --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                      | `UUID PK`             | uuid7                                                                                                                                  |
| `tenant_id`                               | `UUID NOT NULL`       | NULL no permitido; built-ins viven bajo el tenant plataforma `00000000-0000-0000-0000-000000000001`.                                   |
| `scope`                                   | `agent_scope`         | `global_builtin` (read-only para tenants) / `global_tenant_template` (tenant template) / `project_local` (fork dentro de un proyecto). |
| `project_id`                              | `UUID NULL`           | Solo poblado para scope `project_local`.                                                                                               |
| `parent_agent_id`                         | `UUID NULL`           | Apunta al agente origen cuando se forkea un global a project_local.                                                                    |
| `role`                                    | `agent_role`          | Identidad funcional.                                                                                                                   |
| `agent_type`                              | `text` (`ai`/`human`) | `ai` por defecto. `human` = **Human Agent**: su config vive en `human_agent_config` (ver sección final). CHECK `ck_agents_agent_type`. |
| `name`, `description`                     | `text`                | Bilingüe ES/EN: el sufijo `_en` se almacena en `description_en`, `system_prompt_en`.                                                   |
| `system_prompt`                           | `text NOT NULL`       | Prompt base. El fork copia este campo.                                                                                                 |
| `llm_provider`, `llm_model`, `llm_config` | `text / jsonb`        | Provider desacoplado del catálogo cerrado (ADR 0021): `claude_sdk`, `copilot`, `azure_foundry`, `ollama`.                              |
| `temperature`, `max_tokens`               | `numeric / int`       | Defaults sensatos en la migración.                                                                                                     |
| `is_builtin`                              | `bool DEFAULT false`  | Bandera redundante con scope=global_builtin que simplifica RLS.                                                                        |
| `deleted_at`                              | `timestamptz NULL`    | Soft delete.                                                                                                                           |

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

| Columna                                                | Tipo                                          | Notas                                                                                                                                                         |
| ------------------------------------------------------ | --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                                                   | `UUID PK`                                     |                                                                                                                                                               |
| `tenant_id`                                            | `UUID NOT NULL`                               |                                                                                                                                                               |
| `name`, `description`                                  | `text`                                        | bilingüe                                                                                                                                                      |
| `status`                                               | `project_status`                              |                                                                                                                                                               |
| `team_id`                                              | `UUID NULL`                                   | Team principal del proyecto (opcional).                                                                                                                       |
| `mcp_servers`                                          | `jsonb DEFAULT '[]'`                          | Lista de MCP servers configurados.                                                                                                                            |
| `rag_knowledge_bases`                                  | `jsonb DEFAULT '[]'`                          | KBs vectoriales (Plan 03 lo activa).                                                                                                                          |
| `worker_config`                                        | `jsonb DEFAULT '{}'`                          | Overrides de cola / runtime template.                                                                                                                         |
| `repository_config`                                    | `jsonb NULL`                                  | URL del bare repo + ramas.                                                                                                                                    |
| `human_approval_policy`                                | `jsonb NULL`                                  | Snapshot copy del `ApprovalPolicyTemplate` adoptado.                                                                                                          |
| `allowed_commands`                                     | `text[]`                                      | Allowlist **deny-by-default** de basenames de binarios que `shell_exec` puede correr (`php`, `composer`, `npm`, `vendor/bin/phpunit`…). `[]` = no corre nada. |
| `default_runtime_template`                             | `varchar(64) NULL`                            | Runtime template por defecto (`php-phpunit`, `node-jest`…) contra el que resuelven los tools `run_*`. NULL = default de cada tool.                            |
| `default_kb_grants`                                    | `text[]`                                      | Solo en `is_template`: KBs (por slug) que el wizard auto-concede al clonar.                                                                                   |
| `budget_amount`, `budget_currency`, `budget_period`, … | `numeric / text / text`                       | Presupuesto por proyecto (consumo + alertas + auto-pausa). `paused_by_budget` lo marca.                                                                       |
| `budget_includes_human_cost`                           | `bool DEFAULT false`                          | Si `true`, el coste humano (rate×horas, convertido a USD) cuenta contra el presupuesto del proyecto.                                                          |
| `human_task_review_mode`                               | `text` (`auto_approve`/`peer_human_reviewer`) | Cómo se revisa el entregable de una tarea humana al enviarse (Plan 16). DEFAULT `auto_approve`.                                                               |
| `is_template`                                          | `bool`                                        | `true` para las plantillas seedeadas; tenant clona vía wizard.                                                                                                |
| `deleted_at`                                           | `timestamptz`                                 | Soft delete.                                                                                                                                                  |

> `allowed_commands` + `default_runtime_template` son el catálogo de
> tools políglota por proyecto —
> [ADR 0045](../05-architecture-decisions/0045-comandos-shell-por-proyecto-y-runtime-por-stack.md),
> [guía](../03-guides/comandos-y-runtime-por-proyecto.md). El presupuesto
> y la conversión FX, en [pricing.md](./pricing.md).

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

## Endpoints REST (núcleo del dominio)

El núcleo CRUD del dominio. La **superficie completa** de endpoints del
sistema final (human agents, marketplace, proveedores LLM, SSO/MFA,
webhooks, evals, stats, backup…) con su rol mínimo está en
[rbac.md](./rbac.md).

| Recurso                | Métodos                                                                                                                                                     |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/agents`              | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}`, `POST /{id}/fork`, `GET /{id}/diff`, `POST /{id}/merge`                                            |
| `/agents/{id}/tools`   | `GET`, `PUT` (asignación declarativa de tools por agente — [ADR 0044](../05-architecture-decisions/0044-per-agent-tool-assignment-y-taxonomia-derivada.md)) |
| `/skills`, `/tools`    | `GET`, `POST`, `PUT /{id}`, `DELETE /{id}` (los built-in devuelven 404 ante writes — sin info-leak)                                                         |
| `/teams`               | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}` + `POST/PUT/DELETE /{id}/members[/{agent_id}]`                                                      |
| `/projects`            | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}` (filtros: `include_templates`, `team_id`, `status`)                                                 |
| `/projects/{id}/tasks` | `GET`, `GET /{tid}`, `POST`, `PUT /{tid}`, `DELETE /{tid}`                                                                                                  |
| `/human-agents`        | `GET`, `GET /{id}`, `POST`, `PUT /{id}`, `DELETE /{id}`, `POST /{id}/fork`, `GET /templates`, `GET /assignable-users`                                       |
| `/inbox/*`             | Bandeja del Human Agent: `assignments`, `accept`/`reject`/`complete`/`escalate`, `reviews`, `metrics`, `history`                                            |
| `/admin/llm-providers` | `GET`, `POST`, `GET/PUT/DELETE /{id}`, `POST /{id}/test` (platform-global, `system_admin`)                                                                  |
| `/approval-policies`   | `GET` (read-only catálogo, filtro `?builtin_only=true`)                                                                                                     |

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

## Extensiones posteriores al Plan 01

El núcleo de arriba se amplió fase a fase. Esta sección resume las
entidades/tablas añadidas; cada subsistema tiene además su referencia
dedicada (enlazada al final). Todas son tenant-scoped (RLS) **salvo** las
cuatro platform-global marcadas explícitamente.

### Memoria y RAG

- `memories` — entradas de memoria del agente con `scope` (`private` /
  `team_shared` / `project_shared` / `global`), `memory_type`
  (`episodic` / `semantic`) y embedding pgvector. Indexa el **memorizer**.
- `knowledge_bases`, `documents`, `document_chunks`, `kb_categories`,
  uniones KB↔proyecto — RAG con pgvector + ingestión **Docling**. Ver
  [guía de ingestión](../03-guides/kb-ingestion.md).

### Ejecución, conversación y aprobaciones

- `executions` — una fila por run del loop del agente IA, con el
  **snapshot de coste** por llamada (tokens, modelo, precio aplicado,
  coste canónico en USD). Es el equivalente IA de `human_work_sessions`.
- `conversations`, `messages`, `custom_chat_modes` — chat / planning
  sub-graph: una conversación produce un Plan en borrador.
- `approval_requests` — request de validación humana sobre una acción
  sensible (estado `pending`/`approved`/`rejected`/`timed_out`); aparca
  la tarea en `awaiting_human_approval` —
  [ADR 0020](../05-architecture-decisions/0020-task-awaiting-human-approval.md).

### Human Agents (Plan 16)

`agent_type='human'` extiende la entidad `Agent` existente; lo
específico de humanos vive en tres tablas tenant-scoped. Ver
[guía de Human Agents](../03-guides/human-agents.md) y
[ADR 0046](../05-architecture-decisions/0046-human-agents-agent-type-y-workflows-mixtos.md).

- **`human_agent_config`** (1:1 con el agente humano, `agent_id` UNIQUE):
  `assignment_mode` (MVP: `specific_user`, CHECK), `assigned_user_id`
  (FK a `users`, SET NULL), `hourly_rate` + `hourly_rate_currency`
  (coste humano), `notification_channels` (jsonb), `acceptance_timeout_hours`
  (default 24), `escalation_target_user_id`, y estimaciones de planning
  (`expected_response_time_hours`, `expected_execution_time_hours`).
- **`human_work_sessions`** (reemplaza `executions` para tareas humanas):
  `task_id`, `user_id`, `start_at`/`end_at`, `hours_logged`, `comments`,
  `output_files_attached` (jsonb). Append-only (no soft-delete): es el
  registro de auditoría de lo que hizo el humano.
- **`human_task_assignments`** (a quién está asignada una tarea humana +
  ciclo de aceptación): `task_id`, `human_agent_id`, `assigned_to_user_id`,
  `assigned_at`, `status` (`pending_acceptance` / `accepted` /
  `reassigned` / `declined` / `expired`, CHECK). El job de
  acceptance-timeout escala al `escalation_target_user_id`.

Enums asociados: `AssignmentMode`, `HumanTaskReviewMode`
(`auto_approve` / `peer_human_reviewer`, ajuste por proyecto),
`HumanTaskAssignmentStatus`. Las métricas por usuario
(`compute_user_metrics` en `db/human_metrics.py`) agregan estas tablas
para `GET /inbox/metrics` y para que el PM dimensione tareas humanas. El
estado de tarea `assigned_to_human` y el `awaiting_human_approval` son
nuevos en `TaskStatus`.

### Proveedores LLM y precios (platform-global)

> **Sin `tenant_id`, sin RLS.** Gestionadas solo por `system_admin`
> sobre el engine BYPASSRLS —
> [ADR 0028](../05-architecture-decisions/0028-platform-global-providers.md).

- **`llm_providers`** (platform-global) — un proveedor configurado del
  catálogo cerrado (ADR 0021): `kind` (`claude_sdk` / `copilot` /
  `azure_foundry` / `ollama`, CHECK), `display_name`, `base_url`
  (APIM/Ollama; NULL para Claude SDK), `secret_vault_path` (puntero a la
  credencial en Vault `platform/llm/<id>` — el valor **nunca** se guarda
  en BD), `config` (jsonb no-secreto), `is_active`. Gestión en
  `/admin/llm-providers`; ver
  [guía de proveedores LLM](../03-guides/configurar-proveedores-llm.md).
- **`model_prices`** (platform-global, read-open para members) —
  catálogo de precios con vigencia (effective-dated), `modality`,
  `provider` (familia: "anthropic", "openai"…) y desde Plan 11.2
  **`provider_id`** (FK nullable a `llm_providers`, SET NULL). Alimentado
  por el sync LiteLLM **limitado a las familias de proveedores activos**.
- **`exchange_rates`** (platform-global, read-open) — tipos de cambio FX
  para convertir coste a la moneda canónica (USD). `price_snapshots` y
  los presupuestos consumen ambos. Ver [pricing.md](./pricing.md).

### Guardrails

- `guardrail_alert_rules`, `guardrail_events`, `outlier_alert_rule`,
  `budget_alert_state` — motor de guardrails declarativos por capas
  (plataforma → tenant → proyecto) en pre/post_llm y pre/post_tool, con
  eventos y reglas de alerta. Ver [guardrails.md](./guardrails.md).

### Marketplace

Ver [marketplace.md](./marketplace.md). Las fuentes son tenant-agnósticas
(platform-level); el resto es tenant-scoped.

- `marketplace_sources` — registry/fuente de listings: `source_type`
  (`official` / `private` / `git` / `url`); `owner_tenant_id` nullable
  marca el catálogo privado de un tenant.
- `marketplace_listings` — un listing: `kind` (`skill` / `tool` /
  `mcp_server`), `trust_level` (`verified` / `community` /
  `experimental`, gobierna los guardrails aplicados).
- `marketplace_installations` — instalación por tenant: `status`
  (`enabled` / `disabled` / `revoked`).
- `marketplace_shares` — compartición entre proyectos/tenants.
- `marketplace_audit_entries` — auditoría append-only (`install`,
  `uninstall`, `revoke`, `consent`, `consent_denied`, `enable`,
  `disable`, `update`, `share`).

### Plataforma empresarial

- **Auth/SSO/MFA**: configs OIDC/SAML por tenant, credenciales TOTP +
  WebAuthn, tokens SCIM. Ver [auth-sso.md](./auth-sso.md).
- **API pública + webhooks**: `api_tokens` (por scope read/write),
  configs de webhooks entrantes + entregas salientes. Ver
  [public-api.md](./public-api.md).
- **Notificaciones**: canales y preferencias multicanal + asistente
  personal. Ver [notifications.md](./notifications.md).
- **Evals + stats**: `eval_datasets`, `eval_criteria`,
  `eval_dataset_items`, `eval_runs` + agregados de estadísticas.
  Ver [evals-stats.md](./evals-stats.md).
- **Tareas no agrupadas**: además de `task.plan_id`, la tabla `plans`
  está activa (Plan = DAG con rama git); ver `PlanStatus` arriba.

## Ver también

- [ADR 0006 — Linked vs Forked](../05-architecture-decisions/0006-linked-vs-forked-agents.md)
- [ADR 0007 — Estrategia de seeds idempotentes](../05-architecture-decisions/0007-idempotent-seed-strategy.md)
- [ADR 0008 — Doble Kanban (Planes + Tareas)](../05-architecture-decisions/0008-dual-kanban-planes-tareas.md)
- [ADR 0010 — Superadmin cross-tenant via BYPASSRLS + X-Tenant-Id](../05-architecture-decisions/0010-superadmin-cross-tenant.md)
- [ADR 0028 — Proveedores platform-global](../05-architecture-decisions/0028-platform-global-providers.md)
- [ADR 0046 — Human Agents como agent_type](../05-architecture-decisions/0046-human-agents-agent-type-y-workflows-mixtos.md)
- [RBAC — Matriz de roles por endpoint](./rbac.md)
- [Arquitectura end-to-end](../context/architecture-overview.md) ·
  [Glosario](../context/glossary.md)
- [Guía — Crear tu primer proyecto](../03-guides/01-create-first-project.md)
