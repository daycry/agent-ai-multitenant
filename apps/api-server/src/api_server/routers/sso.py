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
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_session_store,
    require_system_admin,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.sessions import SessionStore
from api_server.auth.sso.oidc import OIDCError, OIDCFlow, ResolvedOIDCConfig
from api_server.auth.sso.saml import (
    DEFAULT_NAME_ID_FORMAT,
    ResolvedSAMLConfig,
    SAMLConfigError,
    SAMLError,
    SAMLUnavailableError,
    build_login_url,
    process_acs_response,
    saml_available,
    validate_saml_security,
)
from api_server.auth.sso.saml_metadata import IdPMetadataError, parse_idp_metadata
from api_server.auth.sso.secrets import (
    SSOSecretError,
    encrypt_client_secret,
    resolve_client_secret,
    resolve_sp_private_key,
)
from api_server.auth.sso.state_store import (
    LoginState,
    OIDCStateStore,
    SAMLLoginState,
    SAMLRelayStateStore,
    new_token,
)
from api_server.auth.sso.templates import list_templates
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
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.routers.mcp import get_vault_resolver
from api_server.schemas.auth import LoginResponse
from api_server.schemas.sso import (
    LOGIN_METHOD_PASSWORD,
    LOGIN_METHOD_SSO,
    ApiPathPrefixResponse,
    ApiPathPrefixUpdate,
    CallbackUrlResponse,
    IdPMetadataParseRequest,
    IdPMetadataParseResponse,
    LoginDiscoveryResponse,
    OIDCTemplateResponse,
    PublicBaseUrlResponse,
    PublicBaseUrlUpdate,
    PublicProviderResponse,
    SAMLConfigResponse,
    SAMLConfigUpsertRequest,
    SPMetadataResponse,
    SSOConfigResponse,
    SSOConfigUpsertRequest,
)

router = APIRouter(prefix="/auth/sso", tags=["sso"])

# Login discovery lives at `/auth/discover` (NOT under `/auth/sso/*`) — it
# is the entry point the login UI hits BEFORE it knows whether SSO applies,
# so it sits one level up alongside the local-login endpoints.
discovery_router = APIRouter(prefix="/auth", tags=["sso"])

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


def _callback_redirect_uri(base: str) -> str:
    return f"{base.rstrip('/')}{_CALLBACK_PATH}"


async def _load_enabled_oidc_config() -> SSOConfiguration | None:
    """Load the platform-global enabled, non-deleted OIDC config (ADR 0047).

    Auth providers are platform-global: there is at most one enabled
    ``oidc`` config for the whole platform (``uq_sso_config_provider``).
    The read runs on the BYPASSRLS admin engine (System Admin surface) —
    the table has no RLS policy and no ``tenant_id``; tenant access is
    granted by membership AFTER login, not by which tenant owns the
    provider.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration).where(
                SSOConfiguration.provider == SSOProvider.OIDC.value,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


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


def _resolve_config(row: SSOConfiguration) -> ResolvedOIDCConfig:
    """Turn a DB row into a flow config with the client secret resolved."""
    try:
        secret = resolve_client_secret(
            client_secret_ref=row.client_secret_ref,
            client_secret_encrypted=row.client_secret_encrypted,
            vault_resolver=get_vault_resolver(),
        )
    except SSOSecretError as exc:
        # Operator misconfiguration — never leak the cause to the client.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SSO is misconfigured for this tenant",
        ) from exc
    # OIDC rows always have issuer + client_id (the per-provider CHECK
    # constraint enforces it); narrow for mypy and fail loud if a row
    # somehow slipped through without them.
    if row.issuer is None or row.client_id is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SSO is misconfigured for this tenant",
        )
    return ResolvedOIDCConfig(
        issuer=row.issuer,
        client_id=row.client_id,
        client_secret=secret,
        scopes=list(row.scopes),
        claim_mappings={str(k): str(v) for k, v in (row.claim_mappings or {}).items()},
    )


# ---------------------------------------------------------------------------
# SAML config loading + resolution (mirrors the OIDC helpers above)
# ---------------------------------------------------------------------------
async def _load_enabled_saml_config() -> SSOConfiguration | None:
    """Load the platform-global enabled, non-deleted SAML config (ADR 0047).

    Mirrors :func:`_load_enabled_oidc_config`: at most one enabled
    ``saml`` config for the whole platform, read on the BYPASSRLS admin
    engine (the table has no RLS / ``tenant_id``).
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration).where(
                SSOConfiguration.provider == SSOProvider.SAML.value,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


def _sp_entity_id(base: str) -> str:
    return f"{base.rstrip('/')}{_SP_ENTITY_PATH}"


def _saml_acs_url(base: str) -> str:
    """The single, GLOBAL ACS URL the IdP POSTs the SAMLResponse to (ADR 0047)."""
    return f"{base.rstrip('/')}{_SAML_ACS_PATH}"


def _resolve_saml_config(row: SSOConfiguration, base: str) -> ResolvedSAMLConfig:
    """Turn a global SAML DB row into a flow config (ADR 0047).

    The per-provider CHECK constraint guarantees a `saml` row has
    entity_id + sso_url + x509_cert; narrow for mypy and fail loud (500)
    if a row somehow slipped through without them.

    Resolves the SP private key (task_08_05) from its Vault ref / Fernet
    ciphertext to plaintext PEM in memory — never stored or logged in
    clear. A secret-resolution fault or a security-invariant violation is
    operator-attributable → 500 (the cause is never echoed to the client).
    """
    if row.idp_entity_id is None or row.idp_sso_url is None or row.idp_x509_cert is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SSO is misconfigured",
        )
    try:
        sp_private_key = resolve_sp_private_key(
            sp_private_key_ref=row.sp_private_key_ref,
            sp_private_key_encrypted=row.sp_private_key_encrypted,
            vault_resolver=get_vault_resolver(),
        )
    except SSOSecretError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SSO is misconfigured",
        ) from exc
    config = ResolvedSAMLConfig(
        idp_entity_id=row.idp_entity_id,
        idp_sso_url=row.idp_sso_url,
        idp_x509_cert=row.idp_x509_cert,
        sp_entity_id=_sp_entity_id(base),
        sp_acs_url=_saml_acs_url(base),
        name_id_format=row.name_id_format or DEFAULT_NAME_ID_FORMAT,
        attribute_mappings={str(k): str(v) for k, v in (row.attribute_mappings or {}).items()},
        sp_x509_cert=row.sp_x509_cert,
        sp_private_key=sp_private_key,
        authn_requests_signed=row.authn_requests_signed,
        want_assertions_signed=row.want_assertions_signed,
        want_assertions_encrypted=row.want_assertions_encrypted,
        want_name_id_encrypted=row.want_name_id_encrypted,
    )
    try:
        validate_saml_security(config)
    except SAMLConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SSO is misconfigured",
        ) from exc
    return config


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


# ---------------------------------------------------------------------------
# GET /auth/sso/{provider_id}/oidc/login  (ADR 0047: by provider, not tenant)
# ---------------------------------------------------------------------------
@router.get("/{provider_id}/oidc/login")
async def oidc_login(
    provider_id: str,
    flow: OIDCFlow = Depends(get_oidc_flow),
    state_store: OIDCStateStore = Depends(get_oidc_state_store),
) -> RedirectResponse:
    """Begin the OIDC login: redirect the browser to the global IdP.

    Addressed by the GLOBAL provider id (ADR 0047), not a tenant. The
    issued session (after the callback) proves IDENTITY only — tenant
    access is resolved by membership AFTER login (task_sso_03).
    """
    try:
        provider_uuid = UUID(provider_id)
    except ValueError as exc:
        # A non-UUID provider segment can never match a real provider; a
        # uniform 404 avoids revealing the route shape vs. a 400.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown OIDC provider",
        ) from exc

    config_row = await _load_enabled_provider_by_id(provider_uuid, kind=SSOProvider.OIDC.value)
    if config_row is None:
        # Unknown / disabled / wrong-kind provider — same response either
        # way so we don't reveal which providers exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown OIDC provider",
        )

    config = _resolve_config(config_row)
    redirect_uri = _callback_redirect_uri(await _effective_redirect_base())
    state = new_token()
    nonce = new_token()

    try:
        authorize_url = await flow.build_authorization_url(
            config,
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
        )
    except OIDCError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="could not reach the identity provider",
        ) from exc

    await state_store.create(
        state,
        LoginState(provider_id=config_row.id, nonce=nonce, redirect_uri=redirect_uri),
        ttl_seconds=get_settings().sso_login_state_ttl_seconds,
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


# ---------------------------------------------------------------------------
# GET /auth/sso/oidc/callback
# ---------------------------------------------------------------------------
@router.get("/oidc/callback", response_model=LoginResponse)
async def oidc_callback(
    code: str = Query(...),
    state: str = Query(...),
    flow: OIDCFlow = Depends(get_oidc_flow),
    state_store: OIDCStateStore = Depends(get_oidc_state_store),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Complete the OIDC login and mint an IDENTITY session (ADR 0047).

    The single-use ``state`` carries the global provider that started the
    flow; we resolve THAT provider, assert it is still the enabled config,
    exchange the code, and mint a tenant-less identity session. Tenant
    access is resolved by membership AFTER login (task_sso_03).
    """
    login_state = await state_store.consume(state)
    if login_state is None:
        # Unknown/expired/replayed state — anti-CSRF tripwire.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired login state",
        )

    # Resolve the SAME provider the flow started with (from the state),
    # asserting it is still enabled + OIDC. A captured state can never be
    # steered onto a different provider this way.
    config_row = await _load_enabled_provider_by_id(
        login_state.provider_id, kind=SSOProvider.OIDC.value
    )
    if config_row is None:
        # The provider was disabled/removed mid-flight.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSO configuration is no longer available",
        )
    config = _resolve_config(config_row)

    try:
        tokens = await flow.exchange_code(config, code=code, redirect_uri=login_state.redirect_uri)
        id_token = str(tokens["id_token"])
        id_claims = await flow.verify_id_token(
            config, id_token=id_token, expected_nonce=login_state.nonce
        )
        access_token = tokens.get("access_token")
        userinfo = await flow.fetch_userinfo(
            config,
            access_token=str(access_token) if access_token else "",
            id_token_claims=id_claims,
        )
    except OIDCError as exc:
        # iss/aud/nonce mismatch and token-exchange faults are all
        # client-attributable to a broken/forged round-trip → 400.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OIDC authentication failed",
        ) from exc

    user_id = await _provision_identity(email=userinfo.email, full_name=userinfo.full_name)
    return await _issue_identity_session(sessions, user_id=user_id)


# ===========================================================================
# SAML 2.0 (Plan 08 task_08_04) — SP-initiated login + ACS (SP- and
# IdP-initiated). Added ALONGSIDE local login + OIDC; reuses the same
# session model. `python3-saml` is imported lazily inside the flow, so a
# node without the native xmlsec backend reports SAML as unavailable
# (501) instead of failing to import — local login + OIDC keep working.
# ===========================================================================
def _require_saml_available() -> None:
    """Short-circuit to 501 when the native SAML stack is absent."""
    if not saml_available():
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SAML support is not available on this server",
        )


# ---------------------------------------------------------------------------
# GET /auth/sso/{provider_id}/saml/login  (SP-initiated -> AuthnRequest)
# ---------------------------------------------------------------------------
@router.get("/{provider_id}/saml/login")
async def saml_login(
    provider_id: str,
    relay_store: SAMLRelayStateStore = Depends(get_saml_relay_state_store),
) -> RedirectResponse:
    """Begin an SP-initiated SAML login: redirect the browser to the IdP.

    Addressed by the GLOBAL provider id (ADR 0047), not a tenant.
    """
    _require_saml_available()
    try:
        provider_uuid = UUID(provider_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown SAML provider",
        ) from exc

    config_row = await _load_enabled_provider_by_id(provider_uuid, kind=SSOProvider.SAML.value)
    if config_row is None:
        # Unknown / disabled / wrong-kind provider — same response either
        # way so we don't reveal which providers exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown SAML provider",
        )

    config = _resolve_saml_config(config_row, await _effective_redirect_base())

    # RelayState is the SAML analogue of the OIDC `state`: a random,
    # single-use token the IdP echoes back to the ACS so we recover the
    # AuthnRequest id (InResponseTo guard) for the SP-initiated leg.
    relay_state = new_token()
    try:
        redirect_url = build_login_url(config, relay_state=relay_state)
    except SAMLUnavailableError as exc:  # pragma: no cover - guarded above
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SAML support is not available on this server",
        ) from exc
    except SAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="could not build the SAML request",
        ) from exc

    # The AuthnRequest id is embedded in the redirect URL's SAMLRequest;
    # python3-saml exposes it via `get_last_request_id`, but to avoid
    # re-parsing we correlate purely on RelayState + provider. The ACS
    # passes request_id=None when no in-flight state is found (covers
    # IdP-initiated), so the strict InResponseTo check is opt-in here:
    # we store the relay state with an empty request id and let the ACS
    # accept either. (task_08_05 tightens this with full request-id
    # tracking once SP signing lands.)
    await relay_store.create(
        relay_state,
        SAMLLoginState(provider_id=config_row.id, request_id=""),
        ttl_seconds=get_settings().sso_login_state_ttl_seconds,
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# POST /auth/sso/saml/acs  (GLOBAL Assertion Consumer Service — ADR 0047)
# ---------------------------------------------------------------------------
@router.post("/saml/acs", response_model=LoginResponse)
async def saml_acs(
    # The SAML spec names these form fields `SAMLResponse` / `RelayState`
    # (CamelCase, fixed by the binding). We accept those on the wire via
    # `alias` while keeping snake_case Python parameter names.
    saml_response: str = Form(..., alias="SAMLResponse"),
    relay_state: str | None = Form(default=None, alias="RelayState"),
    relay_store: SAMLRelayStateStore = Depends(get_saml_relay_state_store),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Consume a SAML ``SAMLResponse`` and mint an IDENTITY session (ADR 0047).

    The ACS is GLOBAL (one SP identity for the platform). Handles BOTH
    bindings of arrival:

      * SP-initiated — the browser was first sent to the IdP by
        ``/{provider_id}/saml/login``; the IdP POSTs back here with the
        RelayState we minted, which we consume single-use to recover the
        provider that started the flow + the AuthnRequest id for the
        ``InResponseTo`` correlation.
      * IdP-initiated (unsolicited) — the user started at the IdP; there
        is no RelayState we created, so correlation is skipped and the
        provider is the single enabled global SAML config.

    The issued session proves IDENTITY only — tenant access is resolved by
    membership AFTER login (task_sso_03).
    """
    _require_saml_available()

    # Recover any SP-initiated state (single-use). Absent for
    # IdP-initiated logins — that's expected, not an error.
    request_id: str | None = None
    provider_id: UUID | None = None
    if relay_state:
        login_state = await relay_store.consume(relay_state)
        if login_state is not None:
            provider_id = login_state.provider_id
            request_id = login_state.request_id or None

    # Resolve the provider: from the state (SP-initiated) or the single
    # enabled global SAML config (IdP-initiated).
    if provider_id is not None:
        config_row = await _load_enabled_provider_by_id(provider_id, kind=SSOProvider.SAML.value)
    else:
        config_row = await _load_enabled_saml_config()
    if config_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML configuration is no longer available",
        )
    config = _resolve_saml_config(config_row, await _effective_redirect_base())

    post_data = {"SAMLResponse": saml_response}
    if relay_state is not None:
        post_data["RelayState"] = relay_state

    try:
        userinfo = process_acs_response(config, post_data=post_data, request_id=request_id)
    except SAMLUnavailableError as exc:  # pragma: no cover - guarded above
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="SAML support is not available on this server",
        ) from exc
    except SAMLError as exc:
        # A bad/forged/expired assertion is client-attributable → 400.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML authentication failed",
        ) from exc

    user_id = await _provision_identity(email=userinfo.email, full_name=userinfo.full_name)
    return await _issue_identity_session(sessions, user_id=user_id)


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


# ===========================================================================
# Platform-global OIDC config CRUD (ADR 0047) — the System Admin UI.
#
# Auth providers are platform-global now (ADR 0047, supersedes the
# per-tenant part of ADR 0031): there is ONE oidc config for the whole
# platform, managed EXCLUSIVELY by `system_admin`. RBAC: every endpoint is
# gated to `require_system_admin` (a non-system-admin caller — even a
# tenant_admin — is 403) and runs on the BYPASSRLS admin session
# (`get_admin_session`); the table has no RLS / `tenant_id`, so a provider
# is identified by `provider`/kind, never by a tenant. Secrets are NEVER
# echoed back: the response carries only `has_client_secret` +
# `client_secret_source`.
# ===========================================================================
def _to_response(row: SSOConfiguration) -> SSOConfigResponse:
    """Project a DB row to the UI shape WITHOUT the secret value.

    The secret value (Vault ref or Fernet ciphertext) never crosses this
    boundary — the UI only learns whether one is set and where it lives.
    """
    source: str | None = None
    if row.client_secret_ref:
        source = _SECRET_SOURCE_VAULT
    elif row.client_secret_encrypted:
        source = _SECRET_SOURCE_ENCRYPTED
    return SSOConfigResponse(
        id=row.id,
        provider=row.provider,
        display_name=row.display_name,
        enabled=row.enabled,
        # This projection only serves OIDC rows (the CRUD endpoints filter
        # provider == 'oidc'), so issuer/client_id are always populated;
        # coerce None -> "" defensively to keep the str-typed response.
        issuer=row.issuer or "",
        client_id=row.client_id or "",
        scopes=list(row.scopes),
        claim_mappings={str(k): str(v) for k, v in (row.claim_mappings or {}).items()},
        group_role_mappings={str(k): str(v) for k, v in (row.group_role_mappings or {}).items()},
        email_domains=[str(d) for d in (row.email_domains or [])],
        has_client_secret=source is not None,
        client_secret_source=source,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_secret(
    row: SSOConfiguration, payload: SSOConfigUpsertRequest, *, is_create: bool
) -> None:
    """Set the secret on `row` from `payload`, encrypting plaintext at rest.

    Rules (the request validator already rejected "both" forms):
      * ``client_secret`` (plaintext) -> Fernet-encrypt, store in
        ``client_secret_encrypted``, clear the Vault ref.
      * ``client_secret_ref`` (Vault pointer) -> store as-is, clear the
        ciphertext column.
      * neither, on CREATE -> error (a confidential OIDC client needs a
        secret to do the code exchange).
      * neither, on EDIT -> leave the existing stored secret untouched.
    """
    if payload.client_secret is not None:
        row.client_secret_encrypted = encrypt_client_secret(payload.client_secret)
        row.client_secret_ref = None
    elif payload.client_secret_ref is not None:
        row.client_secret_ref = payload.client_secret_ref
        row.client_secret_encrypted = None
    elif is_create:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "a client secret is required: send client_secret (plaintext) "
                "or client_secret_ref (a Vault pointer)"
            ),
        )
    # else (edit, no secret in payload): keep what's already stored.


@router.get("/oidc/templates", response_model=list[OIDCTemplateResponse])
async def list_oidc_templates(
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> list[OIDCTemplateResponse]:
    """The per-IdP OIDC templates the UI offers in its provider picker.

    Read-only platform data, gated to System Admin (the only role that
    manages the global SSO config — ADR 0047).
    """
    return [
        OIDCTemplateResponse(
            template_id=t.template_id.value,
            display_name=t.display_name,
            issuer_template=t.issuer_template,
            default_scopes=t.scopes_with_openid(),
            claim_mappings=dict(t.claim_mappings),
            required_params=list(t.required_params),
            notes=t.notes,
        )
        for t in list_templates()
    ]


@router.get("/oidc/callback-url", response_model=CallbackUrlResponse)
async def get_oidc_callback_url(
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> CallbackUrlResponse:
    """The redirect/callback URL the operator must register at the IdP."""
    return CallbackUrlResponse(
        callback_url=_callback_redirect_uri(await _effective_redirect_base())
    )


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


@router.get("/config", response_model=list[SSOConfigResponse])
async def list_sso_configs(
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[SSOConfigResponse]:
    """List the platform-global (non-deleted) OIDC config — never the secret.

    The table is global (ADR 0047): at most one OIDC config for the whole
    platform (`uq_sso_config_provider`), read on the BYPASSRLS admin
    session.
    """
    result = await session.execute(
        select(SSOConfiguration)
        .where(
            SSOConfiguration.provider == SSOProvider.OIDC.value,
            SSOConfiguration.deleted_at.is_(None),
        )
        .order_by(SSOConfiguration.created_at)
    )
    return [_to_response(row) for row in result.scalars().all()]


@router.post("/config", response_model=SSOConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_sso_config(
    payload: SSOConfigUpsertRequest,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> SSOConfigResponse:
    """Create the platform-global OIDC config (ADR 0047). system_admin only.

    There is one OIDC config for the whole platform (DB unique constraint
    on ``provider``); a second create returns 409.
    """
    # Block a duplicate before the DB raises, so the client gets a clean
    # 409 instead of a 500 from the IntegrityError. A soft-deleted row
    # still occupies the unique slot, so this also covers "re-create after
    # delete" — the operator must hard-distinguish, which we keep simple
    # by surfacing the conflict.
    existing = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.provider == SSOProvider.OIDC.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the platform already has an OIDC configuration; edit it instead",
        )

    row = SSOConfiguration(
        id=uuid7(),
        provider=SSOProvider.OIDC.value,
        display_name=payload.display_name,
        enabled=payload.enabled,
        issuer=payload.issuer,
        client_id=payload.client_id,
        scopes=payload.scopes,
        claim_mappings=payload.claim_mappings,
        group_role_mappings=payload.group_role_mappings,
        email_domains=payload.email_domains,
    )
    _apply_secret(row, payload, is_create=True)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Race with a concurrent create, or a lingering soft-deleted row
        # against the unique constraint.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the platform already has an OIDC configuration",
        ) from exc
    await session.refresh(row)
    return _to_response(row)


@router.put("/config/{config_id}", response_model=SSOConfigResponse)
async def update_sso_config(
    config_id: UUID,
    payload: SSOConfigUpsertRequest,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> SSOConfigResponse:
    """Edit the platform-global OIDC config. system_admin only.

    Omitting both secret fields keeps the previously stored secret. An
    unknown config id 404s.
    """
    result = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.id == config_id,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO configuration not found",
        )

    row.display_name = payload.display_name
    row.enabled = payload.enabled
    row.issuer = payload.issuer
    row.client_id = payload.client_id
    row.scopes = payload.scopes
    row.claim_mappings = payload.claim_mappings
    row.group_role_mappings = payload.group_role_mappings
    row.email_domains = payload.email_domains
    _apply_secret(row, payload, is_create=False)
    await session.flush()
    await session.refresh(row)
    return _to_response(row)


@router.delete("/config/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sso_config(
    config_id: UUID,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Soft-delete the platform-global OIDC config. system_admin only."""
    result = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.id == config_id,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SSO configuration not found",
        )
    row.deleted_at = datetime.now(tz=UTC)
    await session.flush()


# ===========================================================================
# Platform-global SAML config CRUD (ADR 0047) — the System Admin UI.
#
# Mirrors the OIDC CRUD above for the SAML provider, with the SAME RBAC
# (`require_system_admin` on every endpoint — a non-system-admin caller is
# 403), the SAME global scoping (one SAML config for the whole platform,
# read/written on the BYPASSRLS admin session), and the SAME
# never-echo-the-secret rule (the SP PRIVATE key never crosses the wire;
# the response only reports whether one is set + which store holds it).
#
# Helper endpoints:
#   GET  /auth/sso/saml/sp-metadata    — the SP EntityID + the GLOBAL ACS
#        URL the operator registers at the IdP (ADR 0047: one SP identity
#        for the whole platform).
#   POST /auth/sso/saml/parse-metadata — parse pasted IdP metadata XML to
#        pre-fill the form (no xmlsec needed).
# ===========================================================================
def _to_saml_response(row: SSOConfiguration) -> SAMLConfigResponse:
    """Project a SAML DB row to the UI shape WITHOUT the SP private key.

    The SP private key (Vault ref or Fernet ciphertext) never crosses
    this boundary — the UI only learns whether one is set and where it
    lives. The IdP cert and SP public cert are not secret and round-trip.
    """
    key_source: str | None = None
    if row.sp_private_key_ref:
        key_source = _SECRET_SOURCE_VAULT
    elif row.sp_private_key_encrypted:
        key_source = _SECRET_SOURCE_ENCRYPTED
    # This projection only serves SAML rows (the CRUD filters provider ==
    # 'saml'), so the IdP triple is always populated; coerce None -> ""
    # defensively to keep the str-typed response.
    return SAMLConfigResponse(
        id=row.id,
        provider=row.provider,
        display_name=row.display_name,
        enabled=row.enabled,
        idp_entity_id=row.idp_entity_id or "",
        idp_sso_url=row.idp_sso_url or "",
        idp_x509_cert=row.idp_x509_cert or "",
        name_id_format=row.name_id_format,
        attribute_mappings={str(k): str(v) for k, v in (row.attribute_mappings or {}).items()},
        group_role_mappings={str(k): str(v) for k, v in (row.group_role_mappings or {}).items()},
        email_domains=[str(d) for d in (row.email_domains or [])],
        sp_x509_cert=row.sp_x509_cert,
        has_sp_private_key=key_source is not None,
        sp_private_key_source=key_source,
        authn_requests_signed=row.authn_requests_signed,
        want_assertions_signed=row.want_assertions_signed,
        want_assertions_encrypted=row.want_assertions_encrypted,
        want_name_id_encrypted=row.want_name_id_encrypted,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply_sp_private_key(row: SSOConfiguration, payload: SAMLConfigUpsertRequest) -> None:
    """Set the SP private key on `row` from `payload`, encrypting at rest.

    Unlike the OIDC client secret, the SP private key is OPTIONAL (a
    tenant that neither signs its AuthnRequest nor encrypts assertions
    needs none). Rules (the request validator already rejected "both"):

      * ``sp_private_key`` (plaintext PEM) -> Fernet-encrypt, store in
        ``sp_private_key_encrypted``, clear the Vault ref.
      * ``sp_private_key_ref`` (Vault pointer) -> store as-is, clear the
        ciphertext column.
      * neither -> leave the existing stored key untouched (so an edit
        that omits it keeps the key, mirroring the OIDC secret behavior).
    """
    if payload.sp_private_key is not None:
        row.sp_private_key_encrypted = encrypt_client_secret(payload.sp_private_key)
        row.sp_private_key_ref = None
    elif payload.sp_private_key_ref is not None:
        row.sp_private_key_ref = payload.sp_private_key_ref
        row.sp_private_key_encrypted = None
    # else: keep whatever is already stored.


def _validate_saml_crypto_invariant(row: SSOConfiguration) -> None:
    """Reject a config that enables a key-requiring feature without a key.

    Mirrors :func:`api_server.auth.sso.saml.validate_saml_security` and
    the DB CHECK constraint, but evaluated against the *resulting* row
    (after the key was applied) so we can return a clean 422 to the
    operator instead of surfacing a 500 from the DB constraint. Needs no
    native crypto.
    """
    needs_key = (
        row.authn_requests_signed or row.want_assertions_encrypted or row.want_name_id_encrypted
    )
    if not needs_key:
        return
    has_key = bool(row.sp_private_key_ref or row.sp_private_key_encrypted)
    if not row.sp_x509_cert or not has_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "AuthnRequest signing and assertion/NameID encryption require "
                "both an SP certificate and an SP private key"
            ),
        )


@router.get("/saml/sp-metadata", response_model=SPMetadataResponse)
async def get_saml_sp_metadata(
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> SPMetadataResponse:
    """The SP EntityID + the GLOBAL ACS URL to register at the IdP (ADR 0047).

    The SP identity (entityID + ACS) is platform-global now: one value for
    the whole platform, derived purely from the configured public base
    URL; needs no native crypto. Gated to System Admin (the role that
    manages the global SSO config).
    """
    base = await _effective_redirect_base()
    return SPMetadataResponse(sp_entity_id=_sp_entity_id(base), acs_url=_saml_acs_url(base))


@router.post("/saml/parse-metadata", response_model=IdPMetadataParseResponse)
async def parse_saml_idp_metadata(
    payload: IdPMetadataParseRequest,
    _principal: AuthPrincipal = Depends(require_system_admin),
) -> IdPMetadataParseResponse:
    """Parse pasted/uploaded IdP metadata XML to pre-fill the SAML form.

    Pure XML parsing (hardened against XXE) — needs NO native ``xmlsec``,
    so it works on every node. A malformed or non-IdP document yields a
    422 with a generic message. Gated to System Admin.
    """
    try:
        parsed = parse_idp_metadata(payload.metadata_xml)
    except IdPMetadataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    return IdPMetadataParseResponse(
        entity_id=parsed.entity_id,
        sso_url=parsed.sso_url,
        x509_cert=parsed.x509_cert,
        name_id_format=parsed.name_id_format,
    )


@router.get("/saml/config", response_model=list[SAMLConfigResponse])
async def list_saml_configs(
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[SAMLConfigResponse]:
    """List the platform-global (non-deleted) SAML config — never the SP key.

    The table is global (ADR 0047): at most one SAML config for the whole
    platform (`uq_sso_config_provider`), read on the BYPASSRLS admin
    session.
    """
    result = await session.execute(
        select(SSOConfiguration)
        .where(
            SSOConfiguration.provider == SSOProvider.SAML.value,
            SSOConfiguration.deleted_at.is_(None),
        )
        .order_by(SSOConfiguration.created_at)
    )
    return [_to_saml_response(row) for row in result.scalars().all()]


@router.post("/saml/config", response_model=SAMLConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_saml_config(
    payload: SAMLConfigUpsertRequest,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> SAMLConfigResponse:
    """Create the platform-global SAML config (ADR 0047). system_admin only.

    One SAML config for the whole platform (DB unique constraint on
    ``provider``); a second create returns 409.
    """
    existing = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.provider == SSOProvider.SAML.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the platform already has a SAML configuration; edit it instead",
        )

    row = SSOConfiguration(
        id=uuid7(),
        provider=SSOProvider.SAML.value,
        display_name=payload.display_name,
        enabled=payload.enabled,
        idp_entity_id=payload.idp_entity_id,
        idp_sso_url=payload.idp_sso_url,
        idp_x509_cert=payload.idp_x509_cert,
        name_id_format=payload.name_id_format,
        attribute_mappings=payload.attribute_mappings,
        group_role_mappings=payload.group_role_mappings,
        email_domains=payload.email_domains,
        sp_x509_cert=payload.sp_x509_cert,
        authn_requests_signed=payload.authn_requests_signed,
        want_assertions_signed=payload.want_assertions_signed,
        want_assertions_encrypted=payload.want_assertions_encrypted,
        want_name_id_encrypted=payload.want_name_id_encrypted,
    )
    _apply_sp_private_key(row, payload)
    _validate_saml_crypto_invariant(row)
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the platform already has a SAML configuration",
        ) from exc
    await session.refresh(row)
    return _to_saml_response(row)


@router.put("/saml/config/{config_id}", response_model=SAMLConfigResponse)
async def update_saml_config(
    config_id: UUID,
    payload: SAMLConfigUpsertRequest,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> SAMLConfigResponse:
    """Edit the platform-global SAML config. system_admin only.

    Omitting both SP-key fields keeps the previously stored key. An
    unknown config id (or one of the wrong kind) 404s.
    """
    result = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.id == config_id,
            SSOConfiguration.provider == SSOProvider.SAML.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SAML configuration not found",
        )

    row.display_name = payload.display_name
    row.enabled = payload.enabled
    row.idp_entity_id = payload.idp_entity_id
    row.idp_sso_url = payload.idp_sso_url
    row.idp_x509_cert = payload.idp_x509_cert
    row.name_id_format = payload.name_id_format
    row.attribute_mappings = payload.attribute_mappings
    row.group_role_mappings = payload.group_role_mappings
    row.email_domains = payload.email_domains
    row.sp_x509_cert = payload.sp_x509_cert
    row.authn_requests_signed = payload.authn_requests_signed
    row.want_assertions_signed = payload.want_assertions_signed
    row.want_assertions_encrypted = payload.want_assertions_encrypted
    row.want_name_id_encrypted = payload.want_name_id_encrypted
    _apply_sp_private_key(row, payload)
    _validate_saml_crypto_invariant(row)
    await session.flush()
    await session.refresh(row)
    return _to_saml_response(row)


@router.delete("/saml/config/{config_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saml_config(
    config_id: UUID,
    _principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Soft-delete the platform-global SAML config. system_admin only."""
    result = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.id == config_id,
            SSOConfiguration.provider == SSOProvider.SAML.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SAML configuration not found",
        )
    row.deleted_at = datetime.now(tz=UTC)
    await session.flush()
