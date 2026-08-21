"""`/auth/sso/**` — el lado OIDC (plan prod-16, `task_prod16_10`).

Login por `provider_id`, callback, plantillas de IdP conocidos, la URL de
callback que el operador registra en el IdP, y el CRUD de configuraciones OIDC.
Lo transversal está en `common`; el lado SAML, en `saml`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
from api_server.auth.sessions import SessionStore
from api_server.auth.sso.oidc import OIDCError, OIDCFlow, ResolvedOIDCConfig
from api_server.auth.sso.secrets import (
    SSOSecretError,
    encrypt_client_secret,
    resolve_client_secret,
)
from api_server.auth.sso.state_store import (
    LoginState,
    OIDCStateStore,
    new_token,
)
from api_server.auth.sso.templates import list_templates
from api_server.config import get_settings
from api_server.db.models import (
    SSOConfiguration,
    SSOProvider,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.routers.mcp import get_vault_resolver
from api_server.routers.sso.common import (
    _CALLBACK_PATH,
    _SECRET_SOURCE_ENCRYPTED,
    _SECRET_SOURCE_VAULT,
    _effective_redirect_base,
    _identity_session_redirect,
    _load_enabled_provider_by_id,
    _provision_identity,
    get_oidc_flow,
    get_oidc_state_store,
)
from api_server.schemas.sso import (
    CallbackUrlResponse,
    OIDCTemplateResponse,
    SSOConfigResponse,
    SSOConfigUpsertRequest,
)

router = APIRouter(tags=["sso"])


def _callback_redirect_uri(base: str) -> str:
    return f"{base.rstrip('/')}{_CALLBACK_PATH}"


async def _load_enabled_oidc_config() -> SSOConfiguration | None:
    """La config OIDC habilitada más antigua (multi-provider 0115: puede haber
    N — los flujos reales resuelven por provider_id; este helper queda solo
    como conveniencia y toma la primera por antigüedad, nunca revienta con
    MultipleResultsFound). Read on the BYPASSRLS admin engine."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration)
            .where(
                SSOConfiguration.provider == SSOProvider.OIDC.value,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
            .order_by(SSOConfiguration.created_at)
        )
        return result.scalars().first()


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
@router.get("/oidc/callback")
async def oidc_callback(
    code: str = Query(...),
    state: str = Query(...),
    flow: OIDCFlow = Depends(get_oidc_flow),
    state_store: OIDCStateStore = Depends(get_oidc_state_store),
    sessions: SessionStore = Depends(get_session_store),
) -> RedirectResponse:
    """Complete the OIDC login, set the session cookie and land in the PANEL.

    Answers a 303 to ``{panel}/auth/callback`` — NOT the ``LoginResponse`` JSON
    it used to return (frontend-1: an SSO login ended on a page of raw JSON,
    with the token in the address bar's page and no session anywhere).


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
    return await _identity_session_redirect(sessions, user_id=user_id)


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
    """Create a platform-global OIDC config (ADR 0047). system_admin only.

    Multi-provider (2026-07-18, migración 0115): la plataforma admite N
    configs OIDC simultáneas (Google Y Microsoft, p.ej.) — el flujo es
    per-provider-id de punta a punta. Única exigencia: a partir de la
    segunda config del mismo kind, ``display_name`` es obligatorio para que
    los botones del login sean distinguibles (422 si falta).
    """
    existing = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.provider == SSOProvider.OIDC.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    if existing.scalars().first() is not None and not (payload.display_name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "ya existe otra configuración OIDC: display_name es obligatorio "
                "para distinguir los botones del login"
            ),
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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="conflicting OIDC configuration write",
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
