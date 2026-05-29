"""Built-in KB categories (Plan 06.10 task_06_10_05).

5 categorías default que la plataforma siembra bajo `tenant_id IS
NULL` (built-in scope). Visibles a todos los tenants via la policy
`kb_categories_builtin_read` definida en la migration 0028.

Cualquier tenant puede crear sus propias categorías custom (con
`tenant_id` propio) desde el endpoint POST /kb-categories — éstas no
sobreescriben las built-ins, sólo añaden.

Slugs estables (ASCII kebab-case). UUID derivado deterministicamente
via `uuid5(KB_CATEGORY_NAMESPACE, slug)` para que re-seedear NO
rompa los `knowledge_bases.category_id` que apuntaban a una built-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Namespace para uuid5("kb_category:<slug>"). Separado de los otros
# (BUILTIN_KBS usa 0015) por seguridad.
KB_CATEGORY_NAMESPACE: UUID = UUID("00000000-0000-0000-0000-000000000016")


def kb_category_id_for_slug(slug: str) -> UUID:
    """UUID determinista de una categoría built-in dado su slug."""
    return uuid5(KB_CATEGORY_NAMESPACE, f"builtin_kb_category:{slug}")


@dataclass(frozen=True)
class BuiltinKbCategory:
    slug: str
    name: str
    color: str  # hex con `#`

    @property
    def id(self) -> UUID:
        return kb_category_id_for_slug(self.slug)


# 5 categorías default. Cubren el 80 % de los casos; el tenant añade
# las suyas (compliance específico, cliente externo, etc.) custom.
BUILTIN_KB_CATEGORIES: tuple[BuiltinKbCategory, ...] = (
    BuiltinKbCategory(
        slug="stack",
        name="Stack",
        color="#3b82f6",  # azul
    ),
    BuiltinKbCategory(
        slug="role",
        name="Rol",
        color="#10b981",  # verde
    ),
    BuiltinKbCategory(
        slug="compliance",
        name="Compliance",
        color="#f59e0b",  # ámbar
    ),
    BuiltinKbCategory(
        slug="architecture",
        name="Arquitectura",
        color="#a855f7",  # violeta
    ),
    BuiltinKbCategory(
        slug="process",
        name="Proceso",
        color="#6b7280",  # gris
    ),
)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------
# Plan 06.12 (ADR 0029): patrón (A) — built-ins bajo el platform tenant
# con is_builtin=true (antes patrón B: tenant_id NULL).
_UPSERT_SQL = text(
    """
    INSERT INTO kb_categories (id, tenant_id, slug, name, color, is_builtin)
    VALUES (:id, :platform_tenant_id, :slug, :name, :color, true)
    ON CONFLICT (id) DO UPDATE SET
        tenant_id = EXCLUDED.tenant_id,
        name = EXCLUDED.name,
        color = EXCLUDED.color,
        is_builtin = true,
        updated_at = now(),
        deleted_at = NULL
    """
)


async def seed_builtin_kb_categories(session: AsyncSession) -> int:
    """Upsert las 5 built-in. Idempotente. Returns count."""
    from api_server.seeds import PLATFORM_TENANT_ID

    count = 0
    for cat in BUILTIN_KB_CATEGORIES:
        await session.execute(
            _UPSERT_SQL,
            {
                "id": str(cat.id),
                "platform_tenant_id": str(PLATFORM_TENANT_ID),
                "slug": cat.slug,
                "name": cat.name,
                "color": cat.color,
            },
        )
        count += 1
    return count


__all__ = [
    "BUILTIN_KB_CATEGORIES",
    "BuiltinKbCategory",
    "KB_CATEGORY_NAMESPACE",
    "kb_category_id_for_slug",
    "seed_builtin_kb_categories",
]
