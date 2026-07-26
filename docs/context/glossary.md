---
title: Glosario del Dominio
last_updated: 2026-06-02
status: published
docs_language: es
---

# Glosario del Dominio

Términos del dominio del sistema final. Para el detalle de cada decisión, ver
los ADRs en [`../05-architecture-decisions/`](../05-architecture-decisions/);
para modelos y endpoints, [`../04-reference/`](../04-reference/).

## Entidades Principales

**Organization / Tenant**: organización o departamento. Aísla todos los recursos. Multi-tenant por diseño con RLS.

**Platform tenant**: tenant especial `00000000-0000-0000-0000-000000000001`, fila real en `organizations` que aloja el **catálogo global** (agentes/skills/tools/teams/KBs/plantillas built-in). No es un cliente; está oculto a todos los tenants y solo lo escribe el rol BYPASSRLS. Mecanismo canónico de contenido global (ADR 0029).

**User**: usuario humano del sistema. 4 roles globales: System Admin, System Operator, Tenant Admin, Tenant User. 4 roles funcionales por proyecto: project_owner, project_maintainer, project_contributor, project_viewer.

**Agent**: entidad con rol, skills, tools y memoria propia. Tres scopes: `global_builtin` (sistema), `global_tenant_template` (tenant), `project_local` (proyecto). Modelos linked vs. forked al añadir global a proyecto. Tiene `agent_type ∈ {ai, human}` (ADR 0046).

**agent_type**: enum de `Agent` (default `ai`). Un agente `human` es un humano modelado como Agent, no una entidad nueva — se planifica, orquesta, mide y audita igual que un agente IA (ADR 0046).

**Human Agent**: `Agent` con `agent_type='human'` ejecutado por una persona. Su config vive en la tabla satélite `human_agent_config`. El orquestador NO le pide contenedor: crea un `HumanTaskAssignment` y registra el trabajo en una `HumanWorkSession`. Distinto del aprobador `awaiting_human` (ADR 0020).

**human_agent_config**: tabla 1:1 con el Agent humano: `assigned_user_id`, `hourly_rate`/`currency`, `notification_channels`, `acceptance_timeout_hours`, `escalation_target_user_id`, tiempos estimados (ADR 0046).

**HumanTaskAssignment**: asignación de una tarea a un Human Agent (estado: pendiente/aceptada/escalada…), resuelta a la persona concreta vía `human_agent_config.assigned_user_id`.

**HumanWorkSession**: trazabilidad auditable de una tarea humana (`start_at`, `end_at`, `hours_logged`, `comments`, `output_files_attached`). **Reemplaza a `Execution`** para los pasos humanos; el coste humano = `hours_logged * hourly_rate` en USD canónico.

**Review modes (humanos)**: `project.human_task_review_mode`. `auto_approve` (default): entregar finaliza la tarea (→ `done`). `peer_human_reviewer`: un segundo Human Agent revisa (aprueba → done / rechaza → backlog con retry). El modo `ai_reviewer` queda diferido (ADR 0046).

**Skill**: capacidad declarativa de un agente (descripción de qué sabe hacer). NO ejecutable. Inyecta fragmento de prompt y sugiere tools.

**Tool**: función concreta invocable por un agente. 5 tipos: `builtin`, `python_function`, `http_endpoint`, `mcp_tool`, `docker_command`. Eje `security_level` ∈ {`safe`, `sandboxed`, `privileged`}.

**Tool básica vs avanzada**: taxonomía **derivada** de `is_builtin` (sin columna nueva, ADR 0044). Básica = `is_builtin=true` (las 18 builtin de plataforma, cualquier `implementation_type`). Avanzada = `is_builtin=false` (custom del tenant + tools MCP). El `security_level` es ortogonal a esta dicotomía.

**agent_tools**: junction `(agent_id, tool_id, config_override)` que declara qué tools puede usar un agente. Sin filas ⇒ sin restricción por agente (sentinel `None`); con filas ⇒ restringido a ese set, intersectado en runtime con el allowlist del chat-mode (ADR 0044).

**shell_exec / run_command**: tool builtin **básica + privilegiada** que ejecuta binarios del stack del proyecto como argv (vía `shlex`, sin shell, con timeout y cwd confinado), filtrada por `project.allowed_commands` deny-by-default (ADR 0045).

**Team**: conjunto de agentes con roles complementarios asignado a uno o varios proyectos.

**Project**: contenedor lógico de tareas, equipo, KBs, configuración MCP, política de validación humana, repos asociados. Campos relevantes nuevos: `allowed_commands`, `default_runtime_template` (ADR 0045), `human_task_review_mode`, `budget_includes_human_cost` (ADR 0046), `budget_amount`/period (ADR 0043).

**allowed_commands**: `TEXT[]` por proyecto (deny-by-default, default `'{}'`): los basenames de binario que `shell_exec` puede ejecutar en ese proyecto. Lista vacía ⇒ no ejecuta nada. El operador autoriza explícitamente `php`/`composer`/`npm`/etc. (ADR 0045).

**default_runtime_template**: id del runtime template del stack del proyecto (`php-phpunit`, `node-jest`…) contra el que resuelven los `run_*`. `NULL` = default por-tool (ADR 0045).

**Plan**: conjunto ordenado de tareas con dependencias DAG generado en chat de planning. Materializa como rama git `plan/{id}-{slug}` al sincronizarse; los commits llevan trailers `Plan-Id`/`Task-Id`/`Execution-Id`; al cerrarse se abre un PR.

**Task**: unidad de trabajo en el Kanban. Tiene runtime declarado para tests automáticos y criterios de aceptación. Su assignee puede ser IA o humano. Para exigir un humano en un punto concreto NO hay un flag por tarea (ver principio 7): están las políticas de aprobación por categoría de acción y la tool `ask_human` (ADR 0114).

**Execution**: registro de una ejecución concreta de una tarea **por un agente IA** en un agent-runtime. Lleva el snapshot de coste USD (`total_cost_usd` + `steps_log`).

**Output**: artefacto generado por una ejecución (código, documento, análisis, plan, review, test_result).

**Review**: registro de revisión de un output con verdict (approved/rejected/changes_requested).

**Conversation**: chat con uno o varios agentes. Modos: planning, discussion, execution, custom.

**Message**: mensaje individual dentro de una conversación.

**KnowledgeBase (KB)**: agrupación nombrada de documentos indexados para RAG. Múltiples por proyecto.

**MemoryEntry**: entrada en la memoria de un agente o equipo. Scopes: private, team_shared, project_shared, global. OJO (revisión 2026-07-03): `private` está atada a un **usuario humano** (`user_id`; CHECK `ck_memory_entries_scope_pointer`) — un agente IA ni escribe ni lee private (el Memorizer hace skip y el recall IA fuerza `user_id=NULL`). Además, con los defaults de fábrica (`memory.default_scope='private'` y `Agent.memory_scope='private'`) la auto-memorización de agentes IA queda APAGADA: para activarla, fija el `memory_scope` del equipo (manda sobre el del agente, ADR 0071) o del agente a team_shared/project_shared/global.

## Términos de Capacitación

> El modelo mental único —**SABER + RECORDAR + SER + HACER** y la tabla de NIVELES— vive en [`../04-reference/training-model.md`](../04-reference/training-model.md). Aquí solo los headwords operador-céntricos.

**Capacitar / Capacidad**: dotar a un agente/equipo/proyecto de **capacidad** por cuatro vías —**SABER** (conocimiento/KBs+RAG), **RECORDAR** (memoria por scope), **SER** (persona/modelo/prompt) y **HACER** (tools/comandos/runtime)—. NO es fine-tuning: los LLM son externos y de catálogo cerrado (ADR 0021). El verbo único en UI es **"Asignar/Quitar"** ("grant" queda como término interno de datos). Ver `training-model.md`.

**Persona**: la categoría **SER** del modelo de capacitación: quién es el agente y cómo se comporta = `system_prompt` + `model_config` (provider/model/temperature + prompts es/en) + skills + chat-mode. Distinta de **Capacidad** (que la engloba) y de **Contexto** (lo que el agente consulta o recuerda). Validada contra el catálogo cerrado (ADR 0055).

**Contexto**: lo que un agente **trae** a un run sin que forme parte de su persona — el **SABER** (hits de RAG sobre las KBs visibles) y el **RECORDAR** (memorias por scope). El contexto de proyecto que ve un agente global ejecutando una tarea lo decide el ADR 0054. No confundir con `model_config` (eso es Persona/SER).

## Términos Operativos

**Kanban de Planes**: vista superior del proyecto, una tarjeta por plan.

**Kanban de Tareas**: vista de detalle al hacer drill-down en un plan.

**Worktree**: checkout dedicado de una rama git compartiendo objetos con el bare repo. Una tarea = un worktree.

**Runtime Template**: imagen Docker pre-construida para ejecutar tests de un stack específico (python-pytest, node-jest, php-phpunit, etc.).

**TestReport canónico**: formato JSON estructurado que normaliza salidas de cualquier test runner para entregar al agente revisor.

**agent-runtime**: contenedor efímero que ejecuta el agent loop (perceive → plan → act → observe).

**test-runtime**: contenedor efímero que ejecuta tests automáticos de una tarea.

**review-runtime**: contenedor persistente que sirve el código del plan a un humano durante validación.

**Worker**: proceso Celery que orquesta el lanzamiento de contenedores. NO ejecuta código del usuario. Familias: default/heavy/gpu/ingestion/test/review/privileged.

**Orchestrator**: servicio que asigna tareas a workers; bifurca por `agent_type` (IA pide contenedor + Execution; humano crea HumanTaskAssignment) y consulta los flags de auto-pausa por budget antes de enqueue.

**Memorizer**: servicio/worker que destila experiencias (de `Execution` y de `HumanWorkSession`) en `memory_entries` al cerrar una tarea, con deduplicación.

**Egress proxy**: tinyproxy con allowlist **default-deny** por el que sale todo el tráfico LLM/red de los runtimes no confiables (ADR 0019/0021).

**Bare repo**: copia del repo sin working directory, solo objetos git. Vive en `/data/.../repos/{name}.git/`.

## Términos de Provider LLM

**`shared-llm`**: paquete Python en `packages/shared-llm` con la capa
común `LLMProvider` async (`complete`/`stream`/`aclose`). Implementa
el catálogo cerrado de cuatro proveedores (ADR 0021). Sustituye al
LiteLLM gateway que se usó hasta 2026-05.

**Azure AI Foundry vía APIM**: gateway empresarial OpenAI-compatible
publicado a través de Azure API Management. Camino del catálogo
ADR 0021 para organizaciones con governance/billing en Azure.

**Ollama**: server local (`ollama serve` en el host o en un
contenedor adyacente) o cloud (`ollama.com`). Mismo wrapper para los
dos despliegues — `OllamaProvider.local()` / `.cloud()`.

**LiteLLM**: (HISTÓRICO) gateway proxy unificado que se usó hasta
2026-05 como provider principal. Retirado por ADR 0021 en favor del
catálogo cerrado de cuatro proveedores.

**Claude Agent SDK**: SDK oficial de Anthropic que permite usar suscripción Claude Pro/Max vía OAuth en lugar de API metered.

**Copilot OAuth Device Flow**: mecanismo de autenticación tipo VSCode para usar modelos via suscripción GitHub Copilot. La máquina de device-flow vive en `shared_llm.providers.copilot`; el token resultante va a Vault.

**llm_providers**: tabla **platform-global** (sin `tenant_id`, sin RLS — solo BYPASSRLS) gestionada por System Admin en `/admin/llm-providers`. `kind` ∈ {`claude_sdk`, `copilot`, `azure_foundry`, `ollama`}. La credencial vive **solo en Vault** (`platform/llm/<provider_id>`); la BD guarda el puntero `secret_vault_path` y un `has_credential` derivado (ADR 0028).

**MCP (Model Context Protocol)**: protocolo estándar para que agentes accedan a recursos externos (tools). Cliente genérico (`stdio`/`sse`/`streamable_http`); catálogo de servidores verificados; secretos por Vault `auth_ref: vault:...` (ADR 0025).

## Términos de Documentación

**Documento** (vs Documentación): unidad **ingerida en una Knowledge Base** para RAG (PDF, Markdown, etc.) — se chunkea, se indexa y el agente lo **consulta** (es **SABER**). Vive en `documents` y se mide en chunks. NO es lo mismo que la **Documentación** del producto.

**Documentación** (vs Documento): las **7 carpetas canónicas de `/docs/`** que describen el sistema (guías, referencia, ADRs, runbooks…) y que renderiza el Visor de Documentación. Es para humanos del proyecto, no un corpus de RAG. Un agente no "consulta" la Documentación salvo que alguien la suba como **Documento** a una KB.

**Estructura Diátaxis adaptada**: las 7 carpetas obligatorias en `/docs/` (01-overview, 02-getting-started, 03-guides, 04-reference, 05-architecture-decisions, 06-runbooks, 07-changelog).

**ADR (Architecture Decision Record)**: documento numerado en `/docs/05-architecture-decisions/` que registra una decisión técnica con su contexto y alternativas descartadas.

**Changelog por plan**: una entrada en `/docs/07-changelog/{plan_id_short}-{slug}.md` por cada plan completado.

## Términos de Seguridad

**RLS (Row-Level Security)**: política PostgreSQL que filtra automáticamente por tenant_id en cada query (ADR 0001).

**app_user (NOBYPASSRLS)**: rol de BD del api-server/workers, sujeto a RLS. **migrations_user / admin (BYPASSRLS)**: rol que salta RLS para migraciones, seeds y endpoints `system_admin` cross-tenant (ADR 0010).

**set_config app.tenant_id**: variable de sesión que el middleware fija por request (`SET app.tenant_id = '<tenant>'`); las políticas RLS comparan `tenant_id` contra ella. Ver gotcha `asyncpg-set-local-no-bind-params`.

**Guardrail**: política de control declarativa evaluada en 4 hook points (`pre_llm`, `post_llm`, `pre_tool`, `post_tool`), motor puro `packages/shared-guardrails`. Composición en 3 capas plataforma → tenant → proyecto, con baselines **bloqueables** (PII, secret_leakage, prompt_injection). 6 acciones: `block`/`redact`/`warn`/`retry_with_feedback`/`escalate_to_human`/`transform` (ADR 0035).

**guardrail_events**: filas tenant-scoped append-only que registran un guardrail que disparó, con detalle **siempre enmascarado** (allowlist de claves no sensibles; nunca el PII/secreto crudo).

**Política de Validación Humana**: configuración por proyecto de qué acciones de agentes requieren confirmación humana (13 categorías, plantillas Sandbox/Desarrollo/Producción/Cliente Externo). Es **aprobación** (`awaiting_human`, ADR 0020), distinta del Human Agent ejecutor (ADR 0046).

**SSO / OIDC / SAML**: autenticación empresarial **junto a** password, platform-global. Sesiones server-side en Redis (también para SSO); JIT provisioning, SCIM, mapeo de grupos (ADRs 0028, 0031).

**MFA**: segundo factor (TOTP + WebAuthn). El `mfa_token` es un reto efímero single-use en Redis; sin segundo factor no se emite sesión (ADR 0031).

**ApiToken**: token por tenant para la API pública (`X-API-Token`), con scope (read/write), vigencia, rate limit e IP allowlist. Solo se persiste el digest SHA-256 (ADR 0037).

**Trust tier (marketplace)**: nivel de confianza de un listing (`verified`/`community`/`experimental`) que gobierna los **guardrails de instalación** (firma, análisis estático, sandbox, consentimiento por permiso), no la disponibilidad (ADR 0032).

**Push Policy**: política por repo: `forbidden`, `branch_only_pr_required` (default), `direct_to_default_allowed`.

**Vault**: HashiCorp Vault o equivalente. Único almacén autorizado de credenciales; inyección just-in-time; rotación de dynamic secrets (ADRs 0003, 0041).

## Términos de Precios, Coste y Budgets

**model_prices**: catálogo de precios **platform-global** y USD-canónico con vigencia (`effective_from`/`effective_to`); un solo periodo abierto por modelo. Lectura global por RLS, escritura solo System Admin (ADR 0035).

**Sync de precios**: importación del feed comunitario JSON de LiteLLM **solo como datos** (no como runtime), acotado a las familias de los proveedores activos; gate de confirmación ante una subida >10% (ADR 0035, plan price-sync-active-providers).

**Snapshot de coste**: cada llamada al modelo congela el precio vigente (input/output/cached + USD) en `steps_log`; un cambio posterior del catálogo no recalcula el histórico (ADR 0028/0035).

**exchange_rates**: catálogo platform-global de tipos de cambio por `(currency, as_of_date)` (rate vs USD), poblado por un job ECB diario. El coste se almacena en USD; la conversión es solo de visualización con el rate del día del run (ADR 0043).

**display_currency**: `Organization.display_currency` (default EUR), moneda de **visualización**; nunca se persiste un coste convertido (ADR 0043).

**Budget / auto-pausa**: límite de gasto por tenant/proyecto con umbrales platform-global (default 80/90/100%). Al 100% se marca `paused_by_budget` y el orquestador rehúsa **arrancar** nuevas ejecuciones (las activas no se matan); override auditado (ADR 0043).

**Coste humano**: `hours_logged * hourly_rate` de una `HumanWorkSession`, convertido a USD canónico. Opt-in al budget vía `project.budget_includes_human_cost`; el dashboard segmenta AI vs Human siempre (ADR 0046).

## Términos de Marketplace, API y Evals

**Marketplace listing**: recurso publicable (`skill` / `tool` / `mcp_server`) navegable e instalable. Catálogo **híbrido**: `tenant_id` NULL = global (catálogo público), no-NULL = privado del tenant (ADR 0032).

**marketplace_shares**: grant explícito y auditado que comparte un listing privado con otro tenant (política RLS aditiva `FOR SELECT`); revocable al instante, nunca un bypass de RLS (ADR 0032).

**Pipeline de instalación gated**: orden fail-closed FETCH → PARSE → VERIFY SIGNATURE → STATIC ANALYSIS → SANDBOX → CONSENT → PERSIST, con las puertas que dicta la `TrustPolicy` del trust tier (ADR 0032).

**Webhook (saliente)**: notificación firmada HMAC que la plataforma emite a un endpoint externo (ADR 0034). **Webhook (entrante)**: `POST /webhooks/incoming/{origin}/{config_id}`, verificado por HMAC en tiempo constante; la URL lleva el `config_id` (UUID), nunca el secreto (ADR 0037).

**API pública v1**: superficie REST `/api/v1` versionada en el path, autenticada con `X-API-Token`, OpenAPI 3.1 + SDKs Python/TS generados; aislamiento garantizado por RLS, no por el endpoint (ADR 0037).

**Eval / LLM-as-judge**: evaluación de la calidad de la salida de un agente por un **modelo de juez distinto** al evaluado, contra criterios custom del tenant. Mismo-modelo se rechaza (`SameModelJudgeError`) (ADR 0038).

**Golden dataset**: benchmark por tenant promocionado idempotentemente desde tareas reales **aprobadas** (con procedencia). **Shadow eval**: muestra aleatoria de tareas reales replicada en background; **nunca** bloquea ni altera la ejecución real (ADR 0038).

**Merge-gate (evals)**: workflow de CI que bloquea un merge si un cambio de prompt regresa la calidad más allá de un umbral configurable (ADR 0038).

**Backup lógico / restore por tenant**: `pg_dump --format=directory` cifrado AES-256-GCM (clave de Vault), destinos enchufables; restore selectivo de un solo tenant vía BD staging + copia filtrada por `tenant_id` (ADR 0036).

## Términos de UI

**Visor de Documentación**: UI `/admin/docs` que renderiza Markdown de `docs/` directamente (lee la carpeta; categorías = las 7 carpetas canónicas). Un doc nuevo aparece sin tocar código.

**Asistente Personal**: agente cross-proyecto que notifica al usuario por canales (Telegram, WhatsApp, Email, Slack, Teams, Discord, SMS, webhooks) y responde a consultas (workload humano, budget, asignaciones pendientes) reutilizando el chat (ADR 0033).

**Panel del System Admin**: UI del operador de la plataforma: Dashboard, Tenants, Monitorización, Backups, Healthchecks, Workers, LLM Providers + Precios, Marketplace, Catálogo de Plantillas, Configuración Global, Auditoría. El menú agrupa por ámbito (platform vs tenant).

**Bandeja personal (human inbox)**: UI donde el usuario aceptar/rechazar/escalar sus `HumanTaskAssignment` y registrar la entrega que crea la `HumanWorkSession` (ADR 0046).

**Instalador**: aplicación web temporal servida por el contenedor `installer`. Wizard de 9 pasos; autodestructivo, secretos CSPRNG, guard de producción (ADR 0039).

## Acrónimos

- **DAG**: Directed Acyclic Graph (dependencias entre tareas).
- **RAG**: Retrieval-Augmented Generation.
- **RLS**: Row-Level Security (PostgreSQL).
- **RBAC**: Role-Based Access Control.
- **SSO**: Single Sign-On.
- **OIDC**: OpenID Connect.
- **SCIM**: System for Cross-domain Identity Management.
- **PAT**: Personal Access Token (GitHub/GitLab).
- **JIT**: Just-In-Time (provisioning).
- **HMAC**: Hash-based Message Authentication Code.
- **PR**: Pull Request.
- **CI/CD**: Continuous Integration / Continuous Delivery.
- **ADR**: Architecture Decision Record.
- **TLS**: Transport Layer Security.
- **MFA**: Multi-Factor Authentication.
- **TOTP**: Time-based One-Time Password.
- **DR**: Disaster Recovery.
- **RPO/RTO**: Recovery Point Objective / Recovery Time Objective.
- **FX**: Foreign Exchange (tipos de cambio).
- **USD**: moneda canónica de coste/precio del sistema.
- **APIM**: Azure API Management (gateway del proveedor Azure AI Foundry).
- **KV v2**: Key/Value versión 2, el motor de secretos de Vault usado.
- **MAC**: Mandatory Access Control (AppArmor).
- **BYPASSRLS / NOBYPASSRLS**: atributo del rol PostgreSQL que salta / respeta RLS.
- **HNSW**: Hierarchical Navigable Small World (índice vectorial de pgvector).
- **RRF**: Reciprocal Rank Fusion (fusión de rankings en búsqueda híbrida).
- **BM25**: ranking léxico para búsqueda full-text.
- **HMAC**: ver arriba; usado para firmar/verificar webhooks.
