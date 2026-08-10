"""`/auth/sso/saml/**` — el lado SAML (plan prod-16, `task_prod16_10`).

Login SP-initiated por `provider_id`, el ACS global, los metadatos del SP, el
parseo de los metadatos del IdP y el CRUD de configuraciones SAML — incluido el
invariante criptográfico que impide guardar una config que firme sin clave.

`saml_available()` se importa AQUÍ, no en el paquete: los tests que simulan la
ausencia de python3-saml/xmlsec deben parchear `api_server.routers.sso.saml`.
Lo transversal está en `common`; el lado OIDC, en `oidc`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, status
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
    resolve_sp_private_key,
)
from api_server.auth.sso.state_store import (
    SAMLLoginState,
    SAMLRelayStateStore,
    new_token,
)
from api_server.config import get_settings
from api_server.db.models import (
    SSOConfiguration,
    SSOProvider,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.routers.mcp import get_vault_resolver
from api_server.routers.sso.common import (
    _SAML_ACS_PATH,
    _SECRET_SOURCE_ENCRYPTED,
    _SECRET_SOURCE_VAULT,
    _SP_ENTITY_PATH,
    _effective_redirect_base,
    _identity_session_redirect,
    _load_enabled_provider_by_id,
    _provision_identity,
    get_saml_relay_state_store,
)
from api_server.schemas.sso import (
    IdPMetadataParseRequest,
    IdPMetadataParseResponse,
    SAMLConfigResponse,
    SAMLConfigUpsertRequest,
    SPMetadataResponse,
)

router = APIRouter(tags=["sso"])


# ---------------------------------------------------------------------------
# SAML config loading + resolution (mirrors the OIDC helpers above)
# ---------------------------------------------------------------------------
async def _load_enabled_saml_config() -> SSOConfiguration | None:
    """Load THE single enabled, non-deleted SAML config (IdP-initiated path).

    Multi-provider (0115): con VARIAS configs SAML habilitadas, un ACS sin
    RelayState (IdP-initiated) es ambiguo — no sabemos contra qué IdP validar
    la aserción. 400 explícito en vez del MultipleResultsFound→500; el flujo
    SP-initiated (los botones del login) resuelve por RelayState y funciona
    con N. Read on the BYPASSRLS admin engine (no RLS / ``tenant_id``)."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(SSOConfiguration).where(
                SSOConfiguration.provider == SSOProvider.SAML.value,
                SSOConfiguration.enabled.is_(True),
                SSOConfiguration.deleted_at.is_(None),
            )
        )
        rows = list(result.scalars().all())
    if len(rows) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "IdP-initiated SAML no está soportado con múltiples configuraciones "
                "SAML habilitadas; inicia sesión desde la página de login (SP-initiated)"
            ),
        )
    return rows[0] if rows else None


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
@router.post("/saml/acs")
async def saml_acs(
    # The SAML spec names these form fields `SAMLResponse` / `RelayState`
    # (CamelCase, fixed by the binding). We accept those on the wire via
    # `alias` while keeping snake_case Python parameter names.
    saml_response: str = Form(..., alias="SAMLResponse"),
    relay_state: str | None = Form(default=None, alias="RelayState"),
    relay_store: SAMLRelayStateStore = Depends(get_saml_relay_state_store),
    sessions: SessionStore = Depends(get_session_store),
) -> RedirectResponse:
    """Consume a SAML ``SAMLResponse``, set the session cookie and land in the PANEL.


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
    return await _identity_session_redirect(sessions, user_id=user_id)


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
    """Create a platform-global SAML config (ADR 0047). system_admin only.

    Multi-provider (2026-07-18, migración 0115): N configs SAML simultáneas;
    a partir de la segunda, ``display_name`` obligatorio (botones del login
    distinguibles). El login SP-initiated resuelve por RelayState; el
    IdP-initiated exige una única config habilitada (400 si hay varias).
    """
    existing = await session.execute(
        select(SSOConfiguration).where(
            SSOConfiguration.provider == SSOProvider.SAML.value,
            SSOConfiguration.deleted_at.is_(None),
        )
    )
    if existing.scalars().first() is not None and not (payload.display_name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "ya existe otra configuración SAML: display_name es obligatorio "
                "para distinguir los botones del login"
            ),
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
