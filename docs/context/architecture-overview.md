# Arquitectura del Sistema (Resumen)

Documento de un solo vistazo. Para detalle profundo de cada capa, consultar el `.docx` (secciones referenciadas entre paréntesis).

## Plano de Control vs. Plano de Ejecución

```
┌─────────────────────────────────────────────────────────────────┐
│                       PLANO DE CONTROL                          │
│  (API Gateway, Servicios de Dominio, Orquestador, BD, Vault)    │
└─────────────────────────────────────────────────────────────────┘
                                │
                                │ (jobs, eventos)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PLANO DE EJECUCIÓN                         │
│              (Workers + contenedores efímeros)                  │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│   │ agent-runtime│  │ test-runtime │  │   review-runtime     │  │
│   │   (efímero)  │  │  (efímero)   │  │   (persistente)      │  │
│   └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Componentes del Stack Docker Compose (sección 21)

**Ingress**: nginx/caddy (TLS + reverse proxy).

**Aplicación**: api-server, orchestrator, personal-assistant, notification-dispatcher, webhook-dispatcher, memorizer.

**Workers** (escalables manualmente):

- worker-default: tareas estándar
- worker-heavy: tareas pesadas
- worker-gpu: si GPU habilitada
- worker-ingestion: pipeline RAG con Docling
- worker-test: orquesta test-runtimes
- worker-review: gestiona review-runtimes persistentes
- worker-privileged: tools privilegiadas (off por defecto)

**Servicios IA**: litellm-proxy, docling-serve, docling-mcp, ollama (opcional).

**Datos**: postgres (con pgvector + pg_trgm), redis, minio, vault, clamav.

**Observabilidad** (opcional): prometheus, grafana, loki, alertmanager.

## Flujo End-to-End de un Plan (sección 8 + 12 + 14 + 31)

1. Usuario abre chat de planning con el equipo del proyecto.
2. PM agente coordina conversación natural. Otros agentes intervienen según pertinencia.
3. Cuando el plan está cerrado, aparece botón "Generar Plan".
4. Plan persiste en estado `pending_approval`. Aparece en Kanban de Planes del proyecto.
5. Humano revisa detalle (cabecera, descripción, fases, tareas, tests humanos, coste).
6. Humano aprueba → plan pasa a `approved`.
7. Humano sincroniza al Kanban de Tareas → plan pasa a `in_progress`.
8. Sistema crea rama `plan/{plan_id}-{slug}` en cada repo afectado.
9. Cada tarea ejecuta en worktree dedicado:
   - agent-runtime corre el agent loop, genera commits locales
   - test-runtime ejecuta tests automáticos (runtime template por stack)
   - agente revisor valida con TestReport canónico
   - Si OK: `git push` a la rama del plan
   - Si KO: tarea vuelve a `in_progress` con feedback
10. Todas las tareas en `done` → plan pasa a `pending_human_validation` (si política lo requiere).
11. Sistema levanta review-runtime persistente (servicio + DB + frontend + servicios auxiliares).
12. Humano accede vía URL temporal firmada + terminal web + checklist tests humanos.
13. Humano emite verdict:
    - Approved → sistema abre PR contra default branch en cada repo → plan `completed`
    - Rejected → contenedor sigue 4h para investigación, plan vuelve a `in_progress`

## Modelo de Entidades Principales (sección 3)

```
Organization (Tenant)
  └── User (4 roles globales + 4 roles por proyecto)
  └── Team
       └── Agent (global_builtin | global_tenant_template | project_local)
            ├── Skill (M:N, declarativas)
            └── Tool (M:N, ejecutables: builtin/python/http/mcp/docker)
  └── Project (con team_id, mcp_servers, knowledge_bases, repository_config,
       worker_config, secrets_vault_id, human_approval_policy, docs_language)
       └── Plan (con estado: pending_approval/approved/in_progress/
            pending_human_validation/completed/blocked/cancelled/rejected/archived)
            └── Task (con estado: backlog/ready/in_progress/in_review/
                 awaiting_human/blocked/done/cancelled)
                 └── Execution
                      └── Output → Review
  └── KnowledgeBase
       └── Document
            └── Chunk (con embedding pgvector)
  └── Conversation (Planning | Discusión | Ejecución | custom)
       └── Message
```

## Modelo Linked vs. Forked de Agentes (sección 5.7)

- Agentes globales (built-in + plantillas tenant) son **read-only**.
- Al añadir a proyecto: pregunta "tal cual" (linked, default) o "personalizar" (forked).
- Linked: el proyecto referencia al global. Mejoras del global se propagan.
- Forked: clon editable independiente con puntero al global de origen para diff y merge selectivo.
- Aplica a equipos, skills, tools y plantillas de proyecto también.

## Memoria vs. RAG (secciones 10 + 11)

- **Memoria**: experiencias propias del agente/equipo, generadas dinámicamente al cerrar tareas. Scopes: privada, equipo, proyecto, organización. Episódica vs. semántica.
- **RAG**: documentación externa cargada explícitamente. Múltiples KBs por proyecto. Docling como motor de ingestión. Búsqueda híbrida BM25 + vector + RRF + reranking.
- Distinción clave: Memoria = "qué hemos aprendido haciendo". RAG = "qué sabe la organización antes de empezar".

## MCP Como Ciudadano Nativo (sección 9)

- Cliente MCP genérico soporta transportes stdio + sse + streamable_http.
- Cada proyecto declara qué MCP servers usa.
- Tools MCP se descubren automáticamente y se exponen al agente como tools nativas.
- MCP servers pre-integrados verified: **docling-mcp** (procesamiento documental in-flight), github-mcp, postgres-mcp, filesystem-mcp, gdrive/gmail/gcalendar-mcp, slack-mcp, jira-mcp/linear-mcp.

## Persistencia del Código (sección 12.4)

```
/data/agent-platform/
├── postgres/                       (datos PostgreSQL)
├── redis/                          (AOF + RDB)
├── minio/                          (object storage)
├── vault/                          (secretos cifrados)
├── projects/
│   └── {tenant_slug}/
│       └── {project_slug}/
│           ├── repos/
│           │   ├── {repo_name}.git/        (bare repo)
│           │   └── worktrees/
│           │       ├── {plan_id}-{task_id}/  (worktree por tarea)
│           │       └── {plan_id}-review/     (worktree review-runtime)
│           └── dep-cache/
│               ├── npm-{hash}/             (cacheado por lock file)
│               ├── pip-{hash}/
│               └── composer-{hash}/
└── backups/                        (snapshots locales pre-remoto)
```

## Seguridad por Capas (secciones 16 + 19 + 20)

1. **Auth multi-tenant**: SSO configurable por tenant (user+password, OIDC Azure/Google/Okta, SAML 2.0, LDAP opcional).
2. **RBAC**: 4 roles globales (System Admin/Operator, Tenant Admin/User) + 4 roles por proyecto.
3. **RLS**: PostgreSQL Row-Level Security por tenant_id en todas las tablas.
4. **Aislamiento de contenedores**: red dedicada, cap-drop ALL, seccomp default-deny, AppArmor, read-only FS excepto workspace.
5. **Vault**: HashiCorp Vault para secretos, inyección just-in-time vía Docker secrets.
6. **Guardrails declarativos**: 12 tipos (PII, secret leakage, prompt injection, content safety, cost ceiling, etc.) en pipeline pre_llm/post_llm/pre_tool/post_tool.
7. **Validación humana por proyecto**: 13 categorías con plantillas Sandbox/Desarrollo/Producción/Cliente Externo.
8. **Auditoría**: tabla `audit_log` inmutable, retención 2 años.

## Panel del System Admin (sección 24)

11 secciones: Dashboard, Tenants, Monitorización (Grafana embebido), Backups, Healthchecks, Workers, Modelos & Precios, Marketplace, Catálogo de Plantillas, Configuración Global, Auditoría.

## Instalador (sección 22)

Wizard de 9 pasos: Bienvenida → Config básica → Recursos/Workers/GPU → Almacenamiento → Providers LLM → Tenant inicial → Resumen → Instalación → Listo. Modo CLI desatendido con plantillas YAML para automatización.
