# Glosario del Dominio

## Entidades Principales

**Organization / Tenant**: organización o departamento. Aísla todos los recursos. Multi-tenant por diseño con RLS.

**User**: usuario humano del sistema. 4 roles globales: System Admin, System Operator, Tenant Admin, Tenant User. 4 roles funcionales por proyecto: project_owner, project_maintainer, project_contributor, project_viewer.

**Agent**: entidad de IA con rol, skills, tools y memoria propia. Tres scopes: `global_builtin` (sistema), `global_tenant_template` (tenant), `project_local` (proyecto). Modelos linked vs. forked al añadir global a proyecto.

**Skill**: capacidad declarativa de un agente (descripción de qué sabe hacer). NO ejecutable. Inyecta fragmento de prompt y sugiere tools.

**Tool**: función concreta invocable por un agente. 5 tipos: `builtin`, `python_function`, `http_endpoint`, `mcp_tool`, `docker_command`.

**Team**: conjunto de agentes con roles complementarios asignado a uno o varios proyectos.

**Project**: contenedor lógico de tareas, equipo, KBs, configuración MCP, política de validación humana, repos asociados.

**Plan**: conjunto ordenado de tareas con dependencias DAG generado en chat de planning. Materializa como rama git al sincronizarse.

**Task**: unidad de trabajo en el Kanban. Tiene runtime declarado para tests automáticos, criterios de aceptación, opcionalmente `human_validation_required=true`.

**Execution**: registro de una ejecución concreta de una tarea por un worker.

**Output**: artefacto generado por una ejecución (código, documento, análisis, plan, review, test_result).

**Review**: registro de revisión de un output con verdict (approved/rejected/changes_requested).

**Conversation**: chat con uno o varios agentes. Modos: planning, discussion, execution, custom.

**Message**: mensaje individual dentro de una conversación.

**KnowledgeBase (KB)**: agrupación nombrada de documentos indexados para RAG. Múltiples por proyecto.

**MemoryEntry**: entrada en la memoria de un agente o equipo. Scopes: private, team_shared, project_shared, global.

## Términos Operativos

**Kanban de Planes**: vista superior del proyecto, una tarjeta por plan.

**Kanban de Tareas**: vista de detalle al hacer drill-down en un plan.

**Worktree**: checkout dedicado de una rama git compartiendo objetos con el bare repo. Una tarea = un worktree.

**Runtime Template**: imagen Docker pre-construida para ejecutar tests de un stack específico (python-pytest, node-jest, php-phpunit, etc.).

**TestReport canónico**: formato JSON estructurado que normaliza salidas de cualquier test runner para entregar al agente revisor.

**agent-runtime**: contenedor efímero que ejecuta el agent loop (perceive → plan → act → observe).

**test-runtime**: contenedor efímero que ejecuta tests automáticos de una tarea.

**review-runtime**: contenedor persistente que sirve el código del plan a un humano durante validación.

**Worker**: proceso Celery que orquesta el lanzamiento de contenedores. NO ejecuta código del usuario.

**Bare repo**: copia del repo sin working directory, solo objetos git. Vive en `/data/.../repos/{name}.git/`.

## Términos de Provider LLM

**LiteLLM**: gateway proxy unificado que soporta 100+ providers tras la interfaz OpenAI-compatible. Provider principal del sistema.

**Claude Agent SDK**: SDK oficial de Anthropic que permite usar suscripción Claude Pro/Max vía OAuth en lugar de API metered.

**Copilot OAuth Device Flow**: mecanismo de autenticación tipo VSCode para usar modelos via suscripción GitHub Copilot.

**MCP (Model Context Protocol)**: protocolo estándar para que agentes accedan a recursos externos (tools).

## Términos de Documentación

**Estructura Diátaxis adaptada**: las 7 carpetas obligatorias en `/docs/` (01-overview, 02-getting-started, 03-guides, 04-reference, 05-architecture-decisions, 06-runbooks, 07-changelog).

**ADR (Architecture Decision Record)**: documento numerado en `/docs/05-architecture-decisions/` que registra una decisión técnica con su contexto y alternativas descartadas.

**Changelog por plan**: una entrada en `/docs/07-changelog/{plan_id_short}-{slug}.md` por cada plan completado.

## Términos de Seguridad

**RLS (Row-Level Security)**: política PostgreSQL que filtra automáticamente por tenant_id en cada query.

**Guardrail**: política de control declarativa aplicada en uno de 4 puntos: pre_llm, post_llm, pre_tool, post_tool.

**Política de Validación Humana**: configuración por proyecto de qué acciones de agentes requieren confirmación humana (13 categorías).

**Push Policy**: política por repo: `forbidden`, `branch_only_pr_required` (default), `direct_to_default_allowed`.

**Vault**: HashiCorp Vault o equivalente. Único almacén autorizado de credenciales.

## Términos de UI

**Visor de Documentación**: UI cross-proyecto que renderiza Markdown de todos los proyectos del tenant accesibles al usuario.

**Asistente Personal**: agente cross-proyecto que notifica al usuario por canales (Telegram, WhatsApp, Email, Slack, Teams, Discord, webhooks).

**Panel del System Admin**: UI del operador de la plataforma. 11 secciones (Dashboard, Tenants, Monitorización, Backups, etc.).

**Instalador**: aplicación web temporal servida por el contenedor `installer`. Wizard de 9 pasos.

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
