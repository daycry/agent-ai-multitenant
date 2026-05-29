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

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from uuid6 import uuid7

from api_server.auth.deps import get_session_store
from api_server.auth.jwt import encode_jwt
from api_server.auth.sessions import SessionStore
from api_server.auth.sso.oidc import OIDCError, OIDCFlow, OIDCUserInfo, ResolvedOIDCConfig
from api_server.auth.sso.secrets import SSOSecretError, resolve_client_secret
from api_server.auth.sso.state_store import LoginState, OIDCStateStore, new_token
from api_server.config import get_settings
from api_server.db.models import (
    SSOConfiguration,
    SSOProvider,
    User,
    UserOrganizationMembership,
    UserRole,
)
from api_server.db.session import get_sessionmaker
from api_server.routers.mcp import get_vault_resolver
from api_server.schemas.auth import LoginResponse

router = APIRouter(prefix="/auth/sso", tags=["sso"])

# JIT-provisioned users have no usable password — they authenticate only
# through the IdP. A sentinel hash that no plaintext can produce keeps
# the NOT NULL column satisfied while making local login impossible for
# them (verify_password against this never matches).
_SSO_PASSWORD_SENTINEL = "!sso-no-local-login!"  # - not a real secret

# OIDC callback path appended to `sso_redirect_base_url`. Must match the
# IdP's registered redirect-URI allowlist.
_CALLBACK_PATH = "/auth/sso/oidc/callback"


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
    return ResolvedOIDCConfig(
        issuer=row.issuer,
        client_id=row.client_id,
        client_secret=secret,
        scopes=list(row.scopes),
        claim_mappings={str(k): str(v) for k, v in (row.claim_mappings or {}).items()},
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

    user_id = await _jit_provision_user(login_state.tenant_id, userinfo)
    assert user_id is not None  # provisioning always returns a live user id

    # Mint the session + JWT — identical shape to local login.
    settings = get_settings()
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=user_id,
        tenant_id=login_state.tenant_id,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=login_state.tenant_id,
        is_system_admin=False,
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=ttl_seconds)


async def _jit_provision_user(tenant_uuid: UUID, userinfo: OIDCUserInfo) -> UUID:
    """Look the user up by email; create them (role ``tenant_user``) on first
    SSO login, and ensure they have an active membership in the tenant.

    Returns the user's id. Designed so the dedicated JIT task (task_08_07)
    can extend the policy without reshaping this call site.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        # `users` is NOT tenant-scoped (no RLS), so the lookup needs no
        # app.user_id binding. Membership IS tenant-scoped, so bind
        # app.tenant_id before touching it.
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_uuid)},
        )
        existing = await session.execute(select(User).where(User.email == userinfo.email))
        user = existing.scalar_one_or_none()
        if user is None:
            user = User(
                id=uuid7(),
                email=userinfo.email,
                password_hash=_SSO_PASSWORD_SENTINEL,
                full_name=userinfo.full_name,
                is_system_admin=False,
            )
            session.add(user)
            try:
                await session.flush()
            except IntegrityError:
                # Race: another concurrent SSO login created the same
                # user. Re-read and proceed.
                await session.rollback()
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": str(tenant_uuid)},
                )
                existing = await session.execute(select(User).where(User.email == userinfo.email))
                user = existing.scalar_one()

        # Ensure an active membership in this tenant (role tenant_user).
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
                    role=UserRole.TENANT_USER.value,
                    is_active=True,
                )
            )
        return user.id
