# CLAUDE.md — Contexto del Proyecto Sistema Agéntico Multi-Tenant

Este archivo es el contexto principal que debes cargar al arrancar. Define qué es este sistema, sus principios rectores, cómo trabajar en él y el protocolo de gestión del roadmap.

## Qué es Este Sistema

Una **plataforma de IA agéntica multi-tenant** que permite construir, configurar y orquestar equipos de agentes autónomos especializados (Project Manager, Arquitecto, Backend Dev, Frontend Dev, QA, Reviewer, etc.) que trabajan de forma cooperativa sobre proyectos software. La unidad operativa es el **Plan**: un conjunto ordenado de tareas con dependencias DAG que los agentes ejecutan en paralelo.

El sistema se opera como un stack **Docker Compose en una sola máquina** (no Kubernetes). Es multi-tenant a nivel de departamentos/equipos, no SaaS comercial masivo. Lenguaje principal: **Python + FastAPI + PostgreSQL+pgvector + Redis + LangGraph + Celery**. Frontend: **Next.js + React + Tailwind + shadcn/ui**.

## Principios Rectores (NO Negociables)

1. **Multi-tenancy desde el día uno**: cada tabla tiene `tenant_id`, PostgreSQL RLS activado, middleware que inyecta tenant_id en cada request, tests automáticos cross-tenant en CI. NUNCA escribir queries sin tenant_id.

2. **Aislamiento por contenedor**: los workers NO ejecutan código del usuario. Lanzan contenedores efímeros (agent-runtime, test-runtime, review-runtime) con red restringida, sin socket Docker, cap-drop ALL, seccomp default-deny.

3. **Ejecución de tests en stacks heterogéneos**: catálogo de **runtime templates** (python-pytest, node-jest, php-phpunit, etc.) como imágenes Docker mantenidas. Los workers solo orquestan, los runtimes ejecutan.

4. **Código persistente con git worktrees**: cada proyecto tiene su bare repo en disco en `/data/agent-platform/projects/{tenant}/{project}/repos/{repo}.git/`. Los worktrees por tarea permiten paralelismo sin clones repetidos.

5. **Plan = unidad de cambio**: cada plan se materializa como una rama git `plan/{plan_id_short}-{slug}`, los commits de cada tarea llevan trailers `Plan-Id`, `Task-Id`, `Execution-Id`. Al completar el plan se abre un PR automático.

6. **Doble Kanban**: vista superior de Planes (gerencial) + vista de Tareas por plan (operativa). NUNCA mostrar un Kanban plano que mezcla tareas de varios planes.

7. **Tests humanos a nivel de plan**, no de tarea. Excepción: `task.human_validation_required=true` para tareas individuales críticas.

8. **Documentación obligatoria en `/docs/`** con estructura canónica de 7 carpetas (`01-overview/`, `02-getting-started/`, `03-guides/`, `04-reference/`, `05-architecture-decisions/`, `06-runbooks/`, `07-changelog/`). El Technical Writer agente la mantiene al cierre de cada plan.

9. **LLM providers desacoplados, catálogo cerrado** (ADR 0021): los cuatro caminos soportados son **Claude Agent SDK** (suscripción Pro/Max), **GitHub Copilot** (OAuth Device Flow + JWT minted), **Azure AI Foundry vía APIM** (gateway empresarial OpenAI-compatible) y **Ollama** (local + cloud). La capa común vive en `packages/shared-llm` (Protocol async `LLMProvider`). **LiteLLM ya no se usa**: añadir un quinto proveedor pide un ADR explícito.

10. **Guardrails declarativos por capas** (plataforma → tenant → proyecto) en cuatro puntos del ciclo: pre_llm, post_llm, pre_tool, post_tool.

11. **Validación humana configurable por proyecto** con 13 categorías de acciones sensibles y 4 plantillas (Sandbox, Desarrollo, Producción, Cliente Externo).

12. **Idiomas soportados**: **ES + EN únicamente** en esta versión. No invertir esfuerzo en más idiomas.

## Estructura del Repositorio (Esperada)

```
agentic-platform/
├── CLAUDE.md                        # este archivo
├── apps/
│   ├── api-server/                  # FastAPI + endpoints REST/WebSocket
│   ├── orchestrator/                # Asignación de tareas a workers
│   ├── workers/                     # Celery workers (default/heavy/gpu/test/review)
│   ├── memorizer/                   # Indexación memoria
│   ├── notification-dispatcher/
│   ├── webhook-dispatcher/
│   ├── personal-assistant/
│   ├── installer/                   # Wizard UI bootstrap
│   ├── admin-panel/                 # Frontend Next.js del System Admin
│   └── web-app/                     # Frontend Next.js de tenants
├── packages/
│   ├── shared-domain/               # Modelos Pydantic compartidos
│   ├── shared-db/                   # SQLAlchemy + Alembic
│   ├── shared-auth/                 # JWT + RBAC + Casbin
│   ├── shared-llm/                  # Capa LLM async (Claude SDK + Copilot + Azure Foundry + Ollama) — ADR 0021
│   ├── shared-mcp/                  # Cliente MCP genérico
│   ├── shared-guardrails/           # Motor de guardrails
│   └── shared-test-runtimes/        # Definiciones de runtime templates
├── docker/
│   ├── docker-compose.yml           # Stack principal
│   ├── docker-compose.dev.yml       # Overrides desarrollo
│   ├── docker-compose.gpu.yml       # Overrides GPU (opcional)
│   └── agent-runtimes/              # Dockerfiles de runtime templates
├── docs/
│   ├── context/                     # Contextualización para el desarrollo
│   ├── roadmap/                     # Planes por fase (00 a 15)
│   ├── 01-overview/                 # 7 carpetas canónicas del producto
│   ├── 02-getting-started/
│   ├── 03-guides/
│   ├── 04-reference/
│   ├── 05-architecture-decisions/
│   ├── 06-runbooks/
│   └── 07-changelog/
├── scripts/
│   ├── install.sh
│   ├── uninstall.sh
│   └── backup.sh
└── tests/
    ├── unit/
    ├── integration/
    └── e2e/
```

## Stack Tecnológico (Resumen)

| Capa                 | Tecnología                                                                              |
| -------------------- | --------------------------------------------------------------------------------------- |
| Lenguaje backend     | Python 3.12                                                                             |
| Framework web        | FastAPI + Uvicorn                                                                       |
| ORM                  | SQLAlchemy 2.x async + Alembic                                                          |
| BD relacional        | PostgreSQL 16                                                                           |
| BD vectorial         | pgvector (en el mismo PostgreSQL)                                                       |
| Cache/broker         | Redis 7                                                                                 |
| Cola de tareas       | Celery 5                                                                                |
| Orquestación agentes | LangGraph                                                                               |
| LLM abstracción      | `packages/shared-llm` — Claude SDK + Copilot + Azure Foundry (APIM) + Ollama (ADR 0021) |
| Object storage       | MinIO (S3-compatible)                                                                   |
| Secrets              | HashiCorp Vault                                                                         |
| Ingestión documental | Docling (IBM) + docling-serve + docling-mcp                                             |
| Frontend             | Next.js 14 + React + TanStack Query + Tailwind + shadcn/ui                              |
| Tiempo real          | WebSocket + SSE                                                                         |
| Observabilidad       | OpenTelemetry + Prometheus + Grafana + Loki                                             |
| Antivirus            | ClamAV                                                                                  |

## Convenciones de Código

Lee `docs/context/conventions.md` para el detalle. Resumen:

- **Python**: black + ruff + mypy strict. Type hints obligatorios.
- **TypeScript**: prettier + eslint + tipos estrictos. No `any`.
- **Commits**: Conventional Commits con trailers `Plan-Id`, `Task-Id`, `Execution-Id`.
- **PRs**: uno por plan, no por tarea. Cuerpo del PR auto-generado por el sistema.
- **Tests**: cobertura > 70% en dominio crítico. Unit + integration + e2e separados.
- **Docs**: Markdown con frontmatter YAML, Mermaid para diagramas, estructura canónica de 7 carpetas.

---

## Protocolo de Trabajo con el Roadmap

El roadmap vive en `docs/roadmap/` con un archivo por fase (`00-fundaciones.md` a `15-instalador-produccion.md`). El estado del proyecto se mantiene **dentro de cada archivo de fase**, no en archivos paralelos. La fuente de verdad es el frontmatter YAML y los checkboxes de tareas.

### Para saber dónde estamos

Lee los frontmatter de `docs/roadmap/*.md`. La fase activa es la que tenga:

```yaml
status: in_progress
```

Si ninguna lo tiene, la activa es la primera (por número) con `status: pending_approval` cuyos plan_ids de `blocking_plan` estén todos `completed` (o cuyo `blocking_plan` sea `null`, caso de la fase 00-fundaciones).

### Al empezar una fase

Edita el frontmatter del archivo correspondiente:

```yaml
status: in_progress
started_at: 2026-05-20
```

**Solo una fase puede estar `in_progress` a la vez.**

### Durante el desarrollo de una fase

Cada tarea es un checkbox `- [ ] **Título**: ...`. Al completar una tarea Y validar su test automático en verde, edita el archivo para marcarla:

```
- [x] **Título**: ...
```

El checkbox refleja realidad verificada, no intención.

### Al cerrar una fase

Solo cuando se cumplen TODOS los criterios de cierre del plan:

1. Todos los checkboxes de tareas marcados `[x]`.
2. Todos los tests automáticos en verde.
3. Tests humanos del plan validados por un humano.
4. Entrada generada en `docs/07-changelog/{plan_id}.md`.
5. PR del plan mergeado a `main`.

Edita el frontmatter:

```yaml
status: completed
completed_at: 2026-05-21
```

Y a continuación, si procede, activa la siguiente fase (aquella cuyos plan_ids de `blocking_plan` estén todos `completed`) cambiando su `status` a `in_progress` y rellenando `started_at`.

### Reglas Duras del Protocolo

- ❌ NUNCA marcar `[x]` una tarea cuyo test automático no pase.
- ❌ NUNCA cambiar `status: completed` sin la entrada de changelog generada y el PR mergeado.
- ❌ NUNCA tener dos fases en `status: in_progress` simultáneamente.
- ❌ NUNCA empezar una fase si algún plan listado en su `blocking_plan` no está `completed`.
- ❌ NUNCA editar el roadmap para "saltarse" pasos o reordenarlos sin que un humano apruebe el cambio.

El campo `blocking_plan` es siempre una **lista YAML** de plan_ids (puede ser
vacía representada como `null`, o tener uno o varios elementos). Una fase solo
puede pasar a `in_progress` cuando TODOS los plan_ids de su `blocking_plan`
tengan `status: completed`.

### Estados Válidos del Frontmatter

| status                     | Significado                                                          |
| -------------------------- | -------------------------------------------------------------------- |
| `pending_approval`         | Plan definido pero no empezado                                       |
| `approved`                 | Aprobado por humano, listo para empezar (estado intermedio opcional) |
| `in_progress`              | Plan activo ahora mismo (solo uno a la vez)                          |
| `blocked`                  | Plan empezado pero pausado por bloqueo externo                       |
| `pending_human_validation` | Todas las tareas done, esperando tests humanos del plan              |
| `completed`                | Plan cerrado completamente                                           |
| `cancelled`                | Plan abandonado                                                      |
| `rejected`                 | Plan revisado y rechazado por humano                                 |
| `archived`                 | Plan completado y movido a histórico                                 |

---

## Cómo Trabajar por Fases

1. Lee este `CLAUDE.md` entero al arrancar la sesión.
2. Determina la fase activa según el protocolo (lee los frontmatter de `docs/roadmap/`).
3. Abre el archivo de la fase activa y léelo entero antes de empezar.
4. Implementa las tareas en orden, respetando dependencias entre tareas.
5. Marca cada tarea como `[x]` solo tras pasar su test automático.
6. Al cerrar la fase: genera entrada en `/docs/07-changelog/`, actualiza `/docs/04-reference/` afectados, actualiza el frontmatter, activa la siguiente fase.
7. Si tienes dudas que el documento de especificaciones no resuelve: NO inventes, genera un ADR en `/docs/05-architecture-decisions/` con opciones para que el humano decida.

## Cosas que NO Hacer

- ❌ Escribir queries SQL sin filtro por `tenant_id` o sin middleware que lo inyecte.
- ❌ Instalar lenguajes adicionales (PHP, Node, Go, Java) en la imagen de los workers. Eso vive en runtime templates separados.
- ❌ Hacer `docker compose up -d --build` sin antes verificar que las migraciones Alembic son reversibles.
- ❌ Pushear directamente a `main` del repo del propio sistema. Todo va por PR.
- ❌ Comitear secretos. Vault es la única vía de credenciales.
- ❌ Crear features nuevas no documentadas en el .docx sin pasar antes por ADR.
- ❌ Asumir Kubernetes / multi-máquina. El alcance actual es Docker Compose en una sola máquina.
- ❌ Confundir scopes de memoria: private (usuario humano — un agente IA ni la escribe ni la lee), team_shared (equipo), project_shared (proyecto), global (organización).

## Contexto Adicional

- `docs/context/architecture-overview.md` — visión arquitectónica resumida.
- `docs/context/glossary.md` — términos del dominio.
- `docs/context/tech-stack.md` — stack tecnológico detallado.
- `docs/context/conventions.md` — convenciones de código y commits.
- `docs/03-guides/gotchas/` — trampas conocidas del toolchain (Docker, asyncpg, mypy, pre-commit, OTEL, Windows…) con síntoma + causa raíz + fix. Antes de inventar una solución para un error de infraestructura, **busca aquí primero**; si lo resuelves y la trampa no estaba documentada, **añádela**.
- `docs/roadmap/` — planes por fase (00 a 15), uno por archivo Markdown.
- `docs/roadmap/README.md` — índice de fases del roadmap.
- `especificaciones-completas.docx` (ubicación según convención del repo) — documento maestro con TODO el detalle (36 secciones + anexo).

## Sobre el Documento Maestro

El `.docx` es la fuente de verdad. Si en algún momento Claude Code tiene una intuición que contradice el documento, **el documento gana**. El documento puede tener huecos en detalles concretos de implementación (eso es intencional, son decisiones técnicas a tomar durante el desarrollo), pero las decisiones de producto y arquitectura ya están cerradas.

Cuando una fase remite a "ver sección X.Y", esa referencia es al .docx. Para no tener que abrirlo constantemente, cada fase en `docs/roadmap/` resume las secciones relevantes; el .docx es para consultas profundas.
