"""Built-in skill catalog (task_01_10).

55 skills across seven categories. Each skill ships with a short
`prompt_fragment` that gets injected into the system prompt of any agent
that has it. Skills are NOT executable code -- they're narrative cues
that nudge the agent toward a particular shape of work.

Seeded under the platform tenant with stable uuid5 IDs. `required_tools`
ships empty for now; tools land in task_01_11 and a later task may
wire cross-references via slug. The `atlassian` category (2026-07-23,
ADR 0127/0128) teaches agents to drive the project's Atlassian MCP
(Jira + Confluence) — those skills carry no `required_tools` either;
the tools arrive as a project capability at runtime (ADR 0128).
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
# Catalog -- 55 skills across 7 categories (33 base + 18 Ola B0.1 + 4 Atlassian)
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
    # ------ Backend (Ola B0.1: PHP/CI4 + security + data) ------
    BuiltinSkill(
        "php-phpunit",
        "PHP + PHPUnit",
        "backend",
        "Tests con PHPUnit en PHP moderno.",
        "Escribes tests PHPUnit claros: data providers para casos de borde, "
        "dobles solo cuando la unidad real es cara o no determinista, asserts "
        "específicos (assertSame sobre assertEquals cuando importa el tipo). "
        "Cada test nombra el comportamiento que documenta.",
    ),
    BuiltinSkill(
        "codeigniter4-hmvc",
        "CodeIgniter 4 HMVC",
        "backend",
        "Módulos HMVC en CodeIgniter 4.",
        "Estructuras el código en módulos bajo app/Modules/ con su propio "
        "routing, controllers, models y views (HMVC). Controllers finos, "
        "Models con validación, Entities para la lógica de dominio y Services "
        "para orquestar. Respetas PSR-12.",
    ),
    BuiltinSkill(
        "doctrine-orm",
        "Doctrine ORM",
        "backend",
        "Mapeo y consultas con Doctrine (daycry/doctrine).",
        "Modelas entidades Doctrine con atributos, repositorios para queries y "
        "DQL/QueryBuilder en vez de SQL crudo. Evitas el N+1 con fetch joins, "
        "usas transacciones explícitas para escrituras múltiples y mantienes "
        "las migraciones de esquema versionadas.",
    ),
    BuiltinSkill(
        "secure-coding-owasp",
        "Secure coding (OWASP)",
        "backend",
        "Defensa contra el OWASP Top 10.",
        "Validas y sanitizas toda entrada, usas consultas parametrizadas (nunca "
        "concatenas SQL), escapas la salida según contexto (HTML/JS/URL), "
        "aplicas authz por recurso (no solo authn) y no logueas secretos. "
        "Piensas como atacante: ¿qué pasa con input malicioso en cada borde?",
    ),
    BuiltinSkill(
        "sql-optimization",
        "SQL optimization",
        "backend",
        "Consultas e índices eficientes.",
        "Lees EXPLAIN/ANALYZE antes de optimizar; añades índices que cubran el "
        "patrón de acceso real (no a ciegas); evitas SELECT *, funciones sobre "
        "columnas indexadas y el N+1; paginas con keyset cuando el offset "
        "crece. Mides antes y después.",
    ),
    BuiltinSkill(
        "rag-pgvector",
        "RAG con pgvector",
        "backend",
        "Recuperación híbrida sobre pgvector.",
        "Combinas recuperación léxica (tsvector/BM25) y semántica (pgvector, "
        "coseno) fusionadas con RRF; chunkeas con solapamiento sensato; "
        "normalizas embeddings a la dimensión del índice (HNSW); y citas las "
        "fuentes. Evalúas el recall con consultas reales.",
    ),
    # ------ Frontend (Ola B0.1) ------
    BuiltinSkill(
        "twig-templating",
        "Twig templating",
        "frontend",
        "Plantillas Twig (daycry/twig).",
        "Separas presentación de lógica: la vista Twig solo formatea; los datos "
        "llegan listos del controller. Usas herencia de plantillas "
        "(extends/blocks), autoescape activo e includes/macros para reutilizar. "
        "Nada de queries ni lógica de negocio en la plantilla.",
    ),
    BuiltinSkill(
        "state-management",
        "Frontend state management",
        "frontend",
        "Estado de cliente vs servidor.",
        "Distingues estado de servidor (cachéalo con TanStack Query: "
        "invalidación, staleTime) del estado de UI local (useState/useReducer). "
        "No duplicas estado de servidor en stores globales; levantas el estado "
        "solo lo necesario; y derivas en vez de sincronizar.",
    ),
    BuiltinSkill(
        "web-performance",
        "Web performance",
        "frontend",
        "Core Web Vitals y carga.",
        "Optimizas LCP/CLS/INP: imágenes dimensionadas y lazy, code-splitting "
        "por ruta, reservar espacio para evitar layout shift, y minimizar JS en "
        "el camino crítico. Mides con Lighthouse/web-vitals antes de afirmar "
        "mejoras.",
    ),
    # ------ DevOps (Ola B0.1) ------
    BuiltinSkill(
        "dependency-audit-sca",
        "Dependency audit (SCA)",
        "devops",
        "Análisis de composición de software.",
        "Ejecutas SCA (pip-audit/npm audit/composer audit) en CI; fijas "
        "versiones y revisas CVEs por severidad y explotabilidad real; "
        "priorizas parches de seguridad sin romper; y documentas excepciones "
        "con justificación y fecha de revisión.",
    ),
    BuiltinSkill(
        "backup-recovery",
        "Backup & recovery",
        "devops",
        "Copias verificables y restauración.",
        "Diseñas backups con la regla 3-2-1, cifrados en reposo, y — lo más "
        "importante — pruebas la RESTAURACIÓN periódicamente (un backup no "
        "verificado no existe). Defines RPO/RTO explícitos y un runbook de "
        "recuperación paso a paso.",
    ),
    # ------ QA (Ola B0.1) ------
    BuiltinSkill(
        "contract-testing",
        "Contract testing",
        "qa",
        "Contratos entre servicios/API.",
        "Verificas que productor y consumidor cumplen el mismo contrato "
        "(esquema de request/response) con tests de contrato, no solo e2e. "
        "Versionas el contrato; un cambio incompatible falla el test del "
        "consumidor antes del deploy.",
    ),
    BuiltinSkill(
        "load-testing",
        "Load & stress testing",
        "qa",
        "Comportamiento bajo carga.",
        "Defines objetivos (throughput, p95/p99) y escenarios realistas (carga "
        "sostenida, picos, soak). Mides recursos durante la prueba, identificas "
        "el cuello de botella, y distingues límites de capacidad de fugas "
        "(degradación creciente).",
    ),
    # ------ Research (Ola B0.1) ------
    BuiltinSkill(
        "prompt-engineering",
        "Prompt engineering",
        "research",
        "Prompts efectivos para LLMs.",
        "Escribes prompts con rol claro, instrucciones específicas y formato de "
        "salida explícito; das ejemplos (few-shot) cuando la tarea es ambigua; "
        "pides razonamiento antes de la respuesta en tareas complejas; e iteras "
        "midiendo contra casos reales, no por intuición.",
    ),
    BuiltinSkill(
        "eval-design",
        "LLM eval design",
        "research",
        "Evaluación de salidas de LLM.",
        "Diseñas evals con un set representativo + criterios objetivos "
        "(rúbricas, asserts, o juez-LLM con rúbrica); separas dev/test para no "
        "sobreajustar; mides regresiones entre versiones de prompt/modelo; y "
        "reportas con varias corridas, no una sola.",
    ),
    BuiltinSkill(
        "web-research",
        "Web research",
        "research",
        "Buscar y citar en internet con criterio.",
        "Triangulas fuentes (no te fías de una sola), priorizas fuentes "
        "primarias y recientes, distingues hecho de opinión, y CITAS cada "
        "afirmación con su URL. Verificas de forma adversarial las afirmaciones "
        "clave antes de incorporarlas.",
    ),
    # ------ Docs (Ola B0.1) ------
    BuiltinSkill(
        "changelog-authoring",
        "Changelog authoring",
        "docs",
        "Changelogs útiles (Keep a Changelog).",
        "Escribes entradas orientadas al usuario agrupadas por tipo "
        "(Added/Changed/Fixed/Removed/Security), con el impacto y la acción de "
        "migración cuando aplica. Nada de volcar mensajes de commit crudos: el "
        "changelog cuenta QUÉ cambia para QUIÉN lo usa.",
    ),
    BuiltinSkill(
        "openapi-authoring",
        "OpenAPI authoring",
        "docs",
        "Especificaciones OpenAPI precisas.",
        "Defines OpenAPI con operationId estables, schemas reutilizables ($ref), "
        "ejemplos por respuesta y códigos de error documentados con su forma. El "
        "spec es la fuente de verdad del contrato; lo mantienes sincronizado con "
        "el código (generado del código cuando se puede).",
    ),
    # ------ Atlassian (integración Jira + Confluence vía MCP, ADR 0127/0128) ------
    # Enseñan a los agentes a USAR el MCP de Atlassian del proyecto. No cablean
    # nombres de tool namespaced (el operador elige el nombre del server) ni ids
    # (llegan por el plan). Idempotentes y degradan con gracia si el MCP no está.
    BuiltinSkill(
        "atlassian-jira-task-tracking",
        "Jira — seguimiento de tareas",
        "atlassian",
        "Refleja cada tarea de la plataforma en Jira: crea o localiza su issue bajo "
        "el epic del plan y transiciona su estado al empezar y al cerrarla; útil para "
        "backend_dev, frontend_dev y project_manager.",
        "Cuando empiezas una tarea, la reflejas en Jira con tus herramientas de Jira del "
        "proyecto: primero buscas si ya existe su issue —por el título de la tarea o por una "
        "clave que el plan haya registrado— y solo la creas si no aparece, normalmente como "
        "subtarea del epic del plan, volcando el título y la descripción de la tarea, de modo "
        "que nunca duplicas. Tomas la clave del epic y los demás identificadores del contexto "
        "del plan (su descripción, la de la tarea o un comentario del plan); si no están, los "
        "pides en un comentario del plan o creas la issue sin colgarla del epic en lugar de "
        "inventarlos, y trabajas siempre contra el proyecto Jira de este plan, nunca contra "
        "otros. Nada más localizarla o crearla la transicionas a «en curso», y al cerrar la "
        "tarea la llevas a «hecho», eligiendo siempre entre las transiciones que la herramienta "
        "te ofrezca en vez de asumir nombres de estado. Conforme avanzas, enlazas en la issue "
        "la rama, los commits o el PR en cuanto existan, para que Jira y el trabajo real queden "
        "conectados. Si en este run no dispones de tus herramientas de Jira, no fallas la tarea "
        "por ello: anotas el motivo en tu PROGRESS y sigues con el trabajo real.",
    ),
    BuiltinSkill(
        "atlassian-jira-review-notes",
        "Jira — notas de revisión",
        "atlassian",
        "Publica tu veredicto y hallazgos de revisión como comentario en la issue de "
        "Jira y transiciónala según apruebes o rechaces (reviewer, qa).",
        "Cuando cierras la revisión de una tarea, reflejas tu veredicto en Jira: localizas la "
        "issue por la clave que venga en el contexto del plan —descripción del plan o de la "
        "tarea, o un comentario del plan— y, si no la tienes clara, la buscas con tus "
        "herramientas de Jira por el título de la tarea antes de comentar o transicionar sobre "
        "la issue equivocada, operando siempre sobre issues del proyecto actual y nunca sobre "
        "otras aunque las veas listadas. Antes de escribir relees los comentarios existentes de "
        "esa issue para no duplicar un informe que ya dejaste en una iteración previa —si ya "
        "hay una nota equivalente la amplías en lugar de crear otra— y publicas tus hallazgos "
        "como comentario, nunca como una issue nueva, redactando de forma concreta qué apruebas "
        "o exactamente qué falta y enlazando al trabajo revisado (rama, PR o id de ejecución) "
        "para que la trazabilidad Jira↔código quede explícita. Después transicionas la issue "
        "consultando primero las transiciones disponibles y eligiendo por nombre la que "
        "corresponda al flujo del proyecto, sin asumir estados fijos: si apruebas la llevas al "
        "estado de revisión o cierre disponible, y si rechazas la devuelves a en curso o la "
        "reabres dejando escrito en el comentario el trabajo pendiente para que quien la retome "
        "sepa qué corregir. Si no encuentras la clave de la issue en el contexto, la pides en "
        "un comentario del plan o lo omites con gracia en vez de adivinarla; y si tus "
        "herramientas de Atlassian no están disponibles en este run, no fallas la tarea por "
        "ello: anotas en tu PROGRESS el veredicto, los hallazgos y el motivo por el que no "
        "pudiste reflejarlo en Jira, y sigues con el trabajo real de la revisión.",
    ),
    BuiltinSkill(
        "atlassian-confluence-docs",
        "Confluence — documentación",
        "atlassian",
        "Publica y mantén en sync la documentación del plan como páginas hijas de "
        "Confluence, sin duplicar y enlazando a las issues de Jira relacionadas.",
        "Cuando el trabajo documentable está listo —normalmente al cerrar el plan o al "
        "completar tu tarea de documentación— reflejas el resultado en Confluence con tus "
        "herramientas de crear y actualizar páginas, sin cablear nombres de servidor ni "
        "identificadores fijos. Tomas del contexto del plan (su descripción, la de la tarea o "
        "un comentario del plan) el espacio y la página padre bajo la que debes anclar; si ese "
        "dato no viene, lo pides o lo omites con gracia en vez de inventarlo. Antes de crear "
        "nada, buscas con tu herramienta de búsqueda si ya existe una página equivalente bajo "
        "ese padre y, de existir, la actualizas en lugar de duplicarla, de modo que la "
        "documentación quede sincronizada con el estado real al cierre. Creas y actualizas "
        "siempre como páginas HIJAS de esa página padre, dentro del espacio y el árbol del "
        "proyecto actual sin tocar páginas de otros proyectos ni tenants, y enlazas hacia las "
        "issues de Jira relacionadas cuyas claves tomas del propio plan para dejar trazabilidad "
        "en ambos sentidos entre el trabajo y sus tickets. Si en este run no dispones de la "
        "herramienta de Confluence, no das la tarea por fallida: anotas el motivo en tu PROGRESS "
        "y sigues adelante con el trabajo real, dejando la publicación pendiente para cuando el "
        "conector esté presente.",
    ),
    BuiltinSkill(
        "atlassian-jira-planning-context",
        "Jira — contexto de planificación",
        "atlassian",
        "Busca en Jira issues y epics relacionados antes de planificar o diseñar, para "
        "no duplicar trabajo y referenciar sus claves en el plan y en sus tareas.",
        "Cuando arrancas a planificar o diseñar, antes de proponer tareas nuevas usas tus "
        "herramientas de búsqueda de Jira para localizar issues y epics ya existentes "
        "relacionados con el objetivo, de modo que no dupliques trabajo ya registrado y alinees "
        "el plan con lo que hay. Tomas la clave del epic o los identificadores de proyecto del "
        "contexto del plan —su descripción, la de la tarea o un comentario del plan— y acotas "
        "la búsqueda al proyecto Jira actual sin cruzar a espacios de otros; si no vienen, "
        "buscas por los términos del objetivo y, si aún dudas, los pides en un comentario del "
        "plan en lugar de inventarlos. Este paso de consulta va siempre primero: solo tras "
        "verificar que un issue existe anotas su clave en la descripción del plan y en las "
        "tareas que dependan de él, dejando claro qué relación guarda cada una (amplía, depende "
        "de o solapa con lo ya existente) para que la trazabilidad sea real y no duplicada. "
        "Reflejas en tu PROGRESS qué issues encontraste y cuáles reutilizas frente a los que "
        "faltan. Si tus herramientas de Jira no están disponibles en este run, no fallas la "
        "tarea por ello: registras el motivo en tu PROGRESS y continúas la planificación con la "
        "información que ya tengas.",
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
