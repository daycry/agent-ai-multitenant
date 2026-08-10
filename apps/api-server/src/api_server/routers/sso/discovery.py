"""Lo que la PÁGINA DE LOGIN necesita antes de saber qué protocolo toca.

`GET /auth/discover` (mapea el dominio del email al proveedor SSO que lo
reclama) y `GET /auth/sso/providers` (los proveedores habilitados, con su botón
y su URL de login), más los dos ajustes de plataforma de los que dependen las
URLs que se publican al IdP: `public-base-url` y `api-path-prefix`.

No son de OIDC ni de SAML: sirven a los dos y se consultan ANTES de elegir. Por
eso no viven en `oidc.py` ni en `saml.py` — meterlos en cualquiera de los dos
haría que el otro tuviera que importarlo, que es exactamente cómo el router
mixto llegó a 1654 líneas.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    require_system_admin,
)
from api_server.config import get_settings
from api_server.db.models import (
    SSOConfiguration,
    SSOProvider,
    User,
)
from api_server.db.platform_settings import (
    InvalidApiPathPrefixError,
    InvalidPublicBaseUrlError,
    get_api_path_prefix_override,
    get_app_public_base_url_override,
    set_api_path_prefix,
    set_app_public_base_url,
    validate_api_path_prefix,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.routers.sso.common import (
    _OIDC_LOGIN_PATH_TEMPLATE,
    _SAML_LOGIN_PATH_TEMPLATE,
    _load_enabled_providers,
)
from api_server.schemas.sso import (
    LOGIN_METHOD_PASSWORD,
    LOGIN_METHOD_SSO,
    ApiPathPrefixResponse,
    ApiPathPrefixUpdate,
    LoginDiscoveryResponse,
    PublicBaseUrlResponse,
    PublicBaseUrlUpdate,
    PublicProviderResponse,
)

router = APIRouter(tags=["sso"])

# Login discovery lives at `/auth/discover` (NOT under `/auth/sso/*`) — it
# is the entry point the login UI hits BEFORE it knows whether SSO applies,
# so it sits one level up alongside the local-login endpoints.
discovery_router = APIRouter(prefix="/auth", tags=["sso"])


# ===========================================================================
# Login discovery (Plan 08 task_08_12) — PUBLIC GET /auth/discover?email=...
#
# Maps an email's DOMAIN to the tenant whose enabled SSO config claims it.
# The lookup runs ONCE on the BYPASSRLS admin role (the request is
# unauthenticated and tenant-agnostic — we don't yet know which tenant the
# email belongs to), filtering PURELY on the operator-attested
# `email_domains` of enabled, non-deleted configs. It NEVER touches the
# `users` table, so it cannot reveal whether a specific account exists:
# the response shape is identical whether or not a user with that email
# is registered. The only thing it discloses is whether the *domain* is
# configured for SSO — which is a deliberate, operator-published fact.
# ===========================================================================
def _extract_email_domain(email: str) -> str | None:
    """Return the lower-cased domain of ``email``, or None if malformed.

    Deliberately lenient (this is a routing hint, not validation): we only
    need the part after a single ``@`` to match against configured
    domains. A value with zero or multiple ``@`` yields None → the generic
    local-login answer (never an error that could be probed).
    """
    parts = email.strip().lower().split("@")
    if len(parts) != 2:
        return None
    domain = parts[1]
    return domain or None


async def _resolve_sso_config_for_domain(domain: str) -> SSOConfiguration | None:
    """Find the oldest enabled SSO config claiming ``domain`` (any tenant).

    Runs on the BYPASSRLS admin role so the cross-tenant scan can see every
    tenant's configs (the caller is unauthenticated). Matching is
    case-insensitive — the stored domains are normalised to lower-case on
    write and ``domain`` is lower-cased by the caller. Multi-tenant-domain
    collisions (two tenants attesting the same domain) resolve
    deterministically to the oldest-created config; the result reveals only
    the configured provider, never that more than one tenant matched.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration)
            .where(
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
                # JSONB containment: the array column holds `domain`. The
                # stored values are lower-case (normalised on write) and
                # `domain` is lower-cased by the caller, so this is the
                # case-insensitive match.
                SSOConfiguration.email_domains.contains([domain]),
            )
            .order_by(SSOConfiguration.created_at)
            .limit(1)
        )
        return result.scalar_one_or_none()


@discovery_router.get("/discover", response_model=LoginDiscoveryResponse)
async def discover_login(
    email: str = Query(..., min_length=1, max_length=320),
) -> LoginDiscoveryResponse:
    """Public: given an email, say which login method to use.

    If the email's DOMAIN is claimed by an enabled SSO config, return that
    provider (``oidc`` / ``saml``) + the tenant id + the relative login URL
    to start the flow. Otherwise return the generic local-login response.

    NO user enumeration: the answer is derived solely from the configured
    SSO domain — the users table is never queried — so the response is
    byte-for-byte identical whether or not an account with that email
    exists. A malformed email (or an unconfigured domain) gets the same
    generic local-login answer, never an error.
    """
    domain = _extract_email_domain(email)
    config = await _resolve_sso_config_for_domain(domain) if domain else None
    if config is None:
        # No SSO domain match — use local (email + password) login. Same
        # shape regardless of whether the account exists.
        return LoginDiscoveryResponse(method=LOGIN_METHOD_PASSWORD)

    provider = config.provider
    if provider == SSOProvider.OIDC.value:
        login_url = _OIDC_LOGIN_PATH_TEMPLATE.format(provider_id=config.id)
    elif provider == SSOProvider.SAML.value:
        login_url = _SAML_LOGIN_PATH_TEMPLATE.format(provider_id=config.id)
    else:  # pragma: no cover - provider column is constrained to oidc/saml
        # Unknown provider value — fail safe to local login rather than
        # emit a half-formed SSO answer.
        return LoginDiscoveryResponse(method=LOGIN_METHOD_PASSWORD)

    # tenant_id is no longer part of the SSO answer (ADR 0047): the
    # provider is platform-global and tenant access is resolved by
    # membership after login.
    return LoginDiscoveryResponse(
        method=LOGIN_METHOD_SSO,
        provider=provider,
        login_url=login_url,
    )


# ===========================================================================
# PUBLIC providers list (ADR 0047 task_sso_02) — GET /auth/sso/providers
#
# Unauthenticated. Lists the enabled GLOBAL providers so the /login page
# can render a branded button + the relative URL that starts each flow.
# Exposes NO secret: only id / kind / display_name / button_label /
# login_url cross this boundary (the response model has no secret field).
# ===========================================================================
@router.get("/providers", response_model=list[PublicProviderResponse])
async def list_public_providers() -> list[PublicProviderResponse]:
    """Public: the enabled global auth providers for the login page.

    No tenant, no auth, no secrets. Each entry carries the relative
    ``login_url`` (``/auth/sso/{id}/oidc|saml/login``) the browser hits to
    start the flow. A provider whose kind is neither ``oidc`` nor ``saml``
    is skipped defensively (the column is constrained to those two).
    """
    rows = await _load_enabled_providers()
    out: list[PublicProviderResponse] = []
    for row in rows:
        if row.provider == SSOProvider.OIDC.value:
            login_url = _OIDC_LOGIN_PATH_TEMPLATE.format(provider_id=row.id)
        elif row.provider == SSOProvider.SAML.value:
            login_url = _SAML_LOGIN_PATH_TEMPLATE.format(provider_id=row.id)
        else:  # pragma: no cover - provider column is constrained to oidc/saml
            continue
        out.append(
            PublicProviderResponse(
                id=row.id,
                kind=row.provider,
                display_name=row.display_name,
                button_label=row.button_label,
                login_url=login_url,
            )
        )
    return out


@router.get("/public-base-url", response_model=PublicBaseUrlResponse)
async def get_public_base_url(
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> PublicBaseUrlResponse:
    """The public application base URL + how it is currently sourced (ADR 0047).

    ``base_url`` is the EFFECTIVE value (the System-Admin override if set, else
    the env bootstrap default). ``is_override`` says whether it came from the
    DB override; ``env_default`` exposes the bootstrap so the UI can warn when
    the effective value is still the (localhost) default. The callback / ACS
    URLs are PATHS under this base — the operator registers those at the IdP.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        override = await get_app_public_base_url_override(session)
    env_default = get_settings().sso_redirect_base_url.rstrip("/")
    return PublicBaseUrlResponse(
        base_url=(override or env_default).rstrip("/"),
        is_override=override is not None,
        env_default=env_default,
    )


@router.put("/public-base-url", response_model=PublicBaseUrlResponse)
async def put_public_base_url(
    payload: PublicBaseUrlUpdate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> PublicBaseUrlResponse:
    """Set the public application base URL override (System Admin only).

    Validated + normalised (a bare ``scheme://host[:port]`` origin, no path) —
    a bad value is a 422, never persisted. Stored in ``platform_settings`` so it
    takes effect live (the next SSO redirect reads it) without a restart.
    """
    actor = await session.get(User, principal.user_id)
    if actor is None:  # pragma: no cover - token validated upstream
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user no longer exists"
        )
    try:
        stored = await set_app_public_base_url(session, payload.base_url, actor=actor)
    except InvalidPublicBaseUrlError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    env_default = get_settings().sso_redirect_base_url.rstrip("/")
    return PublicBaseUrlResponse(base_url=stored, is_override=True, env_default=env_default)


@router.get("/api-path-prefix", response_model=ApiPathPrefixResponse)
async def get_api_path_prefix_endpoint(
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> ApiPathPrefixResponse:
    """The effective API path prefix + how it is sourced (ADR 0069).

    The PATH segment under which the API is published behind a single-origin
    reverse proxy (e.g. ``/api``), inserted between the public origin and the
    SSO/SCIM paths. ``""`` = no prefix (api-server at the origin root)."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        override = await get_api_path_prefix_override(session)
    env_default = validate_api_path_prefix(get_settings().api_path_prefix)
    return ApiPathPrefixResponse(
        prefix=override if override is not None else env_default,
        is_override=override is not None,
        env_default=env_default,
    )


@router.put("/api-path-prefix", response_model=ApiPathPrefixResponse)
async def put_api_path_prefix(
    payload: ApiPathPrefixUpdate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> ApiPathPrefixResponse:
    """Set the API path prefix override (System Admin only). Validated +
    normalised (``""`` or a bare absolute path); a bad value is a 422. Stored in
    ``platform_settings`` so it takes effect live for the next SSO redirect."""
    actor = await session.get(User, principal.user_id)
    if actor is None:  # pragma: no cover - token validated upstream
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="user no longer exists"
        )
    try:
        stored = await set_api_path_prefix(session, payload.prefix, actor=actor)
    except InvalidApiPathPrefixError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    env_default = validate_api_path_prefix(get_settings().api_path_prefix)
    return ApiPathPrefixResponse(prefix=stored, is_override=True, env_default=env_default)
