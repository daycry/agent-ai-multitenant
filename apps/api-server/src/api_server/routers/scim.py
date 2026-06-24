"""SCIM 2.0 user provisioning endpoints (Plan 08 task_08_08).

``/scim/v2/Users`` — an IdP (Okta, Azure AD, ...) creates, reads,
updates and deactivates users in a tenant via the SCIM 2.0 protocol
(RFC 7643/7644). This is ADDED ALONGSIDE the interactive auth: local
login, OIDC and SAML are untouched and keep issuing the same Redis
session + JWT. SCIM is machine-to-machine, so it authenticates with a
per-tenant **bearer token** (``scim_tokens`` table) instead of a JWT —
the token *is* the tenant context.

Multi-tenancy: the presented token is resolved ONCE on the BYPASSRLS
admin role (``token_hash`` -> ``tenant_id``), because the request is
unauthenticated until the token is matched. Every subsequent query then
runs on the app role (NOBYPASSRLS) with ``app.tenant_id`` bound to the
resolved tenant, so PostgreSQL RLS guarantees a token issued for tenant
A can never read or write tenant B's users (the ``@pytest.mark.cross_tenant``
test pins this).

Mapping (see :mod:`api_server.schemas.scim`):

  * ``userName`` / primary ``emails`` -> ``users.email`` (normalised
    lower-case, like local register / SSO JIT — no duplicate identities).
  * ``name.formatted`` / ``displayName`` -> ``users.full_name``.
  * ``externalId`` -> echoed back (the IdP's stable id).
  * ``active`` -> the per-tenant ``user_org_memberships.is_active``.
    Deprovisioning (``active=false`` or ``DELETE``) deactivates the
    membership AND revokes the user's live sessions in this tenant, so
    access is cut immediately — not when the JWT happens to expire.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_session_store,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.auth.scim.tokens import generate_scim_token, hash_scim_token, token_prefix
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.models import ScimToken, User, UserOrganizationMembership, UserRole
from api_server.db.platform_settings import (
    InvalidApiPathPrefixError,
    validate_api_path_prefix,
)
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.scim import (
    SCIM_USER_RESOURCE_TYPE,
    ScimEmail,
    ScimError,
    ScimListResponse,
    ScimMeta,
    ScimName,
    ScimPatchRequest,
    ScimTokenCreatedResponse,
    ScimTokenCreateRequest,
    ScimTokenResponse,
    ScimUserRequest,
    ScimUserResponse,
)

# JIT/SCIM-provisioned users have no usable local password — same sentinel
# the SSO JIT path uses (`routers/sso.py`), so local login can never match.
_SCIM_PASSWORD_SENTINEL = "!sso-no-local-login!"  # - not a real secret

# SCIM ListResponse paging defaults (RFC 7644 §3.4.2). startIndex is 1-based.
_DEFAULT_START_INDEX = 1
_DEFAULT_COUNT = 100
_MAX_COUNT = 200

# Content type SCIM mandates for its JSON bodies (RFC 7644 §3.1).
_SCIM_CONTENT_TYPE = "application/scim+json"

# The two SCIM routers: the machine-facing /scim/v2/* surface (token auth)
# and the operator-facing token management under /auth/sso/scim/* (JWT +
# tenant_admin), kept on one APIRouter for a single include in main.py.
router = APIRouter(tags=["scim"])


# ===========================================================================
# Token authentication — resolve the bearer token to a tenant
# ===========================================================================
class ScimAuthError(Exception):
    """Raised when the SCIM bearer token is missing/unknown/revoked."""


def _scim_error_response(
    status_code: int, detail: str, *, scim_type: str | None = None
) -> JSONResponse:
    """Build a SCIM-shaped error envelope (RFC 7644 §3.12)."""
    body = ScimError(detail=detail, status=str(status_code), scim_type=scim_type)
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(by_alias=True, exclude_none=True),
        media_type=_SCIM_CONTENT_TYPE,
    )


def _parse_bearer(authorization: str | None) -> str:
    if not authorization:
        raise ScimAuthError("missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ScimAuthError("malformed Authorization header")
    return token


async def _resolve_token_tenant(token: str) -> tuple[UUID, UUID]:
    """Resolve a clear SCIM token to ``(tenant_id, token_id)``.

    Runs on the BYPASSRLS admin role: the request is unauthenticated until
    the token is matched, so there is no ``app.tenant_id`` to scope by yet.
    The lookup is an equality probe on the SHA-256 digest (the token is
    never stored in clear). A revoked token resolves to nothing.

    Bumps ``last_used_at`` as a side effect (best effort, same txn).
    """
    digest = hash_scim_token(token)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(
            select(ScimToken).where(
                ScimToken.token_hash == digest,
                ScimToken.revoked_at.is_(None),
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ScimAuthError("invalid SCIM token")
        row.last_used_at = datetime.now(tz=UTC)
        return row.tenant_id, row.id


async def get_scim_tenant(
    authorization: str | None = Header(default=None),
) -> UUID:
    """FastAPI dependency: the tenant resolved from the SCIM bearer token.

    Raises :class:`ScimAuthError` (turned into a SCIM 401 by the handler's
    exception path) when the token is missing, malformed, unknown, or
    revoked.
    """
    token = _parse_bearer(authorization)
    tenant_id, _ = await _resolve_token_tenant(token)
    return tenant_id


# ===========================================================================
# Tenant-scoped session bound to the SCIM token's tenant (RLS)
# ===========================================================================
def _scim_session() -> AsyncSession:
    """Open an app-role (NOBYPASSRLS) session for a SCIM request.

    NOT a FastAPI dependency — the endpoints manage the session lifetime
    explicitly so the whole create/patch sequence runs in one transaction.
    The caller binds ``app.tenant_id`` via :func:`_bind_tenant` as the
    first statement inside ``session.begin()`` so RLS scopes every query.
    """
    return get_sessionmaker()()


async def _bind_tenant(session: AsyncSession, tenant_id: UUID) -> None:
    """Bind ``app.tenant_id`` for the current transaction (RLS scope).

    ``set_config(..., is_local := true)`` is transaction-scoped, so this
    must run inside the same ``session.begin()`` block as the queries it
    governs (and again after any rollback, which tears the binding down).
    """
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"),
        {"tid": str(tenant_id)},
    )


# ===========================================================================
# SCIM <-> domain mapping helpers
# ===========================================================================
def _scim_user_location(user_id: UUID) -> str:
    # ADR 0069: include the API path prefix so the Location header is correct
    # behind a single-origin reverse proxy. SCIM is bootstrap-configured (env),
    # so it uses the env prefix (not the live DB override, like its base).
    settings = get_settings()
    base = settings.sso_redirect_base_url.rstrip("/")
    try:
        prefix = validate_api_path_prefix(settings.api_path_prefix)
    except InvalidApiPathPrefixError:
        prefix = ""
    return f"{base}{prefix}/scim/v2/Users/{user_id}"


def _email_from_request(payload: ScimUserRequest) -> str:
    """Pick the email: primary email if present, else userName.

    Normalised to lower-case to match local register / SSO JIT so the same
    human is never duplicated across auth methods.
    """
    primary = next((e.value for e in payload.emails if e.primary), None)
    raw = primary or (payload.emails[0].value if payload.emails else payload.user_name)
    return raw.strip().lower()


def _full_name_from_request(payload: ScimUserRequest) -> str | None:
    if payload.name and payload.name.formatted:
        return payload.name.formatted
    return payload.display_name


def _to_scim_user(
    user: User,
    membership: UserOrganizationMembership,
) -> ScimUserResponse:
    """Project a (user, membership) pair to the SCIM User response."""
    return ScimUserResponse(
        id=str(user.id),
        external_id=membership.external_id,
        user_name=user.email,
        display_name=user.full_name,
        name=ScimName(formatted=user.full_name) if user.full_name else None,
        emails=[ScimEmail(value=user.email, primary=True, type="work")],
        active=membership.is_active,
        meta=ScimMeta(
            resource_type=SCIM_USER_RESOURCE_TYPE,
            created=membership.created_at,
            last_modified=membership.updated_at,
            location=_scim_user_location(user.id),
        ),
    )


def _scim_json(model: ScimUserResponse | ScimListResponse, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=model.model_dump(by_alias=True, exclude_none=True, mode="json"),
        media_type=_SCIM_CONTENT_TYPE,
    )


# ===========================================================================
# /scim/v2/Users — the SCIM 2.0 protocol surface (token-authenticated)
# ===========================================================================
@router.post("/scim/v2/Users")
async def scim_create_user(
    payload: ScimUserRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Create (or link) a user + active membership in the token's tenant.

    Idempotent on identity: if a user with the (normalised) email already
    exists they are LINKED, never duplicated — matching the SSO JIT
    policy. If they already have a membership in this tenant we return 409
    (SCIM ``uniqueness``); the IdP should PUT/PATCH instead.
    """
    try:
        token = _parse_bearer(authorization)
        tenant_id, _ = await _resolve_token_tenant(token)
    except ScimAuthError as exc:
        return _scim_error_response(status.HTTP_401_UNAUTHORIZED, str(exc))

    email = _email_from_request(payload)
    full_name = _full_name_from_request(payload)

    session = _scim_session()
    try:
        async with session.begin():
            await _bind_tenant(session, tenant_id)
            # Link by verified email, never duplicate (users is un-RLSed).
            existing = await session.execute(select(User).where(User.email == email))
            user = existing.scalar_one_or_none()
            if user is None:
                user = User(
                    id=uuid7(),
                    email=email,
                    password_hash=_SCIM_PASSWORD_SENTINEL,
                    full_name=full_name,
                    is_system_admin=False,
                    is_sso_provisioned=True,
                )
                session.add(user)
                try:
                    await session.flush()
                except IntegrityError:
                    # Race: a concurrent create won. Re-read the winner.
                    await session.rollback()
                    await _bind_tenant(session, tenant_id)
                    existing = await session.execute(select(User).where(User.email == email))
                    user = existing.scalar_one()

            membership_q = await session.execute(
                select(UserOrganizationMembership).where(
                    UserOrganizationMembership.user_id == user.id,
                    UserOrganizationMembership.tenant_id == tenant_id,
                )
            )
            membership = membership_q.scalar_one_or_none()
            if membership is not None and membership.deleted_at is None:
                return _scim_error_response(
                    status.HTTP_409_CONFLICT,
                    "user already provisioned in this tenant",
                    scim_type="uniqueness",
                )
            if membership is None:
                membership = UserOrganizationMembership(
                    id=uuid7(),
                    tenant_id=tenant_id,
                    user_id=user.id,
                    role=UserRole.TENANT_USER.value,
                    is_active=payload.active,
                    external_id=payload.external_id,
                )
                session.add(membership)
            else:
                # A soft-deleted membership is revived by the IdP re-create.
                membership.deleted_at = None
                membership.is_active = payload.active
                membership.external_id = payload.external_id
            await session.flush()
            await session.refresh(membership)
            await session.refresh(user)
            body = _to_scim_user(user, membership)
        return _scim_json(body, status.HTTP_201_CREATED)
    finally:
        await session.close()


@router.get("/scim/v2/Users/{user_id}")
async def scim_get_user(
    user_id: UUID,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Read a single provisioned user by id (scoped to the token's tenant)."""
    try:
        token = _parse_bearer(authorization)
        tenant_id, _ = await _resolve_token_tenant(token)
    except ScimAuthError as exc:
        return _scim_error_response(status.HTTP_401_UNAUTHORIZED, str(exc))

    session = _scim_session()
    try:
        async with session.begin():
            await _bind_tenant(session, tenant_id)
            membership = await _load_membership(session, tenant_id, user_id)
            if membership is None:
                return _scim_error_response(
                    status.HTTP_404_NOT_FOUND, "user not found in this tenant"
                )
            user = await _load_user(session, user_id)
            if user is None:
                return _scim_error_response(
                    status.HTTP_404_NOT_FOUND, "user not found in this tenant"
                )
            body = _to_scim_user(user, membership)
        return _scim_json(body, status.HTTP_200_OK)
    finally:
        await session.close()


@router.get("/scim/v2/Users")
async def scim_list_users(
    authorization: str | None = Header(default=None),
    scim_filter: str | None = Query(default=None, alias="filter"),
    start_index: int = Query(default=_DEFAULT_START_INDEX, alias="startIndex", ge=1),
    count: int = Query(default=_DEFAULT_COUNT, ge=0, le=_MAX_COUNT),
) -> JSONResponse:
    """List provisioned users; supports ``filter=userName eq "x"`` (RFC 7644).

    Only the ``userName eq`` filter is implemented (the one IdPs use to
    check existence before a create). An unsupported filter yields an
    empty result rather than an error, which IdPs tolerate.
    """
    try:
        token = _parse_bearer(authorization)
        tenant_id, _ = await _resolve_token_tenant(token)
    except ScimAuthError as exc:
        return _scim_error_response(status.HTTP_401_UNAUTHORIZED, str(exc))

    username_eq = _parse_username_eq_filter(scim_filter)

    session = _scim_session()
    try:
        async with session.begin():
            await _bind_tenant(session, tenant_id)
            # Memberships in this tenant (RLS-scoped) joined to their users.
            stmt = (
                select(UserOrganizationMembership, User)
                .join(User, User.id == UserOrganizationMembership.user_id)
                .where(UserOrganizationMembership.deleted_at.is_(None))
                .order_by(UserOrganizationMembership.created_at)
            )
            if username_eq is not None:
                stmt = stmt.where(func.lower(User.email) == username_eq.lower())
            rows = (await session.execute(stmt)).all()
            total = len(rows)
            # Apply 1-based paging window.
            page = rows[start_index - 1 : start_index - 1 + count] if count > 0 else []
            resources = [_to_scim_user(user, membership) for membership, user in page]
            body = ScimListResponse(
                total_results=total,
                start_index=start_index,
                items_per_page=len(resources),
                resources=resources,
            )
        return _scim_json(body, status.HTTP_200_OK)
    finally:
        await session.close()


@router.put("/scim/v2/Users/{user_id}")
async def scim_replace_user(
    user_id: UUID,
    payload: ScimUserRequest,
    authorization: str | None = Header(default=None),
    sessions: SessionStore = Depends(get_session_store),
) -> JSONResponse:
    """Replace a user's mapped attributes (full update, RFC 7644 §3.5.1).

    Updates full_name + external_id and applies ``active``. Flipping
    ``active`` to false deprovisions (revokes sessions); flipping back to
    true re-activates the membership.
    """
    try:
        token = _parse_bearer(authorization)
        tenant_id, _ = await _resolve_token_tenant(token)
    except ScimAuthError as exc:
        return _scim_error_response(status.HTTP_401_UNAUTHORIZED, str(exc))

    session = _scim_session()
    try:
        async with session.begin():
            await _bind_tenant(session, tenant_id)
            membership = await _load_membership(session, tenant_id, user_id)
            user = await _load_user(session, user_id)
            if membership is None or user is None:
                return _scim_error_response(
                    status.HTTP_404_NOT_FOUND, "user not found in this tenant"
                )
            user.full_name = _full_name_from_request(payload)
            membership.external_id = payload.external_id
            was_active = membership.is_active
            membership.is_active = payload.active
            await session.flush()
            await session.refresh(membership)
            await session.refresh(user)
            body = _to_scim_user(user, membership)
        # Deprovision side effect AFTER the txn commits: if it just went
        # inactive, cut live sessions in this tenant.
        if was_active and not payload.active:
            await sessions.revoke_user_sessions(user_id, tenant_id)
        return _scim_json(body, status.HTTP_200_OK)
    finally:
        await session.close()


@router.patch("/scim/v2/Users/{user_id}")
async def scim_patch_user(
    user_id: UUID,
    payload: ScimPatchRequest,
    authorization: str | None = Header(default=None),
    sessions: SessionStore = Depends(get_session_store),
) -> JSONResponse:
    """Apply a SCIM PatchOp (RFC 7644 §3.5.2).

    The IdP's primary use is ``replace`` of ``active`` to deprovision
    (``active=false``) or re-enable a user. We also accept ``replace`` of
    ``displayName`` / ``name.formatted`` and ``externalId``. Operations on
    unmapped paths are ignored (no error) so a chatty IdP still succeeds.
    """
    try:
        token = _parse_bearer(authorization)
        tenant_id, _ = await _resolve_token_tenant(token)
    except ScimAuthError as exc:
        return _scim_error_response(status.HTTP_401_UNAUTHORIZED, str(exc))

    session = _scim_session()
    try:
        async with session.begin():
            await _bind_tenant(session, tenant_id)
            membership = await _load_membership(session, tenant_id, user_id)
            user = await _load_user(session, user_id)
            if membership is None or user is None:
                return _scim_error_response(
                    status.HTTP_404_NOT_FOUND, "user not found in this tenant"
                )
            was_active = membership.is_active
            _apply_patch_operations(payload, user=user, membership=membership)
            await session.flush()
            await session.refresh(membership)
            await session.refresh(user)
            body = _to_scim_user(user, membership)
        if was_active and not membership.is_active:
            await sessions.revoke_user_sessions(user_id, tenant_id)
        return _scim_json(body, status.HTTP_200_OK)
    finally:
        await session.close()


@router.delete("/scim/v2/Users/{user_id}")
async def scim_delete_user(
    user_id: UUID,
    authorization: str | None = Header(default=None),
    sessions: SessionStore = Depends(get_session_store),
) -> Response:
    """Deprovision a user (RFC 7644 §3.6).

    We do NOT delete the global user row (they may belong to other
    tenants). Instead we soft-delete + deactivate the membership in THIS
    tenant and revoke the user's live sessions here, so access is cut
    immediately. 204 on success, 404 if the user was never provisioned
    here.
    """
    try:
        token = _parse_bearer(authorization)
        tenant_id, _ = await _resolve_token_tenant(token)
    except ScimAuthError as exc:
        return _scim_error_response(status.HTTP_401_UNAUTHORIZED, str(exc))

    session = _scim_session()
    try:
        async with session.begin():
            await _bind_tenant(session, tenant_id)
            membership = await _load_membership(session, tenant_id, user_id)
            if membership is None:
                return _scim_error_response(
                    status.HTTP_404_NOT_FOUND, "user not found in this tenant"
                )
            membership.is_active = False
            membership.deleted_at = datetime.now(tz=UTC)
            await session.flush()
        await sessions.revoke_user_sessions(user_id, tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Internal query + patch helpers
# ---------------------------------------------------------------------------
async def _load_membership(
    session: AsyncSession, tenant_id: UUID, user_id: UUID
) -> UserOrganizationMembership | None:
    result = await session.execute(
        select(UserOrganizationMembership).where(
            UserOrganizationMembership.user_id == user_id,
            UserOrganizationMembership.tenant_id == tenant_id,
            UserOrganizationMembership.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def _load_user(session: AsyncSession, user_id: UUID) -> User | None:
    result = await session.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()


def _parse_username_eq_filter(scim_filter: str | None) -> str | None:
    """Extract the value from a ``userName eq "value"`` SCIM filter.

    Returns the value (without surrounding quotes) for the one filter we
    support, or ``None`` for an absent/unsupported filter (the caller then
    lists everything / nothing accordingly).
    """
    if not scim_filter:
        return None
    parts = scim_filter.strip().split(None, 2)
    if len(parts) != 3:
        return None
    attr, op, raw_value = parts
    if attr.lower() != "username" or op.lower() != "eq":
        return None
    return raw_value.strip().strip('"')


def _apply_patch_operations(
    payload: ScimPatchRequest,
    *,
    user: User,
    membership: UserOrganizationMembership,
) -> None:
    """Apply each PATCH op to the (user, membership). Unmapped paths ignored."""
    for operation in payload.operations:
        if operation.op.lower() not in {"replace", "add"}:
            continue
        path = (operation.path or "").lower()
        value = operation.value
        if path == "active":
            membership.is_active = _coerce_bool(value)
        elif path in {"displayname", "name.formatted"}:
            user.full_name = None if value is None else str(value)
        elif path == "externalid":
            membership.external_id = None if value is None else str(value)
        elif path == "" and isinstance(value, dict):
            # No-path replace: a dict of attributes (Azure AD style).
            if "active" in value:
                membership.is_active = _coerce_bool(value["active"])
            if "displayName" in value:
                dn = value["displayName"]
                user.full_name = None if dn is None else str(dn)
            if "externalId" in value:
                ext = value["externalId"]
                membership.external_id = None if ext is None else str(ext)


def _coerce_bool(value: object) -> bool:
    """Coerce a SCIM PATCH value to bool (IdPs send true/false or "True")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


# ===========================================================================
# SCIM token management (the Tenant-Admin UI) — JWT + tenant_admin
# ===========================================================================
@router.get("/auth/sso/scim/tokens", response_model=list[ScimTokenResponse])
async def list_scim_tokens(
    _principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ScimTokenResponse]:
    """List this tenant's SCIM tokens — never the token value. tenant_admin."""
    result = await session.execute(select(ScimToken).order_by(ScimToken.created_at.desc()))
    return [
        ScimTokenResponse(
            id=row.id,
            token_prefix=row.token_prefix,
            description=row.description,
            revoked_at=row.revoked_at,
            last_used_at=row.last_used_at,
            created_at=row.created_at,
        )
        for row in result.scalars().all()
    ]


@router.post(
    "/auth/sso/scim/tokens",
    response_model=ScimTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_scim_token(
    payload: ScimTokenCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> ScimTokenCreatedResponse:
    """Mint a new SCIM token for this tenant. tenant_admin only.

    The clear token is returned EXACTLY ONCE here; only its SHA-256 digest
    is stored, so it can never be retrieved again.
    """
    tenant_id = require_tenant_id(principal)
    clear_token = generate_scim_token()
    row = ScimToken(
        id=uuid7(),
        tenant_id=tenant_id,
        token_hash=hash_scim_token(clear_token),
        token_prefix=token_prefix(clear_token),
        description=payload.description,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return ScimTokenCreatedResponse(
        id=row.id,
        token=clear_token,
        token_prefix=row.token_prefix,
        description=row.description,
        revoked_at=row.revoked_at,
        last_used_at=row.last_used_at,
        created_at=row.created_at,
    )


@router.delete(
    "/auth/sso/scim/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_scim_token(
    token_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Revoke a SCIM token. tenant_admin only. A revoked token 401s."""
    require_tenant_id(principal)
    result = await session.execute(
        select(ScimToken).where(
            ScimToken.id == token_id,
            ScimToken.revoked_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="SCIM token not found",
        )
    row.revoked_at = datetime.now(tz=UTC)
    await session.flush()
