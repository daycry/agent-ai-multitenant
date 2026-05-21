"""Built-in skill catalog (task_01_10).

Thirty-three skills across six categories. Each skill ships with a
short `prompt_fragment` that gets injected into the system prompt of
any agent that has it. Skills are NOT executable code -- they're
narrative cues that nudge the agent toward a particular shape of work.

Seeded under the platform tenant with stable uuid5 IDs. `required_tools`
ships empty for now; tools land in task_01_11 and a later task may
wire cross-references via slug.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import PLATFORM_TENANT_ID, SKILL_SEED_NAMESPACE


def _skill_id(slug: str) -> UUID:
    return uuid5(SKILL_SEED_NAMESPACE, f"skill:{slug}")


@dataclass(frozen=True)
class BuiltinSkill:
    slug: str
    name: str
    category: str
    description: str
    prompt_fragment: str

    @property
    def id(self) -> UUID:
        return _skill_id(self.slug)


# ---------------------------------------------------------------------------
# Catalog -- 33 skills across 6 categories
# ---------------------------------------------------------------------------
BUILTIN_SKILLS: tuple[BuiltinSkill, ...] = (
    # ------ Backend ------
    BuiltinSkill(
        "python-pytest",
        "Python + pytest",
        "backend",
        "Diseña y ejecuta tests con pytest.",
        "Escribes tests pytest expresivos: usa fixtures sobre setup/teardown, "
        "parametriza casos de borde, evita mocks sobre la unidad real cuando "
        "puedes usar un doble más fino. Cada test debería leerse como un "
        "ejemplo del comportamiento que documenta.",
    ),
    BuiltinSkill(
        "sqlalchemy-async",
        "SQLAlchemy async",
        "backend",
        "ORM async con asyncpg y patrones modernos.",
        "Manejas SQLAlchemy 2.x async con asyncpg: AsyncSession, mapped_column, "
        "Mapped[...] types, select() y joinedload(). Conoces las limitaciones "
        "de asyncpg con SET LOCAL (usa set_config) y nunca devuelves objetos ORM "
        "tras cerrar la sesión.",
    ),
    BuiltinSkill(
        "fastapi-routing",
        "FastAPI routing",
        "backend",
        "Convenciones REST con FastAPI + Pydantic.",
        "Diseñas routers FastAPI con prefijo claro, response_model explícito, "
        "y dependencias inyectadas para auth/sesión. Validas inputs con Pydantic "
        "v2 (Field, model_validator). Devuelves 4xx para errores del cliente "
        "y 5xx solo para fallos genuinos del servidor.",
    ),
    BuiltinSkill(
        "database-migrations",
        "Database migrations",
        "backend",
        "Diseña migraciones Alembic reversibles.",
        "Cada migración Alembic es reversible (upgrade + downgrade simétricos). "
        "Una migración hace una sola cosa coherente. Cambios que requieren "
        "backfill van en pasos separados: añadir columna nullable, backfill, "
        "marcar NOT NULL.",
    ),
    BuiltinSkill(
        "background-jobs",
        "Background jobs",
        "backend",
        "Diseño de tareas en background con Celery.",
        "Diseñas jobs en background con Celery: tasks idempotentes, retries con "
        "backoff exponencial, dead-letter para fallos permanentes. Estado "
        "transitorio en Redis; estado durable en Postgres.",
    ),
    BuiltinSkill(
        "api-versioning",
        "API versioning",
        "backend",
        "Estrategia de versionado de APIs públicas.",
        "Versionas APIs públicas con /v1, /v2 y mantienes la versión anterior "
        "al menos un ciclo mayor. Cambios breaking nunca van a una versión "
        "publicada. Documentas la deprecación con fecha y migración recomendada.",
    ),
    # ------ Frontend ------
    BuiltinSkill(
        "nextjs-app-router",
        "Next.js App Router",
        "frontend",
        "Patrones App Router (server/client components).",
        "Construyes UI con Next.js App Router. Distingues Server Components "
        "(default) de Client Components ('use client'). Data fetching en "
        "el servidor cuando puedes; useEffect solo para efectos puramente "
        "de cliente. Loading + error UIs explícitos.",
    ),
    BuiltinSkill(
        "tailwind-design",
        "Tailwind CSS",
        "frontend",
        "Diseño utility-first con Tailwind.",
        "Compones UI con clases utility de Tailwind. Para repetición usas "
        "componentes y `cn()` (clsx + tailwind-merge), no @apply. Mantienes "
        "design tokens en `tailwind.config.ts` y los referencias por nombre "
        "(`primary`, `muted-foreground`) en vez de hardcodear hex.",
    ),
    BuiltinSkill(
        "shadcn-components",
        "shadcn/ui composition",
        "frontend",
        "Composición de componentes shadcn/ui.",
        "Compones UI usando primitivos de shadcn/ui (Button, Dialog, Form, "
        "Table). Los copias al repo, no los importas como dependencia. "
        "Cuando necesitas un nuevo primitive, lo añades una vez y lo reutilizas.",
    ),
    BuiltinSkill(
        "tanstack-query",
        "TanStack Query",
        "frontend",
        "Fetching y caching con TanStack Query.",
        "Manejas estado servidor con TanStack Query. Cada query con queryKey "
        "estable, staleTime explícito y refetchInterval cuando hace falta "
        "polling. Invalidaciones quirúrgicas (queryKey específica), no flushes "
        "globales.",
    ),
    BuiltinSkill(
        "accessibility-aria",
        "Accessibility (ARIA)",
        "frontend",
        "WAI-ARIA + navegación por teclado.",
        "Cada control interactivo tiene un nombre accesible y un rol válido. "
        "Navegación completa por teclado (Tab, Enter, Esc, flechas donde "
        "aplica). Anuncias cambios dinámicos con aria-live. Contraste AA "
        "mínimo en todos los textos.",
    ),
    BuiltinSkill(
        "responsive-design",
        "Responsive design",
        "frontend",
        "Mobile-first y breakpoints semánticos.",
        "Diseñas mobile-first: layout base para 360px, breakpoints sm/md/lg/xl "
        "para añadir capacidad cuando hay espacio. Pruebas en al menos tres "
        "tamaños antes de marcar una pantalla como terminada.",
    ),
    # ------ DevOps ------
    BuiltinSkill(
        "dockerfile-optimization",
        "Dockerfile optimization",
        "devops",
        "Multi-stage + cache de capas.",
        "Escribes Dockerfiles multi-stage. Ordenas las capas por frecuencia "
        "de cambio (deps antes que código). Imagen final con un usuario "
        "no-root, sin shell, sin paquetes de build. Pinneas las versiones "
        "base por digest cuando el SLA lo justifica.",
    ),
    BuiltinSkill(
        "docker-compose-orchestration",
        "Docker Compose orchestration",
        "devops",
        "Stacks compose con healthchecks y depends_on.",
        "Diseñas stacks Compose con healthchecks reales (no solo "
        "puerto-abierto), depends_on por condición, override files para "
        "dev/staging/prod. Volúmenes nombrados, no bind mounts en prod. "
        "`restart: unless-stopped` como default razonable.",
    ),
    BuiltinSkill(
        "github-actions-ci",
        "GitHub Actions CI",
        "devops",
        "Workflows reutilizables y matrices.",
        "Diseñas workflows GitHub Actions con jobs concisos y reusable "
        "workflows para lo compartido. Caches explícitos por path (Python "
        "venv, node_modules, Docker layers). Matrices solo cuando aportan "
        "valor; si no, un job único más legible.",
    ),
    BuiltinSkill(
        "observability-otel",
        "OpenTelemetry observability",
        "devops",
        "Traces, metrics, logs correlados por trace_id.",
        "Configuras OpenTelemetry con TracerProvider único, auto-instrumentación "
        "del stack (FastAPI, asyncpg, Redis, httpx), propagación W3C. Logs "
        "estructurados con trace_id + span_id inyectados. Sampling agresivo "
        "en dev, conservador en prod.",
    ),
    BuiltinSkill(
        "secrets-vault",
        "Secrets management (Vault)",
        "devops",
        "Patrones de uso de HashiCorp Vault.",
        "Trabajas con HashiCorp Vault: KV v2 para secretos arbitrarios, "
        "engines dedicados (database, aws) cuando puedes. Lease + renewal "
        "para credenciales rotativas. Nunca commiteas un secreto; el "
        "código pide a Vault, no lee env vars sin saber su origen.",
    ),
    BuiltinSkill(
        "infrastructure-as-code",
        "Infrastructure as code",
        "devops",
        "Terraform / Ansible con estado versionado.",
        "Defines infra con Terraform (cloud) o Ansible (config). Estado "
        "remoto y bloqueado. Diff de plan antes de apply. Recursos con "
        "lifecycle.prevent_destroy en cosas con datos. Outputs documentados "
        "para que otros módulos los consuman.",
    ),
    # ------ QA ------
    BuiltinSkill(
        "test-pyramid-design",
        "Test pyramid design",
        "qa",
        "Distribución unit / integration / e2e.",
        "Diseñas la pirámide de tests con muchas pruebas unit baratas, un "
        "puñado de integration que prueban contratos entre componentes, "
        "y E2E mínimos sobre los flujos críticos. Si una capa es lenta o "
        "frágil, lo cuestionas en vez de añadir retries.",
    ),
    BuiltinSkill(
        "playwright-e2e",
        "Playwright E2E",
        "qa",
        "Tests E2E robustos con Playwright.",
        "Escribes E2E con Playwright. Selectores semánticos (getByRole, "
        "getByLabel) sobre CSS frágil. Waits explícitos para el estado UI, "
        "nunca sleep arbitrario. Cada test independiente: setup/cleanup en "
        "el propio test, no en hooks globales.",
    ),
    BuiltinSkill(
        "property-based-testing",
        "Property-based testing",
        "qa",
        "Hypothesis para invariantes.",
        "Cuando el comportamiento se puede expresar como una invariante "
        "(ej. round-trip, idempotencia, conmutatividad), escribes tests "
        "property-based con Hypothesis. Estos cazan casos que el cerebro "
        "humano no enumera.",
    ),
    BuiltinSkill(
        "regression-test-strategy",
        "Regression test strategy",
        "qa",
        "Bug → test → fix.",
        "Para cada bug: primero escribes el test que lo reproduce, luego "
        "lo arreglas. El test queda como red de seguridad. Si el bug venía "
        "de un caso de borde no contemplado, ese caso se vuelve test "
        "permanente.",
    ),
    BuiltinSkill(
        "edge-case-identification",
        "Edge case identification",
        "qa",
        "Análisis de límites y casos extraños.",
        "Para cada feature identificas casos de borde: input vacío, máximos, "
        "negativos, caracteres Unicode raros, concurrencia, multi-tenant "
        "cross-talk, timeouts, datos parcialmente consumidos. Lo que no "
        "está probado, no está protegido.",
    ),
    # ------ Research ------
    BuiltinSkill(
        "technical-comparison",
        "Technical comparison",
        "research",
        "Evaluación de librerías y frameworks.",
        "Comparas opciones técnicas con criterios explícitos (mantenimiento, "
        "comunidad, perf, license, lock-in). Tabla pros/contras corta y un "
        "veredicto razonado. Si dos están empatadas, lo dices; no inventas "
        "una ganadora.",
    ),
    BuiltinSkill(
        "literature-review",
        "Literature review",
        "research",
        "Síntesis de papers y RFCs.",
        "Lees papers y RFCs y produces un resumen ejecutable: qué problema "
        "atacan, qué proponen, qué resultados muestran, dónde están sus "
        "límites. Citas con URL/DOI. Tu output es un dossier corto que "
        "alimenta una decisión del arquitecto.",
    ),
    BuiltinSkill(
        "cost-benefit-analysis",
        "Cost-benefit analysis",
        "research",
        "Cuantificas trade-offs.",
        "Cuando una decisión tiene impacto en coste/tiempo, lo estimas con "
        "cifras: tokens/petición, € al mes, tiempo de implementación, riesgo "
        "de mantenimiento. Sin cifras, no es análisis: es opinión.",
    ),
    BuiltinSkill(
        "competitive-analysis",
        "Competitive analysis",
        "research",
        "Escaneo de mercado y alternativas.",
        "Mapeas el espacio competitivo de una herramienta o feature: quién "
        "lo hace, cómo, qué fortalezas/debilidades, qué hueco queda. Útil "
        "para evitar reinventar y para encontrar oportunidades reales.",
    ),
    BuiltinSkill(
        "evidence-collection",
        "Evidence collection",
        "research",
        "Citas verificables.",
        "Cada afirmación técnica importante con una cita: URL, RFC, paper, "
        "commit. No confundes 'lo dice un blog' con 'lo documenta el "
        "fabricante'. Marcas la fecha de la fuente -- la tecnología envejece "
        "rápido.",
    ),
    # ------ Docs ------
    BuiltinSkill(
        "structured-writing",
        "Structured writing",
        "docs",
        "Frases cortas, jerarquía clara.",
        "Escribes docs técnicos en frases cortas, una idea por párrafo. "
        "Cada heading anticipa lo que el lector va a encontrar. Ejemplos "
        "antes que prosa abstracta. Si una explicación lleva tres "
        "intentos, el concepto subyacente está mal modelado.",
    ),
    BuiltinSkill(
        "mermaid-diagrams",
        "Mermaid diagrams",
        "docs",
        "Diagramas como código.",
        "Diagramas en Mermaid (flowchart, sequenceDiagram, erDiagram, "
        "stateDiagram). Versionables, diffables, renderizables en GitHub. "
        "Usas un diagrama cuando ahorra cinco párrafos; no por estética.",
    ),
    BuiltinSkill(
        "adr-authoring",
        "ADR authoring",
        "docs",
        "Architecture Decision Records.",
        "Escribes ADRs con cuatro secciones: Contexto, Decisión, "
        "Alternativas descartadas (con razón), Consecuencias. Una decisión "
        "por ADR. Status: proposed/accepted/superseded. Numerados, "
        "inmutables una vez aceptados.",
    ),
    BuiltinSkill(
        "runbook-authoring",
        "Runbook authoring",
        "docs",
        "Guías operativas paso a paso.",
        "Runbooks que un oncall recién despierto pueda seguir: pasos "
        "numerados, comandos exactos copy-pasteables, verificaciones "
        "intermedias, criterio de rollback. Asumes contexto cero del "
        "lector.",
    ),
    BuiltinSkill(
        "api-documentation",
        "API documentation",
        "docs",
        "OpenAPI + ejemplos curl.",
        "Cada endpoint con resumen, parámetros, respuesta de éxito, "
        "errores típicos con código + cuerpo, y al menos un ejemplo curl "
        "funcional. OpenAPI generado del código; documentación humana "
        "alrededor del schema.",
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text(
    """
    INSERT INTO skills (
        id, tenant_id, name, category, description,
        prompt_fragment, required_tools, is_builtin
    )
    VALUES (
        :id, :tenant_id, :name, :category, :description,
        :prompt_fragment, '[]'::jsonb, true
    )
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        category = EXCLUDED.category,
        description = EXCLUDED.description,
        prompt_fragment = EXCLUDED.prompt_fragment,
        updated_at = now()
    """
)


async def seed_builtin_skills(session: AsyncSession) -> int:
    for skill in BUILTIN_SKILLS:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(skill.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": skill.name,
                "category": skill.category,
                "description": skill.description,
                "prompt_fragment": skill.prompt_fragment,
            },
        )
    return len(BUILTIN_SKILLS)
