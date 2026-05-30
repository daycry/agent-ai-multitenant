"""`/auth/sso/*` endpoints — generic OIDC login (Plan 08 task_08_01).

SSO is **added alongside** the existing email+password login
(``routers/auth.py``); it does not replace or touch it. A successful
OIDC callback issues a session EXACTLY like local login — a server-side
Redis session (:class:`SessionStore`) plus a JWT (:func:`encode_jwt`) —
so logout/revocation and every downstream `get_principal` check behave
identically regardless of how the user authenticated. There is no
stateless-JWT-after-OIDC path (Plan 08 "Decisiones Clave").

Two endpoints:

  * ``GET /auth/sso/{tenant_id}/oidc/login`` — resolve the tenant's
    enabled OIDC config, mint ``state`` + ``nonce``, store them
    server-side, and 307-redirect the browser to the IdP.
  * ``GET /auth/sso/oidc/callback`` — validate ``state`` (single-use,
    from Redis) → recover the tenant, exchange the ``code``, verify the
    ID token (signature + iss/aud/nonce), fetch userinfo, JIT-provision
    the user (role ``tenant_user`` on first login), then mint the
    session + JWT.

Multi-tenancy: the config read runs on the app role (NOBYPASSRLS) with
``app.tenant_id`` bound to the tenant from the login URL / state record,
so PostgreSQL RLS guarantees tenant A's SSO config can never be resolved
for tenant B even if an attacker forges identifiers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_session_store,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.sessions import SessionStore
from api_server.auth.sso.group_mapping import resolve_role_from_groups
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
    UserOrganizationMembership,
)
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.routers._helpers import require_tenant_id
from api_server.routers.mcp import get_vault_resolver
from api_server.schemas.auth import LoginResponse
from api_server.schemas.sso import (
    LOGIN_METHOD_PASSWORD,
    LOGIN_METHOD_SSO,
    CallbackUrlResponse,
    IdPMetadataParseRequest,
    IdPMetadataParseResponse,
    LoginDiscoveryResponse,
    OIDCTemplateResponse,
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
# Per-tenant ACS path; `{tenant_id}` is substituted. A per-tenant ACS
# URL lets the IdP-initiated (unsolicited) Response reach the right
# tenant's config even though the POST carries no RelayState we minted.
_SAML_ACS_PATH_TEMPLATE = "/auth/sso/{tenant_id}/saml/acs"

# The per-tenant login-flow entry points login discovery points the UI at.
# `{tenant_id}` is substituted with the resolved tenant. These match the
# `oidc_login` / `saml_login` routes below.
_OIDC_LOGIN_PATH_TEMPLATE = "/auth/sso/{tenant_id}/oidc/login"
_SAML_LOGIN_PATH_TEMPLATE = "/auth/sso/{tenant_id}/saml/login"


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


def _callback_redirect_uri() -> str:
    base = get_settings().sso_redirect_base_url.rstrip("/")
    return f"{base}{_CALLBACK_PATH}"


async def _load_enabled_oidc_config(tenant_id: str) -> SSOConfiguration | None:
    """Load the tenant's enabled, non-deleted OIDC config under RLS.

    Runs on the app role with ``app.tenant_id`` bound to ``tenant_id`` so
    the database itself filters to that tenant — a forged config id from
    another tenant simply returns no rows.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
        result = await session.execute(
            select(SSOConfiguration).where(
                SSOConfiguration.provider == SSOProvider.OIDC.value,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


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
async def _load_enabled_saml_config(tenant_id: str) -> SSOConfiguration | None:
    """Load the tenant's enabled, non-deleted SAML config under RLS.

    Same RLS guarantee as :func:`_load_enabled_oidc_config`: the read
    runs with ``app.tenant_id`` bound, so tenant A's SAML config is
    invisible to tenant B even with a forged identifier.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": tenant_id},
        )
        result = await session.execute(
            select(SSOConfiguration).where(
                SSOConfiguration.provider == SSOProvider.SAML.value,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


def _sp_entity_id() -> str:
    base = get_settings().sso_redirect_base_url.rstrip("/")
    return f"{base}{_SP_ENTITY_PATH}"


def _saml_acs_url(tenant_id: str) -> str:
    base = get_settings().sso_redirect_base_url.rstrip("/")
    return f"{base}{_SAML_ACS_PATH_TEMPLATE.format(tenant_id=tenant_id)}"


def _resolve_saml_config(row: SSOConfiguration, *, tenant_id: str) -> ResolvedSAMLConfig:
    """Turn a SAML DB row into a flow config.

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
            detail="SSO is misconfigured for this tenant",
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
            detail="SSO is misconfigured for this tenant",
        ) from exc
    config = ResolvedSAMLConfig(
        idp_entity_id=row.idp_entity_id,
        idp_sso_url=row.idp_sso_url,
        idp_x509_cert=row.idp_x509_cert,
        sp_entity_id=_sp_entity_id(),
        sp_acs_url=_saml_acs_url(tenant_id),
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
            detail="SSO is misconfigured for this tenant",
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
        login_url = _OIDC_LOGIN_PATH_TEMPLATE.format(tenant_id=config.tenant_id)
    elif provider == SSOProvider.SAML.value:
        login_url = _SAML_LOGIN_PATH_TEMPLATE.format(tenant_id=config.tenant_id)
    else:  # pragma: no cover - provider column is constrained to oidc/saml
        # Unknown provider value — fail safe to local login rather than
        # emit a half-formed SSO answer.
        return LoginDiscoveryResponse(method=LOGIN_METHOD_PASSWORD)

    return LoginDiscoveryResponse(
        method=LOGIN_METHOD_SSO,
        provider=provider,
        tenant_id=config.tenant_id,
        login_url=login_url,
    )


# ---------------------------------------------------------------------------
# GET /auth/sso/{tenant_id}/oidc/login
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/oidc/login")
async def oidc_login(
    tenant_id: str,
    flow: OIDCFlow = Depends(get_oidc_flow),
    state_store: OIDCStateStore = Depends(get_oidc_state_store),
) -> RedirectResponse:
    """Begin the OIDC login: redirect the browser to the tenant's IdP."""
    try:
        tenant_uuid = UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid tenant id",
        ) from exc

    config_row = await _load_enabled_oidc_config(tenant_id)
    if config_row is None:
        # No config, or it's disabled/deleted — same response either way so
        # we don't reveal whether a tenant has SSO at all.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no enabled OIDC configuration for this tenant",
        )

    config = _resolve_config(config_row)
    redirect_uri = _callback_redirect_uri()
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
        LoginState(tenant_id=tenant_uuid, nonce=nonce, redirect_uri=redirect_uri),
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
    """Complete the OIDC login and mint a session exactly like local login."""
    login_state = await state_store.consume(state)
    if login_state is None:
        # Unknown/expired/replayed state — anti-CSRF tripwire.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid or expired login state",
        )

    tenant_id = str(login_state.tenant_id)
    config_row = await _load_enabled_oidc_config(tenant_id)
    if config_row is None:
        # The tenant's config was disabled/removed mid-flight.
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

    user_id = await _jit_provision_user(
        login_state.tenant_id,
        email=userinfo.email,
        full_name=userinfo.full_name,
        groups=userinfo.groups,
        group_role_mappings={
            str(k): str(v) for k, v in (config_row.group_role_mappings or {}).items()
        },
    )
    assert user_id is not None  # provisioning always returns a live user id

    return await _issue_session(sessions, user_id=user_id, tenant_uuid=login_state.tenant_id)


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
# GET /auth/sso/{tenant_id}/saml/login  (SP-initiated -> AuthnRequest)
# ---------------------------------------------------------------------------
@router.get("/{tenant_id}/saml/login")
async def saml_login(
    tenant_id: str,
    relay_store: SAMLRelayStateStore = Depends(get_saml_relay_state_store),
) -> RedirectResponse:
    """Begin an SP-initiated SAML login: redirect the browser to the IdP."""
    _require_saml_available()
    try:
        tenant_uuid = UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid tenant id",
        ) from exc

    config_row = await _load_enabled_saml_config(tenant_id)
    if config_row is None:
        # No config, or disabled/deleted — same response either way so we
        # don't reveal whether a tenant has SAML SSO at all.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no enabled SAML configuration for this tenant",
        )

    config = _resolve_saml_config(config_row, tenant_id=tenant_id)

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
            detail="could not build the SAML request for this tenant",
        ) from exc

    # The AuthnRequest id is embedded in the redirect URL's SAMLRequest;
    # python3-saml exposes it via `get_last_request_id`, but to avoid
    # re-parsing we correlate purely on RelayState + tenant. The ACS
    # passes request_id=None when no in-flight state is found (covers
    # IdP-initiated), so the strict InResponseTo check is opt-in here:
    # we store the relay state with an empty request id and let the ACS
    # accept either. (task_08_05 tightens this with full request-id
    # tracking once SP signing lands.)
    await relay_store.create(
        relay_state,
        SAMLLoginState(tenant_id=tenant_uuid, request_id=""),
        ttl_seconds=get_settings().sso_login_state_ttl_seconds,
    )
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------------
# POST /auth/sso/{tenant_id}/saml/acs  (Assertion Consumer Service)
# ---------------------------------------------------------------------------
@router.post("/{tenant_id}/saml/acs", response_model=LoginResponse)
async def saml_acs(
    tenant_id: str,
    # The SAML spec names these form fields `SAMLResponse` / `RelayState`
    # (CamelCase, fixed by the binding). We accept those on the wire via
    # `alias` while keeping snake_case Python parameter names.
    saml_response: str = Form(..., alias="SAMLResponse"),
    relay_state: str | None = Form(default=None, alias="RelayState"),
    relay_store: SAMLRelayStateStore = Depends(get_saml_relay_state_store),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Consume a SAML ``SAMLResponse`` and mint a session like local login.

    Handles BOTH bindings of arrival:

      * SP-initiated — the browser was first sent to the IdP by
        ``/saml/login``; the IdP POSTs back here with the RelayState we
        minted, which we consume single-use and use to recover the
        AuthnRequest id for the ``InResponseTo`` correlation.
      * IdP-initiated (unsolicited) — the user started at the IdP; there
        is no RelayState we created, so correlation is skipped. The
        tenant is taken from the per-tenant ACS URL path instead.
    """
    _require_saml_available()
    try:
        UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid tenant id",
        ) from exc

    config_row = await _load_enabled_saml_config(tenant_id)
    if config_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SAML configuration is no longer available",
        )
    config = _resolve_saml_config(config_row, tenant_id=tenant_id)

    # Recover any SP-initiated state (single-use). Absent for
    # IdP-initiated logins — that's expected, not an error.
    request_id: str | None = None
    if relay_state:
        login_state = await relay_store.consume(relay_state)
        if login_state is not None:
            # Cross-tenant guard: a RelayState minted for tenant A must
            # not be replayed against tenant B's ACS.
            if str(login_state.tenant_id) != tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="SAML relay state does not match this tenant",
                )
            request_id = login_state.request_id or None

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

    tenant_uuid = UUID(tenant_id)
    user_id = await _jit_provision_user(
        tenant_uuid,
        email=userinfo.email,
        full_name=userinfo.full_name,
        groups=userinfo.groups,
        group_role_mappings={
            str(k): str(v) for k, v in (config_row.group_role_mappings or {}).items()
        },
    )
    return await _issue_session(sessions, user_id=user_id, tenant_uuid=tenant_uuid)


async def _issue_session(
    sessions: SessionStore, *, user_id: UUID, tenant_uuid: UUID
) -> LoginResponse:
    """Mint a Redis session + JWT — identical shape to local login.

    Shared by the OIDC callback and the SAML ACS so both auth methods
    end on exactly the same session model (logout/revocation stay
    uniform across local / OIDC / SAML).
    """
    settings = get_settings()
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=user_id,
        tenant_id=tenant_uuid,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_uuid,
        is_system_admin=False,
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=ttl_seconds)


async def _bind_tenant(session: AsyncSession, tenant_uuid: UUID) -> None:
    """Bind ``app.tenant_id`` for the current transaction so RLS scopes
    every tenant-scoped read/write to ``tenant_uuid``.

    Idempotent and cheap; called again after a rollback because the
    ``set_config(..., is_local=true)`` binding is scoped to the
    transaction that a rollback tears down.
    """
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_uuid)},
    )


async def _jit_provision_user(
    tenant_uuid: UUID,
    *,
    email: str,
    full_name: str | None,
    groups: list[str] | None = None,
    group_role_mappings: dict[str, str] | None = None,
) -> UUID:
    """Just-In-Time provisioning at first SSO login (Plan 08 task_08_07).

    Policy (shared by the OIDC callback and the SAML ACS):

      * **Link by verified email, never duplicate.** The IdP asserts the
        email; we normalise it to lower-case (matching local
        register/login) and look the user up. An existing local OR
        SSO user with that email is REUSED — we never create a second
        row for the same identity.
      * **First SSO login creates the user** with no usable local
        password (the ``_SSO_PASSWORD_SENTINEL`` hash that no plaintext
        can produce) and ``is_sso_provisioned = true`` so local login
        rejects it cleanly (see ``routers/auth.py``).
      * **Active membership in the SSO config's tenant.** The role is
        derived from the IdP groups via the tenant's
        ``group_role_mappings`` (task_08_11): the highest-privilege
        per-tenant role any asserted group maps to, defaulting to
        ``tenant_user`` when nothing maps. A group can NEVER grant a
        platform role (``system_admin`` / ``system_operator``). On EVERY
        login the existing membership's role is re-synced to the mapped
        role so an IdP group change takes effect on the next login —
        EXCEPT we never downgrade a manually-elevated ``tenant_admin``
        when the mapping resolves to the bare default and the tenant has
        no group mapping configured at all (so a tenant that never opted
        into group mapping keeps the old "JIT default, admin promotes
        manually" behaviour untouched). The membership is created/updated
        under ``app.tenant_id`` bound to ``tenant_uuid``, so it can only
        ever land in THIS tenant.
      * **Idempotent under concurrency.** Two simultaneous first-logins
        race on the ``users.email`` unique index and on the
        ``uq_membership_user_tenant`` unique index; both races are caught
        and resolved by re-reading the winning row, so neither the user
        nor the membership is ever duplicated.

    Returns the (existing or freshly created) user's id.
    """
    mappings = group_role_mappings or {}
    mapped_role = resolve_role_from_groups(groups or [], mappings)
    normalized_email = email.strip().lower()
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        # `users` is NOT tenant-scoped (no RLS), so the lookup needs no
        # app.tenant_id binding. Membership IS tenant-scoped, so bind
        # app.tenant_id before touching it.
        await _bind_tenant(session, tenant_uuid)

        existing = await session.execute(select(User).where(User.email == normalized_email))
        user = existing.scalar_one_or_none()
        if user is None:
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
                # Race: another concurrent SSO login created the same
                # user between our SELECT and INSERT. Re-read the winner
                # (the unique email index guarantees exactly one) and
                # proceed — no duplicate user row.
                await session.rollback()
                await _bind_tenant(session, tenant_uuid)
                existing = await session.execute(select(User).where(User.email == normalized_email))
                user = existing.scalar_one()

        # Ensure an active membership in this tenant. The query runs under
        # app.tenant_id == tenant_uuid, so it only ever sees / writes a
        # membership for THIS tenant.
        membership_q = await session.execute(
            select(UserOrganizationMembership).where(
                UserOrganizationMembership.user_id == user.id,
                UserOrganizationMembership.tenant_id == tenant_uuid,
            )
        )
        membership = membership_q.scalar_one_or_none()
        if membership is None:
            session.add(
                UserOrganizationMembership(
                    id=uuid7(),
                    tenant_id=tenant_uuid,
                    user_id=user.id,
                    role=mapped_role,
                    is_active=True,
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                # Race: a concurrent first-login created the membership
                # (uq_membership_user_tenant). The user already exists, so
                # the work is done — swallow and return the same user id.
                await session.rollback()
                return user.id
        else:
            _sync_membership_role(membership, mapped_role=mapped_role, has_mappings=bool(mappings))
        return user.id


def _sync_membership_role(
    membership: UserOrganizationMembership, *, mapped_role: str, has_mappings: bool
) -> None:
    """Re-sync an existing membership's role from the IdP group mapping.

    Behaviour split by whether the tenant configured ANY group mapping:

      * **No mapping configured** (``has_mappings`` False): leave the role
        untouched. ``mapped_role`` is just the bare default here, and a
        tenant that never opted into group mapping must keep the legacy
        flow where the JIT default sticks and a Tenant Admin promotes
        manually — we must not clobber a manual ``tenant_admin``.
      * **Mapping configured** (``has_mappings`` True): the IdP is the
        source of truth, so set the role to ``mapped_role`` on every
        login (an IdP group change — grant OR revoke — takes effect next
        login). ``mapped_role`` is already a safe per-tenant role
        (``resolve_role_from_groups`` never returns a platform role), so
        this can never escalate to ``system_admin`` / ``system_operator``.

    The membership's ``is_active`` flag is deliberately left untouched: a
    deactivated membership (e.g. a SCIM deprovision, task_08_08) stays
    deactivated — role mapping governs the role, not access revocation.
    """
    if has_mappings and membership.role != mapped_role:
        membership.role = mapped_role


# ===========================================================================
# Per-tenant OIDC config CRUD (Plan 08 task_08_03) — the Tenant-Admin UI.
#
# RBAC: reads need an active tenant membership (`require_tenant_member`),
# writes need `tenant_admin` (`require_tenant_admin`). RLS: every query
# runs on the tenant-scoped session, so tenant A's config is invisible to
# B at the database level — a forged config id from another tenant simply
# 404s. Secrets are NEVER echoed back: the response carries only
# `has_client_secret` + `client_secret_source`.
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "a client secret is required: send client_secret (plaintext) "
                "or client_secret_ref (a Vault pointer)"
            ),
        )
    # else (edit, no secret in payload): keep what's already stored.


@router.get("/oidc/templates", response_model=list[OIDCTemplateResponse])
async def list_oidc_templates(
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> list[OIDCTemplateResponse]:
    """The per-IdP OIDC templates the UI offers in its provider picker.

    Read-only and not tenant-specific (the registry is platform data),
    but gated to tenant members so anonymous callers can't enumerate it.
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
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> CallbackUrlResponse:
    """The redirect/callback URL the operator must register at the IdP."""
    return CallbackUrlResponse(callback_url=_callback_redirect_uri())


@router.get("/config", response_model=list[SSOConfigResponse])
async def list_sso_configs(
    _principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[SSOConfigResponse]:
    """List this tenant's (non-deleted) SSO configs — never the secret.

    RLS scopes the read to the active tenant, so this only ever returns
    the caller's own rows (0 or 1 OIDC config, per the unique constraint).
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
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SSOConfigResponse:
    """Create the tenant's OIDC config. tenant_admin only.

    There is one OIDC config per tenant (DB unique constraint on
    ``tenant_id, provider``); a second create returns 409.
    """
    tenant_id = require_tenant_id(principal)

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
            detail="this tenant already has an OIDC configuration; edit it instead",
        )

    row = SSOConfiguration(
        id=uuid7(),
        tenant_id=tenant_id,
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
            detail="this tenant already has an OIDC configuration",
        ) from exc
    await session.refresh(row)
    return _to_response(row)


@router.put("/config/{config_id}", response_model=SSOConfigResponse)
async def update_sso_config(
    config_id: UUID,
    payload: SSOConfigUpsertRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SSOConfigResponse:
    """Edit the tenant's OIDC config. tenant_admin only.

    Omitting both secret fields keeps the previously stored secret. A
    config id from another tenant 404s (RLS + tenant filter).
    """
    require_tenant_id(principal)
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
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete the tenant's OIDC config. tenant_admin only."""
    require_tenant_id(principal)
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
# Per-tenant SAML config CRUD (Plan 08 task_08_06) — the Tenant-Admin UI.
#
# Mirrors the OIDC CRUD above for the SAML provider, with the SAME RBAC
# (read = tenant member, write = tenant_admin), the SAME RLS scoping
# (a forged config id from another tenant 404s at the DB), and the SAME
# never-echo-the-secret rule (the SP PRIVATE key never crosses the wire;
# the response only reports whether one is set + which store holds it).
#
# Helper endpoints:
#   GET  /auth/sso/{tenant_id}/saml/metadata-url — the SP EntityID + ACS
#        URL the operator registers at the IdP.
#   POST /auth/sso/saml/parse-metadata           — parse pasted IdP
#        metadata XML to pre-fill the form (no xmlsec needed).
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "AuthnRequest signing and assertion/NameID encryption require "
                "both an SP certificate and an SP private key"
            ),
        )


@router.get("/saml/sp-metadata", response_model=SPMetadataResponse)
async def get_saml_sp_metadata(
    principal: AuthPrincipal = Depends(require_tenant_member),
) -> SPMetadataResponse:
    """The SP EntityID + the CALLER's per-tenant ACS URL to register at the IdP.

    Tenant-implicit: the tenant is taken from the authenticated principal
    (so the UI never needs to know its own tenant UUID). Derived purely
    from the configured public base URL + that tenant id; needs no native
    crypto. Gated to tenant members so anonymous callers can't enumerate it.
    """
    tenant_id = require_tenant_id(principal)
    return SPMetadataResponse(sp_entity_id=_sp_entity_id(), acs_url=_saml_acs_url(str(tenant_id)))


@router.get("/{tenant_id}/saml/metadata-url", response_model=SPMetadataResponse)
async def get_saml_sp_metadata_url(
    tenant_id: str,
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> SPMetadataResponse:
    """The SP EntityID + per-tenant ACS URL for an explicit tenant id.

    The explicit-tenant variant (used by superadmins acting on a specific
    tenant). Read-only and derived purely from the configured public base
    URL + the tenant id; gated to tenant members. Needs no native crypto.
    """
    try:
        UUID(tenant_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid tenant id",
        ) from exc
    return SPMetadataResponse(sp_entity_id=_sp_entity_id(), acs_url=_saml_acs_url(tenant_id))


@router.post("/saml/parse-metadata", response_model=IdPMetadataParseResponse)
async def parse_saml_idp_metadata(
    payload: IdPMetadataParseRequest,
    _principal: AuthPrincipal = Depends(require_tenant_member),
) -> IdPMetadataParseResponse:
    """Parse pasted/uploaded IdP metadata XML to pre-fill the SAML form.

    Pure XML parsing (hardened against XXE) — needs NO native ``xmlsec``,
    so it works on every node. A malformed or non-IdP document yields a
    422 with a generic message. Gated to tenant members.
    """
    try:
        parsed = parse_idp_metadata(payload.metadata_xml)
    except IdPMetadataError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
    _principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[SAMLConfigResponse]:
    """List this tenant's (non-deleted) SAML configs — never the SP key.

    RLS scopes the read to the active tenant (0 or 1 SAML config, per the
    unique constraint on ``tenant_id, provider``).
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
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SAMLConfigResponse:
    """Create the tenant's SAML config. tenant_admin only.

    One SAML config per tenant (DB unique constraint on
    ``tenant_id, provider``); a second create returns 409.
    """
    tenant_id = require_tenant_id(principal)

    existing = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.provider == SSOProvider.SAML.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this tenant already has a SAML configuration; edit it instead",
        )

    row = SSOConfiguration(
        id=uuid7(),
        tenant_id=tenant_id,
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
            detail="this tenant already has a SAML configuration",
        ) from exc
    await session.refresh(row)
    return _to_saml_response(row)


@router.put("/saml/config/{config_id}", response_model=SAMLConfigResponse)
async def update_saml_config(
    config_id: UUID,
    payload: SAMLConfigUpsertRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SAMLConfigResponse:
    """Edit the tenant's SAML config. tenant_admin only.

    Omitting both SP-key fields keeps the previously stored key. A config
    id from another tenant 404s (RLS + provider filter).
    """
    require_tenant_id(principal)
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
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete the tenant's SAML config. tenant_admin only."""
    require_tenant_id(principal)
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
