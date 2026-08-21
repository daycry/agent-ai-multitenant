"""`/marketplace` endpoints — browse the catalog, install, uninstall (Plan 09 task_09_03).

The marketplace REST surface. Two routers:

  - ``router``        the tenant surface under ``/marketplace``, on
                      :func:`get_tenant_session` (PostgreSQL RLS scopes every
                      query).
  - ``admin_router``  the System-Admin surface under ``/admin/marketplace``, on
                      the BYPASSRLS admin session — the review queue and the
                      cross-tenant share audit need to see EVERY tenant.

Uninstall vs. revoke (task_09_08): both flip the install to ``revoked``,
disable it for agents/projects (it is no longer "live" — the partial-unique
live index frees up), soft-delete the row, and ALWAYS write a marketplace
audit entry. They differ only in INTENT / audit action: ``DELETE`` is the
operator-driven teardown (``uninstall`` action), while ``POST .../revoke`` is
the explicit security revocation (``revoke`` action) — e.g. a community tool
flagged after install. The shared teardown lives in
:func:`api_server.routers.marketplace.common._revoke_installation`; the audit is
mandatory and append-only (the ``marketplace_audit_entries`` table enforces
no-update/no-delete via RLS, migration 0043), so an audit row can never be
silently dropped.

Tenancy: every tenant route runs on :func:`get_tenant_session`, so PostgreSQL
RLS scopes the queries. Browsing a listing exposes the GLOBAL catalog
(``tenant_id IS NULL``, via the ``marketplace_listings_global_read``
SELECT policy) plus the caller's own private listings; an installation /
audit row is strictly tenant-owned, so tenant A can never list, install
over, or revoke tenant B's rows.

RBAC: browsing is any tenant member (:func:`require_tenant_member`);
install / uninstall are tenant-admin writes (:func:`require_tenant_admin`).
This repo has no per-membership ``project_owner`` role — project-scoped
writes are gated to ``tenant_admin`` exactly like ``/projects`` and the
skills/tools routers, so we reuse that helper. The per-permission consent
flow (``api_server.marketplace.consent``) and the signature / trust /
sandbox gates de las Fases B-C están CABLEADOS en el install/update de este
router (N-17, auditoría 2026-07-17: este docstring decía que eran futuros).

## Por qué esto es un paquete (plan prod-16, `task_prod16_12`)

Era un solo `routers/marketplace.py` de **1852 líneas** —1380 cuando se escribió
el plan: creció 472 mientras esperaba, que es el argumento del hallazgo
quality-7— con siete responsabilidades dentro. Repartido:

  * :mod:`.common`        — carga con comprobación de visibilidad, el desmontaje
    compartido de uninstall/revoke y la dependencia del orquestador de
    instalación. **No publica ni una ruta.**
  * :mod:`.catalog`       — navegar el catálogo (listado y detalle).
  * :mod:`.private`       — publicar / editar / retirar el catálogo privado.
  * :mod:`.shares`        — compartir un listing privado con otro tenant.
  * :mod:`.installations` — instalar, desinstalar, revocar y listar.
  * :mod:`.consent`       — el consentimiento por permiso de una instalación.
  * :mod:`.updates`       — comprobar y aplicar la actualización de un install.
  * :mod:`.admin`         — las seis rutas de System Admin. **Es el único dueño
    de `admin_router`.**

**Por qué las rutas de admin viven en un módulo aparte, y no junto a la función
hermana de tenant.** `main._is_admin_surface` decide si un router lleva
`require_hardened_system_admin` mirando si TODOS sus caminos cuelgan de
`/admin`, y **lanza al importar** ante un router que mezcle. El caso concreto:
`GET /admin/marketplace/shares` es la vista cross-tenant de lo mismo que sirve
:mod:`.shares`, y ponerlas juntas invitaba a colgarla del router equivocado.
Separadas, la partición se ve en el árbol de ficheros.

**El orden de montaje NO decide nada aquí, y está comprobado.** En
`routers/agents` sí: `GET /agents/provider-options` solapa con
`GET /agents/{agent_id}` y FastAPI casa por orden de registro, así que repartir
las rutas entre módulos podía hacer desaparecer una en silencio. En este router
ningún par de rutas comparte método y forma —
`tests/unit/test_marketplace_router_package.py::
test_no_route_pair_depends_on_the_registration_order` lo afirma en vez de
suponerlo, y se pondrá rojo el día que alguien añada la ruta literal que hoy no
existe.

**Y `route_paths`, no `router.routes` a pelo**: desde FastAPI 0.141
`include_router` ya no aplana, así que un router compuesto como éste presenta
`_IncludedRouter` sin `.path`. Ver :mod:`api_server.routing_introspection`.
"""

from __future__ import annotations

from fastapi import APIRouter

from api_server.routers.marketplace import (
    admin,
    catalog,
    common,
    consent,
    installations,
    private,
    shares,
    updates,
)
from api_server.routers.marketplace.admin import admin_router
from api_server.routers.marketplace.common import (
    get_install_orchestrator,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])
# El orden replica el del monolito. Ninguna pareja de rutas se resuelve por
# orden (hay un test que lo afirma), así que esto es legibilidad, no contrato.
router.include_router(catalog.router)
router.include_router(private.router)
router.include_router(shares.router)
router.include_router(installations.router)
router.include_router(consent.router)
router.include_router(updates.router)

__all__ = [
    "admin",
    "admin_router",
    "catalog",
    "common",
    "consent",
    "get_install_orchestrator",
    "installations",
    "private",
    "router",
    "shares",
    "updates",
]
