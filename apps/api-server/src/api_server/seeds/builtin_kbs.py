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
    ),
    BuiltinKB(
        slug="node-express-conventions",
        name="Node + Express conventions",
        description=(
            "Convenciones para APIs Node.js con Express: estructura de "
            "carpetas, middleware, validación con zod, testing con vitest, "
            "y patrones de conexión a Postgres con pg/Prisma."
        ),
    ),
    BuiltinKB(
        slug="php-symfony-conventions",
        name="PHP + Symfony conventions",
        description=(
            "Convenciones de Symfony 6/7: controladores, autowiring, "
            "Doctrine, tests con phpunit, y migrations con doctrine-migrations."
        ),
    ),
    BuiltinKB(
        slug="postgresql-best-practices",
        name="PostgreSQL best practices",
        description=(
            "Buenas prácticas con PostgreSQL: diseño de esquema, índices, "
            "JSONB, row-level security, conexiones, vacuum/autovacuum, "
            "y migraciones reversibles."
        ),
    ),
    BuiltinKB(
        slug="api-rest-guidelines",
        name="API REST design guidelines",
        description=(
            "Principios de diseño de APIs REST: recursos, verbos HTTP, "
            "códigos de estado, versionado, paginación, filtrado, HATEOAS, "
            "y autenticación con OAuth 2 / JWT. Agnóstico de stack."
        ),
    ),
    BuiltinKB(
        slug="react-nextjs-conventions",
        name="React + Next.js conventions",
        description=(
            "Convenciones para frontend con Next.js 14 + React: app router, "
            "server components, TanStack Query, formularios con react-hook-form, "
            "y testing con vitest + Playwright."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
_UPSERT_SQL = text(
    """
    INSERT INTO knowledge_bases (id, tenant_id, name, description, embedding_model_id)
    VALUES (:id, :tenant_id, :name, :description, 'nomic-embed-text-v1.5')
    ON CONFLICT (id) DO UPDATE SET
        name = EXCLUDED.name,
        description = EXCLUDED.description,
        updated_at = now(),
        deleted_at = NULL
    """
)


async def seed_builtin_kbs(session: AsyncSession) -> int:
    """Upsert the canonical KBs under PLATFORM_TENANT_ID. Returns the
    count of rows written."""
    count = 0
    for kb in BUILTIN_KBS:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(kb.id),
                "tenant_id": str(PLATFORM_TENANT_ID),
                "name": kb.name,
                "description": kb.description,
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
