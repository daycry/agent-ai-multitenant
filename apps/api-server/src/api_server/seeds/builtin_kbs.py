"""Built-in canonical knowledge bases (Plan 06.9 task_06_9_06).

Catálogo inicial de KBs por **stack** que la plataforma siembra bajo
`PLATFORM_TENANT_ID`. Cada KB lleva un slug estable + una descripción
+ un puñado de chunks markdown del open-source upstream — suficiente
para que las plantillas de proyecto (Fase B task_07/08) las
referencien por slug y la wizard de adopción haga el grant.

Decisiones:

  * **Slugs estables**: cada KB tiene un slug ASCII kebab-case que
    NO cambia (las plantillas lo referencian). El UUID se deriva por
    `uuid5(KB_SLUG_NAMESPACE, slug)` así re-seedear no rompe los
    grants existentes.
  * **PLATFORM_TENANT_ID**: las KBs canónicas son **globales**: viven
    bajo el tenant especial de la plataforma. Cualquier tenant puede
    grantearlas a un proyecto suyo (ADR pendiente formaliza este
    "global builtin" análogo al de los agentes built-in).
  * **Embedding model**: dejamos el default `nomic-embed-text-v1.5`
    de la plataforma (ADR 0023) — re-embedar es una operación
    fuera-de-banda.
  * **Chunks de contenido**: este seed sólo crea el `KnowledgeBase`
    row. Los chunks reales los carga `setup_demo_06_9.py` o el
    pipeline de ingesta de Plan 04 a partir de los .md upstream
    en `apps/api-server/src/api_server/seeds/kb_content/`. Aquí
    sólo hacemos la metadata.

Refrescar el catálogo (e.g. añadir un nuevo stack) es:
  1. Añadir entrada en `BUILTIN_KBS`.
  2. Crear el .md de contenido si quieres chunks inmediatos.
  3. Re-correr `python -m api_server.seeds`.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import PLATFORM_TENANT_ID

# Namespace para uuid5("kb:<slug>") — separado de los otros (agentes,
# templates) por seguridad.
KB_SLUG_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000015")


def kb_id_for_slug(slug: str) -> UUID:
    """Returns the deterministic UUID of a built-in KB given its slug.

    Wizard / tests call this to resolve `default_kb_grants` (list of
    slugs) into actual ids without touching the DB.
    """
    return uuid5(KB_SLUG_NAMESPACE, f"builtin_kb:{slug}")


@dataclass(frozen=True)
class BuiltinKB:
    slug: str
    name: str
    description: str
    # Plan 06.10: slug de la categoría built-in que se aplica al
    # sembrar. Vacío = sin categoría.
    category_slug: str = ""

    @property
    def id(self) -> UUID:
        return kb_id_for_slug(self.slug)


# 6 KBs canónicas iniciales. Mezcla intencional de stack (Python /
# Node / PHP) y rol-agnósticas (API REST design / PostgreSQL).
BUILTIN_KBS: tuple[BuiltinKB, ...] = (
    BuiltinKB(
        slug="python-fastapi-conventions",
        name="Python + FastAPI conventions",
        description=(
            "Convenciones de stack para servicios Python con FastAPI: layout "
            "del repo, async/await, manejo de errores, OpenAPI, testing con "
            "pytest + httpx, y patrones de SQLAlchemy 2.x async."
        ),
        category_slug="stack",
    ),
    BuiltinKB(
        slug="node-express-conventions",
        name="Node + Express conventions",
        description=(
            "Convenciones para APIs Node.js con Express: estructura de "
            "carpetas, middleware, validación con zod, testing con vitest, "
            "y patrones de conexión a Postgres con pg/Prisma."
        ),
        category_slug="stack",
    ),
    BuiltinKB(
        slug="php-symfony-conventions",
        name="PHP + Symfony conventions",
        description=(
            "Convenciones de Symfony 6/7: controladores, autowiring, "
            "Doctrine, tests con phpunit, y migrations con doctrine-migrations."
        ),
        category_slug="stack",
    ),
    BuiltinKB(
        slug="postgresql-best-practices",
        name="PostgreSQL best practices",
        description=(
            "Buenas prácticas con PostgreSQL: diseño de esquema, índices, "
            "JSONB, row-level security, conexiones, vacuum/autovacuum, "
            "y migraciones reversibles."
        ),
        category_slug="stack",
    ),
    BuiltinKB(
        slug="api-rest-guidelines",
        name="API REST design guidelines",
        description=(
            "Principios de diseño de APIs REST: recursos, verbos HTTP, "
            "códigos de estado, versionado, paginación, filtrado, HATEOAS, "
            "y autenticación con OAuth 2 / JWT. Agnóstico de stack."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="react-nextjs-conventions",
        name="React + Next.js conventions",
        description=(
            "Convenciones para frontend con Next.js 14 + React: app router, "
            "server components, TanStack Query, formularios con react-hook-form, "
            "y testing con vitest + Playwright."
        ),
        category_slug="stack",
    ),
    # --- KBs built-in del equipo CodeIgniter 4 (plan codeigniter-4-builtin-team) ---
    # El corpus vive en seeds/catalog/codeigniter-4-*.md (purgado, sin marca de
    # proyecto). seed_catalog_ingestion las rellena con documents + chunks bajo
    # PLATFORM_TENANT_ID. Se exponen al RAG al adoptar la plantilla de proyecto
    # codeigniter-4-app (default_kb_grants), no per-agente (agent_knowledge_bases
    # niega grants a agentes global_builtin — migración 0026).
    BuiltinKB(
        slug="codeigniter-4-conventions",
        name="CodeIgniter 4 — Convenciones del equipo",
        description=(
            "Convenciones de stack para proyectos CodeIgniter 4: arquitectura "
            "HMVC, patrón Config+Items, estándares de código y toolchain, "
            "política i18n EN/ES y catálogo de dependencias del ecosistema "
            "(daycry/auth, daycry/doctrine, daycry/twig)."
        ),
        category_slug="stack",
    ),
    BuiltinKB(
        slug="codeigniter-4-architecture",
        name="CodeIgniter 4 — Arquitectura HMVC y routing",
        description=(
            "Arquitectura HMVC modular de CodeIgniter 4: estructura "
            "app/Modules/, patrón Config+Items, BaseEntity como "
            "MappedSuperclass, Second-Level Cache de Doctrine y routing "
            "config-driven."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="codeigniter-4-doctrine-data",
        name="CodeIgniter 4 — Modelo de datos con Doctrine",
        description=(
            "Modelo de datos con Doctrine 3.x vía daycry/doctrine: attribute "
            "mapping, BaseEntity (UUID, timestamps, soft-delete, lifecycle), "
            "funciones JSON, migraciones reversibles, seeds y regiones de "
            "Second-Level Cache."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="codeigniter-4-testing",
        name="CodeIgniter 4 — Estrategia de testing",
        description=(
            "Estrategia de testing con PHPUnit: suites Unit / Integration / "
            "E2E (Selenium/Chrome), modo estricto, phpunit.xml.dist y scripts "
            "composer de cobertura y mutación (Infection)."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="codeigniter-4-security",
        name="CodeIgniter 4 — Seguridad y autenticación",
        description=(
            "Seguridad y autenticación con daycry/auth: authenticators "
            "session/JWT/access-token, grupos y permisos, rate-limiting, "
            "CSP/Cookies/CSRF y buenas prácticas de gestión de secretos."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="codeigniter-4-i18n",
        name="CodeIgniter 4 — Internacionalización EN/ES",
        description=(
            "Internacionalización EN/ES: defaultLocale=en, "
            "supportedLocales=['en','es'], ficheros de idioma de CodeIgniter 4, "
            "daycry/codeigniter-language, columnas JSON {es,en} y la UI de "
            "pestañas de idioma."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="codeigniter-4-frontend",
        name="CodeIgniter 4 — Frontend y assets",
        description=(
            "Frontend y pipeline de assets: JS core, TinyMCE, Select2, "
            "DataTables, Bootstrap, versionado de assets para cache-busting y "
            "macros Twig de formulario con campos traducibles por locale."
        ),
        category_slug="role",
    ),
    BuiltinKB(
        slug="codeigniter-4-ci-cd",
        name="CodeIgniter 4 — CI/CD y despliegue",
        description=(
            "CI/CD y despliegue: pipeline build -> test -> deploy, imagen "
            "Docker PHP-FPM + Nginx + supervisor, health-check, gate solo en "
            "main y checklist de deploy."
        ),
        category_slug="role",
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text(
    """
    INSERT INTO knowledge_bases
        (id, tenant_id, name, description, embedding_model_id, category_id, is_builtin)
    VALUES
        (:id, :tenant_id, :name, :description, 'nomic-embed-text-v1.5', :category_id, true)
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        category_id = EXCLUDED.category_id,
        is_builtin = true,
        updated_at = now(),
        deleted_at = NULL
    """
)


async def seed_builtin_kbs(session: AsyncSession) -> int:
    """Upsert the canonical KBs under PLATFORM_TENANT_ID. Returns the
    count of rows written.

    Plan 06.10: si el BuiltinKB declara `category_slug`, resuelve el
    UUID de la categoría built-in via `uuid5` y lo persiste en
    `knowledge_bases.category_id`. Asume que `seed_builtin_kb_categories`
    se ejecutó antes (el runner del seed garantiza el orden).
    """
    from api_server.seeds.builtin_kb_categories import (
        kb_category_id_for_slug,
    )

    count = 0
    for kb in BUILTIN_KBS:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(kb.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": kb.name,
                "description": kb.description,
                "category_id": (
                    str(kb_category_id_for_slug(kb.category_slug)) if kb.category_slug else None
                ),
            },
        )
        count += 1
    return count


__all__ = [
    "BUILTIN_KBS",
    "BuiltinKB",
    "KB_SLUG_NAMESPACE",
    "kb_id_for_slug",
    "seed_builtin_kbs",
]
