---
title: Convenciones de Código y Commits
last_updated: 2026-06-02
status: published
docs_language: es
---

# Convenciones de Código y Commits

Convenciones vigentes del repo. Visión transversal en
[`architecture-overview.md`](architecture-overview.md); stack en
[`tech-stack.md`](tech-stack.md).

## Python

### Estilo

- **Formato**: `black` con línea máxima de 100 caracteres.
- **Lint**: `ruff` con configuración estricta. Activar reglas E, W, F, I, B, C4, UP, N, S, A.
- **Type checking**: `mypy --strict`. Todo código nuevo debe pasar mypy strict.
- **Imports**: ordenados con `ruff --fix` o `isort` (perfil black).
- **Docstrings**: estilo Google. Obligatorias en funciones públicas y clases.

### Estructura

```python
# Imports en este orden, separados por línea en blanco:
# 1. stdlib
# 2. third-party
# 3. local

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_current_tenant
from app.domain.agent import Agent
```

### Async

- Toda capa de IO es async (HTTP, DB, Redis, LLM calls, file IO con aiofiles).
- Nunca mezclar sync y async en el mismo path (no usar `requests` en código async, usar `httpx`).
- Async generators para streaming.

### Modelos

- **Pydantic v2** para DTOs de API.
- **SQLAlchemy 2.x async** para modelos ORM.
- Separar siempre `domain models` (Pydantic) de `db models` (SQLAlchemy). Mapper explícito entre ambos.

### Errores

- Usar excepciones tipadas custom (`AgentNotFoundError`, `TenantQuotaExceededError`).
- Mapear excepciones a respuestas HTTP en un único handler central.
- Formato de error: Problem Details (RFC 7807).

## TypeScript

### Estilo

- **Formato**: `prettier` con tabWidth 2, singleQuote true, semi true.
- **Lint**: `eslint` con `@typescript-eslint/strict` + `@typescript-eslint/recommended-type-checked`.
- **Tipos**: prohibido `any`. Usar `unknown` o tipos específicos. `// @ts-ignore` requiere comentario justificativo.

### Estructura

- Componentes React funcionales con hooks. Sin clases.
- Hooks custom en `src/hooks/`.
- Tipos compartidos en `src/types/` o generados desde OpenAPI con `openapi-typescript`.

### Estado

- Estado servidor: TanStack Query con queryKeys consistentes.
- Estado cliente: Zustand para estado global, useState/useReducer para local.
- NO usar Context para estado mutable globalmente (mal performance).

## SQL y Migraciones

### PostgreSQL

- Nombres de tabla en `snake_case` plural (`agents`, `tasks`).
- Nombres de columna en `snake_case` singular.
- Claves primarias `id UUID DEFAULT uuidv7()`.
- Timestamps con `TIMESTAMPTZ`, nunca `TIMESTAMP` sin zona.
- Soft-delete con `deleted_at TIMESTAMPTZ NULL`, índices parciales `WHERE deleted_at IS NULL`.
- Todas las tablas tenant-scoped tienen `tenant_id UUID NOT NULL` y RLS activada.

### Alembic

- Cada migración tiene `upgrade()` y `downgrade()` simétricos; se prueba **up/down/up** antes de mergear (reversibilidad obligatoria).
- **Single head**: cada plan encadena sus revisiones sobre la cabeza única vigente; no se crean ramas de migración paralelas.
- **`revision` ≤ 32 caracteres**: `alembic_version.version_num` es `varchar(32)`; un id largo (p.ej. `20260601_0072_projects_command_config`) **rompe el upgrade**. Usar el id corto (`0072_projects_command_config`) como `revision` aunque el fichero lleve el prefijo de fecha. Ver gotcha `alembic-revision-id-length` en `docs/03-guides/gotchas/`.
- Migraciones NO transaccionales para operaciones que crean índices (`CREATE INDEX CONCURRENTLY`).
- Cambios destructivos (DROP COLUMN, RENAME TABLE) en dos releases con feature flag.
- Toda tabla tenant-scoped nace con `tenant_id UUID NOT NULL` + RLS (`tenant_isolation FOR ALL`). El catálogo global usa platform tenant + bandera `is_builtin` + `_builtin_read FOR SELECT` (ADR 0029); los catálogos sin tenant (`model_prices`, `exchange_rates`) usan lectura global `FOR SELECT USING (true)`.
- Migración mensual automática para crear nuevas particiones de tablas particionadas.

## Git

### Commits

Convención: **Conventional Commits** con trailers obligatorios. Los commits de
tareas de agentes llevan `Plan-Id` / `Task-Id` / `Execution-Id`; los commits
generados por Claude Code añaden además `Co-Authored-By`.

```
feat(users): implement POST /users endpoint

Adds the endpoint with validation, hashing, and audit logging.
Returns 201 with the new user's id in the body.

Plan-Id: 01H7K-implementar-auth-oauth
Task-Id: task_xyz123
Execution-Id: exec_abc456
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

Para planes documentales o de tooling sin `Execution-Id` real basta
`Plan-Id` + `Task-Id` (+ `Co-Authored-By`).

Tipos permitidos: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`, `perf`, `build`, `ci`.

Scopes recomendados: nombre del módulo o capa (`users`, `auth`, `kanban`, `docs`, `infra`).

### Ramas

- `main`: rama default, protegida. Solo se mergea desde PRs.
- `plan/{plan_id_short}-{slug}`: rama por plan, creada automáticamente al sincronizar al Kanban.
- `hotfix/{descripcion}`: para fixes urgentes fuera del flujo de planning normal.
- `docs/{descripcion}`: para cambios de docs aislados (poco usado, normalmente los docs van con el plan).

### Pull Requests

- Uno por plan (no por tarea).
- Cuerpo auto-generado por el sistema con resumen del plan, tareas, tests, decisiones, link al detalle en la UI.
- Reviewers asignados según CODEOWNERS + project_owner.
- Labels: `plan/{id}`, `agents:generated`, tipo de cambio detectado.

### Merge Policy

Determinado por el campo `push_policy` del repo:

- `forbidden`: bloqueado.
- `branch_only_pr_required` (default): PR abierto, merge manual humano.
- `direct_to_default_allowed`: merge automático si CI passes.

## Documentación

### Estructura Canónica /docs/

7 carpetas numeradas obligatorias:

```
/docs/
├── README.md
├── 01-overview/
├── 02-getting-started/
├── 03-guides/
├── 04-reference/
├── 05-architecture-decisions/
├── 06-runbooks/
└── 07-changelog/
```

### Formato Markdown

- Frontmatter YAML obligatorio:

```yaml
---
title: Título del Documento
last_updated: 2026-05-20
plan_id: 01H7K
related_tasks: [task_001, task_007]
status: published
---
```

- Headers jerárquicos: H1 = título único, H2 = secciones, H3 = subsecciones. No saltar niveles.
- Bloques de código con language tag obligatorio.
- Diagramas con Mermaid embebido (bloques ` ```mermaid `).
- Enlaces internos relativos (`./02-architecture.md`), no absolutos.
- Cada documento abre con párrafo de 2-3 líneas que lo resume.
- **prettier siempre _scoped_ a los ficheros tocados** (`pre-commit run prettier --files <archivos>`). El hook repo-wide (`--all-files`) **crashea en Windows** por libuv (`UV_HANDLE_CLOSING`); ver gotcha `prettier-all-files-libuv-windows` en `docs/03-guides/gotchas/`.

### ADRs

Numerados secuencialmente, formato consistente:

```markdown
---
title: 0007 — Use git worktrees for task parallelism
status: accepted
date: 2026-05-20
deciders: [system_admin, tech_lead]
---

# 0007 — Use git worktrees for task parallelism

## Context

What problem we're solving and why.

## Decision

What we've decided.

## Alternatives Considered

- Option A: ...
- Option B: ...

## Consequences

Positive and negative implications.
```

## Tests

### Organización

```
tests/
├── unit/           # Pure functions, no IO
├── integration/    # Con DB efímera, sin red externa
└── e2e/            # Sistema end-to-end con docker-compose de prueba
```

### Cobertura

- Dominio crítico (auth, multi-tenancy, agent loop, orchestrator): > 80%.
- Resto: > 70%.

### Convenciones

- Nombres de tests descriptivos: `test_create_user_with_invalid_email_returns_422`, no `test_users_1`.
- Arrange-Act-Assert con líneas en blanco separando bloques.
- Fixtures en `conftest.py` por nivel.
- Tests de aislamiento multi-tenant en `tests/integration/test_isolation.py` — obligatorios.

## Seguridad

### Reglas Inquebrantables

1. **NUNCA** queries SQL sin filtro por `tenant_id` o sin middleware que lo inyecte.
2. **NUNCA** secretos en código. Vault es la única vía.
3. **NUNCA** logs con PII no enmascarada. Filtros activos para email, IBAN, DNI, tokens.
4. **NUNCA** `subprocess.run` con `shell=True` o concatenación de strings. Usar lista de argumentos.
5. **NUNCA** `pickle.loads` de datos no confiables. Usar JSON.
6. **NUNCA** desactivar verificación TLS en producción.

### Buenas Prácticas

- Inputs siempre validados con Pydantic.
- Outputs hacia exterior sanitizados.
- Rate limiting en todos los endpoints públicos.
- CSRF tokens en endpoints de cambio de estado.
- Headers de seguridad: CSP, HSTS, X-Content-Type-Options, X-Frame-Options.

## Performance

### Reglas Generales

- Endpoints API: p95 < 500ms para lecturas.
- Búsqueda en memoria/RAG con HNSW: p95 < 100ms para top-10.
- Arranque de contenedor agent-runtime: < 5s.

### Optimizaciones

- Connection pooling para PostgreSQL (mínimo 10, máximo 50 por servicio).
- Caché Redis con TTL razonable para queries repetidas.
- Paginación obligatoria en todos los listados.
- N+1 prohibido: usar `selectinload` o `joinedload` en SQLAlchemy.
- Imágenes Docker multi-stage para reducir tamaño.

## Observabilidad

- Todo endpoint genera span de OpenTelemetry con trace_id propagable.
- Logs estructurados JSON con campos estándar: timestamp, level, service, trace_id, span_id, tenant_id, user_id, project_id.
- Métricas Prometheus por endpoint, por worker, por modelo LLM.
- Healthcheck endpoint `/health` con status detallado por dependencia.
