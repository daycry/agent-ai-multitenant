# CLAUDE.md — Contexto del Proyecto Sistema Agéntico Multi-Tenant

Este archivo es el contexto principal que debes cargar al arrancar. Define qué es este sistema, sus principios rectores, cómo trabajar en él y el protocolo de gestión del roadmap.

> **Antes de nada, lee [`CONTINUE_HERE.md`](CONTINUE_HERE.md)**: dice en qué rama
> va el trabajo, qué está bloqueado, qué espera una decisión tuya y cómo
> comprobar que ese resumen sigue siendo cierto. Este archivo explica CÓMO se
> trabaja; aquél, POR DÓNDE va.

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

7. **Tests humanos a nivel de plan**, no de tarea. Para exigir un humano en un punto CONCRETO hay dos vías, ambas con granularidad mayor que un flag por tarea: las **políticas de aprobación por categoría de acción sensible** (13 categorías, 4 plantillas — principio 11) y la tool **`ask_human`** (ADR 0114), con la que el propio agente para y pregunta. No existe `task.human_validation_required`: fue una promesa de este documento que nunca tuvo columna ni código, retirada por el ADR 0117 (b) el 2026-07-26.

8. **Documentación obligatoria en `/docs/`** con estructura canónica de 7 carpetas (`01-overview/`, `02-getting-started/`, `03-guides/`, `04-reference/`, `05-architecture-decisions/`, `06-runbooks/`, `07-changelog/`). El Technical Writer agente la mantiene al cierre de cada plan.

9. **LLM providers desacoplados, catálogo cerrado** (ADR 0021): los cuatro caminos soportados son **Claude Agent SDK** (suscripción Pro/Max), **GitHub Copilot** (OAuth Device Flow + JWT minted), **Azure AI Foundry vía APIM** (gateway empresarial OpenAI-compatible) y **Ollama** (local + cloud). La capa común vive en `packages/shared-llm` (Protocol async `LLMProvider`). **LiteLLM ya no se usa**: añadir un quinto proveedor pide un ADR explícito.

10. **Guardrails declarativos por capas** (plataforma → tenant → proyecto) en cuatro puntos del ciclo: pre_llm, post_llm, pre_tool, post_tool.

11. **Validación humana configurable por proyecto** con 13 categorías de acciones sensibles y 4 plantillas (Sandbox, Desarrollo, Producción, Cliente Externo).

12. **Idiomas soportados**: **ES + EN únicamente** en esta versión. No invertir esfuerzo en más idiomas.

## Estructura del Repositorio (Real)

Este árbol describe el repo **como está hoy**, no como se planeó. Las carpetas
marcadas `RESERVADA` contienen solo `.gitkeep`: la funcionalidad existe, pero
vive integrada en otro servicio (ADR 0033 para asistente/memorizer). No las
uses como punto de partida ni asumas que hay código dentro. El test
`tests/unit/test_docs_governance.py::test_claude_md_tree_matches_repo` falla si
este árbol y `apps/`/`packages/` divergen.

```
agentic-platform/
├── CLAUDE.md                        # este archivo
├── apps/
│   ├── api-server/                  # FastAPI + REST/WebSocket. Aloja además el
│   │                                #   memorizer, el asistente y los webhooks
│   ├── orchestrator/                # Asignación de tareas a workers
│   ├── workers/                     # Celery workers (default/heavy/gpu/test/review).
│   │                                #   Aloja además el despacho de webhooks
│   ├── notification-dispatcher/     # Servicio propio
│   ├── watchdog/                    # Vigilante de salud de contenedores
│   │                                #   (reinicio con backoff exponencial)
│   ├── memorizer/                   # RESERVADA (vacía) — vive en api_server/memorizer/
│   ├── webhook-dispatcher/          # RESERVADA (vacía) — vive en los workers
│   ├── personal-assistant/          # RESERVADA (vacía) — vive en api_server/assistant/
│   ├── installer/                   # Wizard UI bootstrap (backend/ + frontend/)
│   └── admin-panel/                 # Frontend ÚNICO: tenants + System Admin,
│                                    #   separados por RBAC y rutas (ADR 0117 c)
├── packages/
│   ├── shared-domain/               # Enums y constantes de dominio compartidas
│   ├── shared-llm/                  # Capa LLM async (Claude SDK + Copilot + Azure Foundry + Ollama) — ADR 0021
│   ├── shared-mcp/                  # Cliente MCP genérico
│   ├── shared-guardrails/           # Motor de guardrails
│   ├── shared-test-runtimes/        # Definiciones de runtime templates
│   ├── sdk-python/                  # SDK público generado del OpenAPI v1 (Plan 13)
│   ├── sdk-typescript/              # SDK público generado del OpenAPI v1 (Plan 13)
│   ├── shared-db/                   # RESERVADA (vacía) — SQLAlchemy y Alembic
│   │                                #   viven en api_server/db/ y api-server/migrations/
│   └── shared-auth/                 # RESERVADA (vacía) — JWT y RBAC viven en
│                                    #   api_server/auth/
├── docker/
│   ├── docker-compose.yml           # Stack principal
│   ├── docker-compose.dev.yml       # Overrides desarrollo
│   ├── docker-compose.gpu.yml       # Overrides GPU (opcional)
│   ├── docker-compose.monitoring.yml# Overlay de observabilidad
│   └── agent-runtimes/              # Dockerfiles de runtime templates
├── docs/
│   ├── context/                     # Contextualización para el desarrollo
│   ├── roadmap/                     # Planes: numerados (00 a 16) + serie
│   │                                #   correctiva prod-01…prod-18 + descriptivos
│   ├── manuals/                     # Manuales de usuario (fuente + PDF generado)
│   ├── 01-overview/                 # 7 carpetas canónicas del producto
│   ├── 02-getting-started/
│   ├── 03-guides/
│   ├── 04-reference/
│   ├── 05-architecture-decisions/
│   ├── 06-runbooks/
│   └── 07-changelog/
├── scripts/
│   ├── install.sh                   # Tooling de plataforma
│   ├── uninstall.sh
│   ├── backup.sh
│   ├── dev/                         # Scripts de desarrollo (up/down/e2e/builds)
│   └── demos/                       # Demos de tests humanos por fase
└── tests/
    ├── unit/
    ├── integration/
    ├── e2e/
    ├── security/
    └── smoke/
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
5. PR del plan mergeado a `master` (la rama por defecto de este repo es
   `master`, no `main`).

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

### La excepción al gate: `gate_override` (ADR 0138)

La regla dura de arriba tiene UNA salida, y está reglada. Si una fase tiene que
empezar con un `blocking_plan` sin `completed`, se declara en su frontmatter:

```yaml
gate_override:
  approved_by: operador
  date: 2026-07-31
  adr: 0138
  unmet: 11-guardrails-precios
  reason: >-
    Por qué se acepta empezar igualmente, con detalle suficiente para que alguien
    lo audite dentro de seis meses.
```

**La justificación es obligatoria y la comprueba un test**
(`test_gate_override_carries_a_written_justification`): un override sin `reason`
escrito, o con uno de menos de 80 caracteres, rompe la suite. Sin esa exigencia el
campo sería la forma barata de saltarse el protocolo, que es justo lo que el ADR
0138 descartó.

Dos cosas más que vigilan los tests:

- **La deuda no crece a espaldas de nadie**: una fase nueva empezada con el gate
  saltado y sin override rompe la suite (`test_gate_debt_inventory_has_not_grown`).
- **El override caduca**: cuando su bloqueante llega de verdad a `completed`, hay
  que retirarlo. Un override huérfano dice que hubo una excepción donde ya no la
  hay (`test_gate_override_only_where_the_gate_is_actually_unmet`).

El caso que motivó el mecanismo: seis fases arrancaron con el gate incumplido, y en
dos de ellas **el override ya lo había escrito un humano**… en un campo duplicado de
la tabla de cabecera que otra tarea venía a borrar por desincronizado. Sin un sitio
previsto, la excepción se anota donde nadie la va a leer.

Y la causa de fondo, que el ADR 0138 mide: el cuello de botella no es la
indisciplina, es que **ninguna fase llega a `completed` porque eso exige validación
humana**, así que toda fase que dependa de una ya terminada lee su gate como
incumplido aunque el trabajo esté hecho.

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
- ❌ Pushear directamente a la rama por defecto del repo del propio sistema, que
  es `master` y no `main`. Todo va por PR.
- ❌ Comitear secretos. Vault es la única vía de credenciales de **plataforma**, con la única excepción escrita más abajo (§«Dónde vive un secreto»).
- ❌ Crear features nuevas no documentadas en el .docx sin pasar antes por ADR.
- ❌ Asumir Kubernetes / multi-máquina. El alcance actual es Docker Compose en una sola máquina.
- ❌ Confundir scopes de memoria: private (usuario humano — un agente IA ni la escribe ni la lee), team_shared (equipo), project_shared (proyecto), global (organización).

## Dónde vive un secreto (y la única excepción a Vault)

La regla sigue siendo **Vault**. La excepción está escrita aquí porque el
[ADR 0146](docs/05-architecture-decisions/0146-fernet-en-db-vs-vault.md) la firmó
el 2026-08-01, y una excepción que no consta en el sitio donde se busca no es una
excepción: es una discrepancia entre el principio y el código, que deja a quien
lea esto sin saber dónde buscar un secreto y a quien audite sin saber qué esperar.

**El criterio, en una línea: si la plataforma no arranca sin ese secreto, va a
Vault; si lo que se rompe es la integración de un tenant concreto, puede ir en
columna.**

| Familia                                                                                                                                                                             | Dónde vive                                                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Credenciales de **PLATAFORMA**: proveedores LLM, contraseñas de BD, claves de MinIO, tokens de servicio                                                                             | **Vault**, sin excepción. La BD guarda sólo el puntero (`secret_vault_path`)     |
| Secretos que un **TENANT** configura para un **TERCERO**: client secrets OIDC, claves privadas SAML, credenciales de canal de notificación, secretos de firma de webhooks entrantes | Columna cifrada con Fernet (`API_SERVER_*_ENCRYPTION_KEY(S)`) — **la excepción** |

**Por qué la excepción existe.** El [ADR 0145](docs/05-architecture-decisions/0145-vault-operable-tokens-y-unseal.md)
decidió **desellado manual** de Vault. Encadenando: se reinicia el host → Vault
arranca sellado → si el SSO leyera su client secret de Vault, nadie entraría por
SSO hasta que un humano apareciese con su fragmento de Shamir. Una regla sin
excepciones que esconde esa trampa no es más limpia: es la misma complejidad
movida al peor momento posible.

**Tres cosas que van con la excepción y no son negociables:**

1. **La frontera no crece.** Añadir una familia a la lista de la derecha exige un
   ADR nuevo que argumente por qué no es una credencial de plataforma. Un secreto
   sin el cual la plataforma no arranca NO cabe aquí por definición.
2. **No viajan en el backup.** Los datos de `sso_configurations`,
   `notification_channels` e `incoming_webhook_configs` se excluyen del `pg_dump`
   (`WORKERS_BACKUP_COLUMN_SECRET_TABLES`, `workers/backup_secrets.py`): con el
   ciphertext dentro, quien robase el bundle **y** conociera la variable de
   entorno tendría los secretos, y el bundle viaja a MinIO y a destinos externos.
   El precio —reconfigurar esas integraciones tras un DR— está en
   [06-runbooks/04-disaster-recovery.md](docs/06-runbooks/04-disaster-recovery.md).
3. **La rotación es la de prod-05**, no una propia: anillos `*_ENCRYPTION_KEYS`
   (cabeza + cola) y `api_server.cli.reencrypt_secrets`.

**Y caduca sola.** El día que se adopte **auto-unseal** de Vault (opciones A o B
de la decisión 2 del ADR 0145) desaparece la objeción de disponibilidad que la
justifica, y el ADR 0146 debe reabrirse hacia la migración a Vault. Está anotado
en los dos ADR para que se lea desde ambos lados: si estás leyendo esto en un
stack con auto-unseal, esta sección está vencida.

## Contexto Adicional

- `docs/context/memoria-del-asistente.md` — **léelo al arrancar en una máquina
  nueva.** Las órdenes permanentes del operador, las constantes del proyecto que no
  se deducen del código y la cola de pendientes que no vive en ningún plan. La
  memoria de Claude Code se guarda fuera del repo (`~/.claude/projects/…/memory/`) y
  se perdería al cambiar de ordenador: ese fichero explica cómo rehidratarla desde
  el archivo verbatim de `docs/context/memoria-asistente/`.
- `docs/context/architecture-overview.md` — visión arquitectónica resumida.
- `docs/context/glossary.md` — términos del dominio.
- `docs/context/tech-stack.md` — stack tecnológico detallado.
- `docs/context/conventions.md` — convenciones de código y commits.
- `docs/03-guides/gotchas/` — trampas conocidas del toolchain (Docker, asyncpg, mypy, pre-commit, OTEL, Windows…) con síntoma + causa raíz + fix. Antes de inventar una solución para un error de infraestructura, **busca aquí primero**; si lo resuelves y la trampa no estaba documentada, **añádela**.
- `docs/03-guides/verificar-antes-de-implementar.md` — la otra mitad: modos de fallo que NO dan error sino trabajo perdido o confianza injustificada (un plan «pendiente» que miente, un test que fija el defecto, una guarda que pasa vacía). Léelo antes de implementar tareas de un plan antiguo.
- `docs/roadmap/` — planes por fase (00 a 15), uno por archivo Markdown.
- `docs/roadmap/README.md` — índice de fases del roadmap.
- `especificaciones-completas.docx` (ubicación según convención del repo) — documento maestro con TODO el detalle (36 secciones + anexo).

## Sobre el Documento Maestro

El `.docx` es la fuente de verdad. Si en algún momento Claude Code tiene una intuición que contradice el documento, **el documento gana**. El documento puede tener huecos en detalles concretos de implementación (eso es intencional, son decisiones técnicas a tomar durante el desarrollo), pero las decisiones de producto y arquitectura ya están cerradas.

Cuando una fase remite a "ver sección X.Y", esa referencia es al .docx. Para no tener que abrirlo constantemente, cada fase en `docs/roadmap/` resume las secciones relevantes; el .docx es para consultas profundas.
