"""`/auth/sso/*` endpoints — GLOBAL OIDC / SAML login (ADR 0047).

Auth providers are **platform-global** (ADR 0047, supersedes the
per-tenant part of ADR 0031): one OIDC + one SAML config for the whole
platform, configured by ``system_admin``, serving every tenant. Login is
keyed by the global **provider id**, never a tenant; the old per-tenant
``/auth/sso/{tenant_id}/…`` login routes are RETIRED (no redirect).

SSO is **added alongside** the existing email+password login
(``routers/auth.py``); it does not replace or touch it. A successful
OIDC callback / SAML ACS issues a session EXACTLY like local login — a
server-side Redis session (:class:`SessionStore`) plus a JWT
(:func:`encode_jwt`) — so logout/revocation and every downstream
`get_principal` check behave identically regardless of how the user
authenticated. There is no stateless-JWT-after-SSO path.

The issued session proves **identity** only — a GLOBAL user WITHOUT an
active tenant (``tenant_id = None``, exactly like the password
pre-tenant session). Tenant access is granted by
``UserOrganizationMembership`` that the admin assigns AFTER login; the
post-login resolution (0 → "no access" screen, 1 → enter, >1 → picker)
is task_sso_03.

Endpoints:

  * ``GET /auth/sso/providers`` — PUBLIC: the enabled global providers
    (id / kind / display_name / button_label / login_url) for the login
    page. No secrets.
  * ``GET /auth/sso/{provider_id}/oidc/login`` — resolve THAT global OIDC
    provider, mint ``state`` + ``nonce`` (the state carries the
    provider), store them server-side, 307-redirect to the IdP.
  * ``GET /auth/sso/oidc/callback`` — validate ``state`` (single-use,
    from Redis) → recover the provider, exchange the ``code``, verify the
    ID token (signature + iss/aud/nonce), fetch userinfo, provision the
    global identity, then mint the identity session + JWT.
  * ``GET /auth/sso/{provider_id}/saml/login`` + the GLOBAL
    ``POST /auth/sso/saml/acs`` — the SAML analogue; the RelayState
    carries the provider for the SP-initiated leg.

The provider reads run on the BYPASSRLS admin engine: the global
``sso_configurations`` table has no RLS / ``tenant_id`` (ADR 0047), so a
provider is resolved by its global id, never by a tenant.

## Por qué esto es un paquete (plan prod-16, `task_prod16_10`)

Era un solo `routers/sso.py` de **1654 líneas** que mezclaba dos protocolos
completos, su CRUD y dos ajustes de plataforma. Repartido:

  * :mod:`.common` — lo que OIDC y SAML comparten. **No tiene rutas.**
  * :mod:`.discovery` — lo que la página de login pregunta ANTES de saber qué
    protocolo toca (`/discover`, `/providers`) y los ajustes de base URL.
  * :mod:`.oidc` — login, callback, plantillas y CRUD de configs OIDC.
  * :mod:`.saml` — login, ACS, metadatos y CRUD de configs SAML.

El montaje de abajo es lo que hace que las rutas sean **las mismas**: los
sub-routers no llevan prefijo propio y cuelgan del `/auth/sso` de siempre.

**Ojo con el orden de montaje**, que es el único riesgo real de un troceo así:
FastAPI casa las rutas por orden de registro, así que mover un `@router.get`
de sitio puede hacer que una ruta paramétrica se coma a una literal. Aquí no
pasa —ninguna ruta de una sola parte es paramétrica, y las dos paramétricas
(`/{provider_id}/oidc/login`, `/{provider_id}/saml/login`) tienen tres partes
con literales distintos en la segunda— y el contrato lo fija
``tests/integration/test_sso_router_package.py``, que compara el conjunto de
rutas efectivo con el que había antes de partir.

**Y ojo con `route_paths`**: desde FastAPI 0.141 `include_router` ya no aplana,
así que un router compuesto de sub-routers como éste presenta `_IncludedRouter`
sin `.path`. Cualquier introspección sobre él —empezando por
``main._is_admin_surface``, que decide si un router lleva la guarda de System
Admin— tiene que usar :func:`api_server.routing_introspection.route_paths` y no
`router.routes` a pelo. Este paquete es el primero del repo que anida
sub-routers, o sea el primero que arma esa trampa de verdad.
"""

from __future__ import annotations

from fastapi import APIRouter

from api_server.routers.sso import common, discovery, oidc, saml
from api_server.routers.sso.common import (
    InvalidLandingOriginError,
    get_oidc_flow,
    get_oidc_http_client,
    get_oidc_state_store,
    get_saml_relay_state_store,
    sso_landing_url,
)
from api_server.routers.sso.oidc import _load_enabled_oidc_config
from api_server.routers.sso.saml import _load_enabled_saml_config

# Login discovery lives at `/auth/discover` (NOT under `/auth/sso/*`) — it
# is the entry point the login UI hits BEFORE it knows whether SSO applies,
# so it sits one level up alongside the local-login endpoints.
discovery_router = discovery.discovery_router

router = APIRouter(prefix="/auth/sso", tags=["sso"])
# El orden importa (ver el docstring): primero lo neutro, luego cada protocolo.
router.include_router(discovery.router)
router.include_router(oidc.router)
router.include_router(saml.router)

__all__ = [
    "InvalidLandingOriginError",
    "_load_enabled_oidc_config",
    "_load_enabled_saml_config",
    "common",
    "discovery",
    "discovery_router",
    "get_oidc_flow",
    "get_oidc_http_client",
    "get_oidc_state_store",
    "get_saml_relay_state_store",
    "oidc",
    "router",
    "saml",
    "sso_landing_url",
]
