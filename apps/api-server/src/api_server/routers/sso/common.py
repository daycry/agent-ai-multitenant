"""Piezas que OIDC y SAML comparten (plan prod-16, `task_prod16_10`).

Aquí NO hay rutas: sólo lo que los dos protocolos usan y que, si viviera en uno
de ellos, obligaría al otro a importarlo — el sesgo que convertía a `sso.py` en
1654 líneas mixtas. En concreto: la URL de aterrizaje en el panel y el redirect
que cierra el login, los proveedores inyectables (cliente HTTP, flujo OIDC,
almacenes de estado), la resolución del base URL efectivo, la carga de un
proveedor global por id y el aprovisionamiento JIT de la identidad.

La dirección de las dependencias es estricta y a propósito: `oidc`, `saml` y
`discovery` importan de aquí; este módulo NO importa de ninguno de ellos. Un
ciclo entre ellos volvería a fundir los tres.
"""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from uuid6 import uuid7

from api_server.auth.cookies import issue_session_cookies
from api_server.auth.deps import (
    get_session_store,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.sessions import SessionStore
from api_server.auth.sso.oidc import OIDCFlow
from api_server.auth.sso.state_store import (
    OIDCStateStore,
    SAMLRelayStateStore,
)
from api_server.config import get_settings
from api_server.db.models import (
    SSOConfiguration,
    User,
)
from api_server.db.platform_settings import (
    InvalidApiPathPrefixError,
    get_api_path_prefix_override,
    get_app_public_base_url_override,
    validate_api_path_prefix,
)
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.schemas.auth import LoginResponse

# `client_secret_source` discriminator values for SSOConfigResponse — the
# UI shows "secret set" without ever seeing the value.
_SECRET_SOURCE_VAULT = "vault"


_SECRET_SOURCE_ENCRYPTED = "encrypted"


# JIT-provisioned users have no usable password — they authenticate only
# through the IdP. A sentinel hash that no plaintext can produce keeps
# the NOT NULL column satisfied while making local login impossible for
# them (verify_password against this never matches).
_SSO_PASSWORD_SENTINEL = "!sso-no-local-login!"  # - not a real secret


# OIDC callback path appended to `sso_redirect_base_url`. Must match the
# IdP's registered redirect-URI allowlist.
_CALLBACK_PATH = "/auth/sso/oidc/callback"


# The SP's own SAML EntityID (the value the IdP knows this SP by) — a
# stable URN derived from the public base URL.
_SP_ENTITY_PATH = "/auth/sso/saml/metadata"


# GLOBAL ACS path (ADR 0047): auth providers are platform-global, so there
# is ONE SP identity (entityID + ACS) for the whole platform. An
# IdP-initiated (unsolicited) Response reaches the single enabled global
# SAML config; an SP-initiated one correlates on the RelayState we minted.
_SAML_ACS_PATH = "/auth/sso/saml/acs"


# The login-flow entry points login discovery + the public providers list
# point the UI at. Auth providers are platform-global (ADR 0047), so the
# path is keyed by the global provider id, not a tenant; these match the
# `oidc_login` / `saml_login` routes below.
_OIDC_LOGIN_PATH_TEMPLATE = "/auth/sso/{provider_id}/oidc/login"


_SAML_LOGIN_PATH_TEMPLATE = "/auth/sso/{provider_id}/saml/login"


# ---------------------------------------------------------------------------
# Landing in the PANEL after SSO (prod-09 task_prod09_09, frontend-1)
# ---------------------------------------------------------------------------
# Where the callback / ACS send the browser once the session cookie is set. The
# panel page resolves the tenant (`resolveAndRoute`) and routes on.
#
# NOTE the topology (ADR 0061/0069): the PANEL sits at the ORIGIN ROOT and the
# api-server under `api_path_prefix` (`/api`). So the landing origin is the
# public base URL WITHOUT the API prefix — using `_effective_redirect_base()`
# here would send the user to `https://host/api/auth/callback`, which the panel
# does not serve.
_PANEL_CALLBACK_PATH = "/auth/callback"


class InvalidLandingOriginError(ValueError):
    """The configured public base URL cannot be turned into a safe redirect."""


def sso_landing_url(origin: str) -> str:
    """``{origin}/auth/callback``, or raise if ``origin`` is not a plain origin.

    The value comes from a System-Admin-writable platform setting
    (``app.public_base_url``) with an env fallback, i.e. it is CONFIGURATION —
    never a request parameter — so this is not an open-redirect gate in the
    usual sense. It is still worth having: a redirect built from a mistyped or
    tampered setting is how an open redirect (``//evil.example``), a phishing
    authority (``https://good@evil.example``) or a header injection
    (``\\r\\nSet-Cookie:``) would enter, and the failure is silent — the browser
    just goes somewhere else with a fresh session cookie in hand.

    Deliberately NOT ``urlparse``-only: ``urlparse("//evil.example")`` yields an
    empty scheme and a netloc, which reads as "relative" and is exactly the case
    that must be refused.
    """
    candidate = origin.strip()
    if not candidate:
        raise InvalidLandingOriginError("public base URL is empty")
    if any(ch in candidate for ch in ("\r", "\n", "\t")):
        raise InvalidLandingOriginError("public base URL contains control characters")
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        raise InvalidLandingOriginError(f"unsupported scheme: {parsed.scheme!r}")
    if not parsed.netloc:
        raise InvalidLandingOriginError("public base URL has no host")
    if "@" in parsed.netloc:
        raise InvalidLandingOriginError("public base URL must not carry credentials")
    return f"{candidate.rstrip('/')}{_PANEL_CALLBACK_PATH}"


async def _effective_panel_origin() -> str:
    """The PANEL's public origin — the same override chain as the SSO base URL,
    minus the API path prefix (see :data:`_PANEL_CALLBACK_PATH`)."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        override = await get_app_public_base_url_override(session)
    return (override or get_settings().sso_redirect_base_url).rstrip("/")


async def _identity_session_redirect(sessions: SessionStore, *, user_id: UUID) -> RedirectResponse:
    """Mint the identity session, put it in the cookie and BOUNCE to the panel.

    This is frontend-1: the callback used to answer ``LoginResponse`` JSON, so a
    user who logged in through their IdP landed on a page of raw JSON with the
    access token in it and no session anywhere — the SSO flow simply had no last
    mile. With the session in a cookie (ADR 0133) the last mile is a redirect:
    the browser carries the credential on its own.

    303 (not 302/307): the SAML ACS arrives as a POST, and only ``See Other``
    guarantees the browser switches to GET for the landing page.
    """
    minted = await _issue_identity_session(sessions, user_id=user_id)
    try:
        landing = sso_landing_url(await _effective_panel_origin())
    except InvalidLandingOriginError as exc:
        # Fail LOUD. Silently falling back to some default origin would hand a
        # fresh session cookie to whatever that origin happens to be.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="the platform public base URL is not a valid origin",
        ) from exc
    response = RedirectResponse(url=landing, status_code=status.HTTP_303_SEE_OTHER)
    issue_session_cookies(response, token=minted.access_token, max_age_seconds=minted.expires_in)
    return response


# ---------------------------------------------------------------------------
# Injectable HTTP client + flow (tests swap these via dependency_overrides)
# ---------------------------------------------------------------------------
def get_oidc_http_client() -> httpx.AsyncClient:
    """A plain async HTTP client for IdP round-trips.

    Overridden in tests with an ``httpx.AsyncClient`` bound to a
    ``MockTransport`` so the whole flow runs offline. The egress proxy
    wiring (ADR 0019) is layered on in a later task; Phase A keeps it a
    direct client.
    """
    return httpx.AsyncClient(timeout=10.0)


def get_oidc_flow(
    http_client: httpx.AsyncClient = Depends(get_oidc_http_client),
) -> OIDCFlow:
    return OIDCFlow(http_client)


def get_oidc_state_store(
    sessions: SessionStore = Depends(get_session_store),
) -> OIDCStateStore:
    # Reuse the same Redis client the session store rides on.
    return OIDCStateStore(sessions._redis)  # - same package


def get_saml_relay_state_store(
    sessions: SessionStore = Depends(get_session_store),
) -> SAMLRelayStateStore:
    # Reuse the same Redis client the session store rides on.
    return SAMLRelayStateStore(sessions._redis)  # - same package


async def _effective_redirect_base() -> str:
    """The effective public base URL for IdP redirects (ADR 0047 / 0069).

    Origin + API path prefix, both System-Admin-overridable (``platform_settings``)
    with env bootstrap fallbacks (``settings.sso_redirect_base_url`` /
    ``settings.api_path_prefix``). Read on the BYPASSRLS admin engine. Returns the
    full ``{origin}{prefix}`` (e.g. ``https://host/api``) with no trailing slash;
    the sync URL builders below append the well-known SSO paths to it, so under a
    single-origin reverse proxy (ADR 0061) the callback/ACS/EntityID carry the
    ``/api`` prefix. Empty prefix (default) reproduces the previous behaviour.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        override = await get_app_public_base_url_override(session)
        prefix_override = await get_api_path_prefix_override(session)
    base = (override or get_settings().sso_redirect_base_url).rstrip("/")
    raw_prefix = prefix_override if prefix_override is not None else get_settings().api_path_prefix
    try:
        prefix = validate_api_path_prefix(raw_prefix)
    except InvalidApiPathPrefixError:
        prefix = ""
    return f"{base}{prefix}"


async def _load_enabled_provider_by_id(provider_id: UUID, *, kind: str) -> SSOConfiguration | None:
    """Load the enabled, non-deleted global config with ``provider_id`` of ``kind``.

    The login routes are addressed by the global provider id (ADR 0047),
    so this resolves THAT specific row, asserting it is enabled, of the
    expected ``kind`` (``oidc`` / ``saml``), and not soft-deleted. Runs on
    the BYPASSRLS admin engine (the System Admin surface; the table has no
    RLS / ``tenant_id``). A mismatch (unknown id, disabled, wrong kind)
    returns ``None`` so the caller can answer with a single uniform 404 —
    never revealing whether some other provider exists.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration).where(
                SSOConfiguration.id == provider_id,
                SSOConfiguration.provider == kind,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


async def _load_enabled_providers() -> list[SSOConfiguration]:
    """Load every enabled, non-deleted global provider (ADR 0047).

    Backs the PUBLIC ``GET /auth/sso/providers`` list. Reads on the
    BYPASSRLS admin engine; ordered by ``created_at`` for a stable button
    order on the login page.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration)
            .where(
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
            .order_by(SSOConfiguration.created_at)
        )
        return list(result.scalars().all())


async def _issue_identity_session(sessions: SessionStore, *, user_id: UUID) -> LoginResponse:
    """Mint a tenant-less IDENTITY session + JWT (ADR 0047).

    Shared by the OIDC callback and the SAML ACS. The session proves the
    GLOBAL user identity WITHOUT an active tenant — exactly like the
    password-login pre-tenant session in ``routers/auth.py`` (``tenant_id
    = None``). Tenant access is resolved by membership AFTER login
    (task_sso_03); logout/revocation stay uniform across local/OIDC/SAML.
    """
    settings = get_settings()
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=user_id,
        tenant_id=None,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=None,
        is_system_admin=False,
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=ttl_seconds)


async def _provision_identity(*, email: str, full_name: str | None) -> UUID:
    """Provision the GLOBAL user identity at SSO login (ADR 0047).

    Auth providers are platform-global and access to a tenant is granted
    EXCLUSIVELY by ``UserOrganizationMembership`` that the admin assigns
    AFTER login (no claiming, no JIT membership — ADR 0047). So this only
    establishes the global user identity; it deliberately creates NO
    tenant membership and reads NO IdP groups. Tenant resolution (0 → "no
    access" screen, 1 → enter, >1 → picker) is task_sso_03.

    Policy:

      * **Link by verified email, never duplicate.** The IdP asserts the
        email; we normalise it to lower-case (matching local
        register/login) and look the user up. An existing local OR SSO
        user with that email is REUSED — never a second row for the same
        identity.
      * **First SSO login creates the user** with no usable local password
        (the ``_SSO_PASSWORD_SENTINEL`` hash that no plaintext can produce)
        and ``is_sso_provisioned = true`` so local login rejects it
        cleanly (see ``routers/auth.py`` — password login stays intact).
      * **Idempotent under concurrency.** Two simultaneous first-logins
        race on the ``users.email`` unique index; the race is caught and
        resolved by re-reading the winning row, so the user is never
        duplicated.

    The ``users`` table is NOT tenant-scoped (no RLS), so this needs no
    ``app.tenant_id`` binding. Returns the (existing or freshly created)
    user's id.
    """
    normalized_email = email.strip().lower()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        existing = await session.execute(select(User).where(User.email == normalized_email))
        user = existing.scalar_one_or_none()
        if user is not None:
            return user.id
        user = User(
            id=uuid7(),
            email=normalized_email,
            password_hash=_SSO_PASSWORD_SENTINEL,
            full_name=full_name,
            is_system_admin=False,
            is_sso_provisioned=True,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:
            # Race: another concurrent SSO login created the same user
            # between our SELECT and INSERT. Re-read the winner (the
            # unique email index guarantees exactly one) and proceed.
            await session.rollback()
            existing = await session.execute(select(User).where(User.email == normalized_email))
            user = existing.scalar_one()
        return user.id
