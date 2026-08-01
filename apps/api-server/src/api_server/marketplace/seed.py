"""Official marketplace catalog seed (Plan 09.1 task_09_1_01).

Plan 09 shipped the marketplace substrate (sources / listings / installs /
trust) and a single featured tool loader (:func:`seed_playwright_listing` in
:mod:`api_server.marketplace.playwright`) — but that loader was never wired
into the seed runner, so the catalog booted EMPTY. The operator's report:
"the marketplace is empty and there is no explanation of how to add things."

This module is the **loader that fills the official catalog** so a fresh
install lands on a curated, non-empty marketplace. It follows
:func:`seed_playwright_listing` EXACTLY as the reference pattern:

  * every listing is VERIFIED + published under the ``official-catalog``
    source (:func:`~api_server.marketplace.playwright.ensure_official_source`)
    + GLOBAL (``tenant_id NULL`` — visible to every tenant via the Phase-A
    ``marketplace_listings_global_read`` RLS policy);
  * the loader is **idempotent** — it upserts each listing by its stable
    identity ``(source, tenant_id=NULL, name, version)`` (the same uniqueness
    ``uq_marketplace_listings_source_tenant_name_version`` enforces), so a
    re-seed refreshes metadata in place and never duplicates;
  * no migration — a listing is a row + a JSONB ``manifest``; the SKILL.md
    body + frontmatter are the seed *data*, the loader is the *code*.

The curated set:

  * the **Playwright** tool — reuses the existing
    :func:`seed_playwright_listing` helper verbatim (the flagship tool);
  * a handful of **SKILL** listings built from REAL content — the stack
    convention docs the platform already ships in ``seeds/catalog/*.md``
    (FastAPI, React/Next.js, PHP/Symfony, PostgreSQL, REST API design). Each
    is packaged as a ``SKILL.md`` manifest the shared
    :func:`~api_server.marketplace.skill_format.parse_skill_md` parser accepts
    (YAML frontmatter + Markdown body), and written under the on-disk
    official-catalog artifact root the
    :class:`~api_server.marketplace.install.LocalArtifactFetcher` reads — so a
    tenant can actually install one, not just browse it.

Like the Playwright loader, this MUST run on a BYPASSRLS publisher session
(writing a global ``tenant_id NULL`` row is reserved for the catalog
publisher; a tenant session's RLS WITH CHECK rejects it). The seed runner
(:mod:`api_server.seeds.__main__`) wires it under the admin engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.marketplace import (
    ListingReviewStatus,
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceTrustLevel,
)
from api_server.marketplace.install import default_artifact_root
from api_server.marketplace.playwright import (
    SeedResult,
    ensure_official_source,
    seed_playwright_listing,
)
from api_server.marketplace.skill_format import SkillManifest, parse_skill_md

# Author stamped on every official listing (mirrors the Playwright loader,
# which stamps ``author="Platform"``).
OFFICIAL_AUTHOR = "Platform"


# =============================================================================
# Curated SKILL listings — derived from the platform's own convention docs.
#
# Each SKILL.md is real, curated content (not filler): the YAML frontmatter
# carries the machine-readable metadata the parser validates, and the Markdown
# body is a concise convention guide distilled from ``seeds/catalog/*.md``.
# Skills declare NO permissions (they are narrative capability cues, not
# network/disk actors), so the most-restrictive default applies.
# =============================================================================
_FASTAPI_SKILL_MD = """\
---
name: python-fastapi-conventions
description: >-
  Convenciones de backend Python con FastAPI, SQLAlchemy 2.x async y pytest:
  layout por capas, routers REST, ORM async y pirámide de tests.
version: 1.0.0
examples:
  - title: Estructurar un servicio nuevo
    prompt: "Crea el esqueleto de un servicio FastAPI por capas (api/domain/db/services)."
  - title: Router REST
    prompt: "Diseña un router FastAPI con response_model y dependencias inyectadas."
---

# Convenciones de stack: Python + FastAPI

Guía práctica para servicios HTTP en Python con FastAPI, SQLAlchemy 2.x async y
pytest. Pensada como referencia para agentes que generan o revisan backend.

## Layout por capas

Estructura un servicio por capas, no por tipo de fichero: `api/` (routers,
dependencias, schemas), `domain/` (lógica de negocio pura, sin I/O), `db/`
(modelos SQLAlchemy, repositorios, sesión) y `services/` (casos de uso). El
dominio no importa FastAPI ni SQLAlchemy; los routers no contienen lógica de
negocio, delegan en services.

## Routers REST

Routers con prefijo claro, `response_model` explícito y dependencias
inyectadas para auth/sesión. Valida inputs con Pydantic v2 (`Field`,
`model_validator`). Devuelve 4xx para errores del cliente y 5xx solo para
fallos genuinos del servidor.

## ORM async

SQLAlchemy 2.x async con asyncpg: `AsyncSession`, `mapped_column`,
`Mapped[...]`, `select()` y `joinedload()`. Conoce las limitaciones de asyncpg
con `SET LOCAL` (usa `set_config`) y nunca devuelvas objetos ORM tras cerrar la
sesión.

## Tests

Pirámide de tests: muchos unit baratos, un puñado de integration que prueban
contratos entre componentes, y E2E mínimos sobre flujos críticos. Usa fixtures
sobre setup/teardown y parametriza casos de borde.
"""

_REACT_SKILL_MD = """\
---
name: react-nextjs-conventions
description: >-
  Convenciones de frontend con Next.js App Router, React, TanStack Query,
  Tailwind y shadcn/ui: Server vs Client Components, fetching y accesibilidad.
version: 1.0.0
examples:
  - title: Server vs Client Component
    prompt: "¿Cuándo marco un componente con 'use client' en el App Router?"
  - title: Fetching con TanStack Query
    prompt: "Define una query con queryKey estable y staleTime explícito."
---

# Convenciones de stack: React + Next.js

Guía para UI con Next.js 14 (App Router), React, TanStack Query, Tailwind y
shadcn/ui.

## Server vs Client Components

Server Components por defecto; `'use client'` solo cuando necesitas estado,
efectos o APIs del navegador. Haz el data fetching en el servidor cuando
puedas; `useEffect` solo para efectos puramente de cliente. Loading + error UIs
explícitos.

## Estado de servidor

Maneja el estado de servidor con TanStack Query: cada query con `queryKey`
estable, `staleTime` explícito y `refetchInterval` solo cuando hace falta
polling. Invalidaciones quirúrgicas (queryKey específica), no flushes globales.

## Estilos

Compón UI con clases utility de Tailwind. Para la repetición usa componentes y
`cn()` (clsx + tailwind-merge), no `@apply`. Compón sobre los primitivos de
shadcn/ui (Button, Dialog, Form, Table), copiados al repo.

## Accesibilidad

Cada control interactivo con nombre accesible y rol válido. Navegación completa
por teclado. Anuncia cambios dinámicos con `aria-live`. Contraste AA mínimo.
"""

_SYMFONY_SKILL_MD = """\
---
name: php-symfony-conventions
description: >-
  Convenciones de backend PHP con Symfony: arquitectura por capas, controllers
  finos, Doctrine, validación y tests con PHPUnit.
version: 1.0.0
examples:
  - title: Controller fino
    prompt: "Diseña un controller Symfony que delega en un service de aplicación."
---

# Convenciones de stack: PHP + Symfony

Guía para servicios PHP modernos con Symfony, Doctrine ORM y PHPUnit.

## Arquitectura

Separa controllers (HTTP), services de aplicación (casos de uso) y dominio
(entidades + lógica pura). Los controllers son finos: validan, delegan en un
service y devuelven la respuesta. Inyecta dependencias por constructor; evita
el service locator.

## Persistencia

Doctrine ORM con entidades anotadas/atributos, repositorios por agregado y
migraciones versionadas. No metas queries en los controllers; encapsúlalas en
repositorios. Usa transacciones explícitas para operaciones compuestas.

## Validación y tests

Valida con el componente Validator (constraints declarativas). Tests con
PHPUnit: unit para el dominio, functional para los endpoints con el
`WebTestCase`. Cada bug primero como test que lo reproduce.
"""

_POSTGRES_SKILL_MD = """\
---
name: postgresql-best-practices
description: >-
  Buenas prácticas de PostgreSQL: diseño de esquema, índices, migraciones
  reversibles, transacciones y rendimiento de consultas.
version: 1.0.0
examples:
  - title: Migración reversible
    prompt: "Escribe una migración que añade una columna NOT NULL sin downtime."
  - title: Índice adecuado
    prompt: "¿Qué índice necesita esta consulta con filtro + orden?"
---

# Buenas prácticas de PostgreSQL

Guía concisa para diseñar y operar esquemas PostgreSQL 16.

## Diseño de esquema

Tipos correctos (no todo es `text`): `timestamptz` para tiempo, `uuid` para
claves, `jsonb` para datos semiestructurados. Restricciones en la base de
datos (NOT NULL, FK, CHECK, UNIQUE), no solo en la aplicación.

## Índices

Indexa las columnas de filtro y join frecuentes; un índice compuesto en el
orden de las condiciones más selectivas. Índices parciales para subconjuntos
calientes (`WHERE deleted_at IS NULL`). No indexes todo: cada índice cuesta en
escrituras.

## Migraciones

Cada migración es reversible (upgrade + downgrade simétricos) y hace una sola
cosa coherente. Cambios con backfill en pasos separados: añadir columna
nullable, backfill, marcar NOT NULL. Evita bloqueos largos en tablas grandes.

## Transacciones y rendimiento

Transacciones cortas; nada de trabajo de red dentro de una transacción
abierta. `EXPLAIN (ANALYZE, BUFFERS)` antes de optimizar a ciegas. `VACUUM` /
autovacuum sano para evitar bloat.
"""

_REST_SKILL_MD = """\
---
name: api-rest-guidelines
description: >-
  Directrices de diseño de APIs REST: recursos y verbos, códigos de estado,
  paginación, versionado, idempotencia y errores consistentes.
version: 1.0.0
examples:
  - title: Modelar un recurso
    prompt: "Diseña los endpoints REST para gestionar 'pedidos'."
  - title: Respuesta de error
    prompt: "Define un cuerpo de error consistente para una API REST."
---

# Directrices de diseño de APIs REST

Guía para diseñar APIs HTTP REST claras, predecibles y evolucionables.

## Recursos y verbos

Modela recursos sustantivos en plural (`/orders`, `/orders/{id}`). Usa los
verbos HTTP por su semántica: GET (leer, seguro), POST (crear), PUT/PATCH
(actualizar total/parcial), DELETE (borrar). Las operaciones que no encajan en
CRUD son sub-recursos o acciones explícitas.

## Códigos de estado

200/201/204 para éxito según el caso; 4xx para errores del cliente (400, 401,
403, 404, 409, 422) y 5xx solo para fallos genuinos del servidor. Cuerpo de
error consistente: `code`, `message` y `details` opcionales.

## Paginación, versionado e idempotencia

Pagina las colecciones (`limit`/`offset` o cursor) con límites validados.
Versiona las APIs públicas (`/v1`) y mantén la versión anterior al menos un
ciclo. Operaciones de escritura idempotentes donde sea posible (claves de
idempotencia para POST sensibles).
"""


@dataclass(frozen=True, slots=True)
class _OfficialSkill:
    """One curated SKILL listing: its stable identity + its SKILL.md text.

    The ``name``/``version`` are the listing's stable identity (the upsert
    key, mirroring how the Playwright loader keys on name+version). The
    ``skill_md`` is the artifact the parser validates AND the bytes written
    to disk for the install fetcher.
    """

    skill_md: str

    @property
    def manifest(self) -> SkillManifest:
        return parse_skill_md(self.skill_md)


# The curated SKILL catalog. Each entry is validated through the shared
# SKILL.md parser at import-time-cheap parse (lazily, via .manifest) so a
# malformed seed fails loudly in the seed/test rather than silently shipping
# an un-installable listing.
_OFFICIAL_SKILLS: tuple[_OfficialSkill, ...] = (
    _OfficialSkill(_FASTAPI_SKILL_MD),
    _OfficialSkill(_REACT_SKILL_MD),
    _OfficialSkill(_SYMFONY_SKILL_MD),
    _OfficialSkill(_POSTGRES_SKILL_MD),
    _OfficialSkill(_REST_SKILL_MD),
)


@dataclass(frozen=True, slots=True)
class CatalogSeedResult:
    """The outcome of :func:`seed_marketplace_listings`.

    ``listing_ids`` is every official listing id after the seed (Playwright +
    skills); ``created`` counts the rows freshly inserted this run (0 on a
    re-seed of an already-full catalog — the idempotency signal).
    """

    listing_ids: tuple[UUID, ...]
    created: int

    @property
    def total(self) -> int:
        return len(self.listing_ids)


async def _seed_skill_listing(
    session: AsyncSession,
    *,
    source_id: UUID,
    skill: _OfficialSkill,
    artifact_root: str,
) -> SeedResult:
    """Upsert ONE SKILL listing + write its on-disk SKILL.md artifact.

    Idempotent, keyed by ``(source, tenant_id=NULL, name, version)`` — the
    exact pattern :func:`seed_playwright_listing` uses for the tool. On a
    re-seed it refreshes the manifest in place (``created=False``); otherwise
    it inserts (``created=True``). After the row exists, the SKILL.md is
    written under ``artifact_root/<listing.id>/SKILL.md`` so the
    :class:`LocalArtifactFetcher` install path can fetch + parse it.
    """
    manifest = skill.manifest
    name = manifest.name
    version = manifest.version
    listing_manifest = manifest.to_manifest_dict()
    requested_permissions = manifest.requested_permissions

    existing = await session.execute(
        select(MarketplaceListing).where(
            MarketplaceListing.source_id == source_id,
            MarketplaceListing.tenant_id.is_(None),
            MarketplaceListing.name == name,
            MarketplaceListing.version == version,
        )
    )
    listing = existing.scalar_one_or_none()
    if listing is not None:
        listing.kind = MarketplaceListingKind.SKILL.value
        listing.description = manifest.description
        listing.trust_level = MarketplaceTrustLevel.VERIFIED.value
        # ADR 0142 D6: el catálogo oficial nace revisado — lo cura la propia
        # plataforma, que es quien revisa. Explícito y no confiado al
        # `server_default` de la 0129, para que un cambio de ese default no
        # vacíe el catálogo en el siguiente re-seed sin que nadie lo note.
        listing.review_status = ListingReviewStatus.PUBLISHED.value
        listing.manifest = listing_manifest
        listing.requested_permissions = requested_permissions
        await session.flush()
        created = False
    else:
        listing = MarketplaceListing(
            source_id=source_id,
            tenant_id=None,  # GLOBAL catalog listing (Phase A hybrid model).
            kind=MarketplaceListingKind.SKILL.value,
            name=name,
            version=version,
            description=manifest.description,
            author=OFFICIAL_AUTHOR,
            trust_level=MarketplaceTrustLevel.VERIFIED.value,
            review_status=ListingReviewStatus.PUBLISHED.value,
            manifest=listing_manifest,
            requested_permissions=requested_permissions,
        )
        session.add(listing)
        await session.flush()
        created = True

    _write_skill_artifact(artifact_root, listing.id, skill.skill_md)
    return SeedResult(listing_id=listing.id, created=created)


def _write_skill_artifact(artifact_root: str, listing_id: UUID, skill_md: str) -> None:
    """Write the SKILL.md under ``artifact_root/<listing_id>/SKILL.md``.

    Idempotent: re-running overwrites the same path with identical bytes. The
    layout is exactly what :class:`LocalArtifactFetcher` reads (one directory
    per listing id, the manifest file inside).
    """
    listing_dir = Path(artifact_root) / str(listing_id)
    listing_dir.mkdir(parents=True, exist_ok=True)
    (listing_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")


async def seed_marketplace_listings(
    session: AsyncSession,
    *,
    artifact_root: str | None = None,
) -> CatalogSeedResult:
    """Seed the official marketplace catalog (idempotent).

    Publishes the curated VERIFIED + GLOBAL listings under the
    ``official-catalog`` source: the Playwright tool (via the existing
    :func:`seed_playwright_listing`) + the SKILL listings derived from the
    platform's convention docs. Re-running NEVER duplicates — each listing is
    upserted by its stable ``(source, tenant_id=NULL, name, version)``
    identity.

    ``artifact_root`` is where the SKILL.md artifacts are written for the
    install fetcher; defaults to
    :func:`~api_server.marketplace.install.default_artifact_root` (overridable
    via ``MARKETPLACE_ARTIFACT_ROOT``). Must run on a BYPASSRLS publisher
    session (global ``tenant_id NULL`` writes are reserved for the catalog
    publisher).
    """
    root = artifact_root if artifact_root is not None else default_artifact_root()
    source = await ensure_official_source(session)

    listing_ids: list[UUID] = []
    created = 0

    # The flagship tool — reuse the existing loader verbatim.
    playwright = await seed_playwright_listing(session)
    listing_ids.append(playwright.listing_id)
    created += int(playwright.created)

    # The curated SKILL listings.
    for skill in _OFFICIAL_SKILLS:
        result = await _seed_skill_listing(
            session, source_id=source.id, skill=skill, artifact_root=root
        )
        listing_ids.append(result.listing_id)
        created += int(result.created)

    return CatalogSeedResult(listing_ids=tuple(listing_ids), created=created)


__all__ = [
    "CatalogSeedResult",
    "OFFICIAL_AUTHOR",
    "seed_marketplace_listings",
]
