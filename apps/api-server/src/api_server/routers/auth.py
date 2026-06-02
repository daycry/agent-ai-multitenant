"""`/auth/*` endpoints — register, login, logout, me.

The endpoints in this router are scoped to a single user, not to a
tenant. They open their own AsyncSession directly (without going
through `get_tenant_session`) because the user is, by definition,
not yet authenticated.

Login enforces a per-IP AND a per-email rate limit independently:
either tripping the threshold returns 429. This frustrates credential
stuffing (lots of emails from one IP) and password spraying (one email
from lots of IPs) at the same time.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_client_ip,
    get_mfa_challenge_store,
    get_principal,
    get_rate_limiter,
    get_session_store,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.mfa.challenge_store import MfaChallenge, MfaChallengeStore, new_challenge_token
from api_server.auth.mfa.store import user_mfa_methods
from api_server.auth.passwords import hash_password, verify_password
from api_server.auth.rate_limit import RateLimiter
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.models import Organization, User, UserOrganizationMembership
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker
from api_server.schemas.auth import (
    RESOLUTION_STATE_MULTIPLE,
    RESOLUTION_STATE_NO_ACCESS,
    RESOLUTION_STATE_SINGLE,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    ResolvedMembership,
    SelectTenantRequest,
    SessionResolutionResponse,
    UserResponse,
)
from api_server.schemas.mfa import MfaRequiredResponse

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_user_response(u: User) -> UserResponse:
    return UserResponse(
        id=u.id,
        email=u.email,
        full_name=u.full_name,
        is_system_admin=u.is_system_admin,
        is_active=u.is_active,
    )


async def _fetch_user_by_email(session: AsyncSession, email: str) -> User | None:
    """Look up a user by email. RLS is NOT involved (users is un-RLSed)."""
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _load_active_memberships(user_id: UUID) -> list[ResolvedMembership]:
    """Return the user's ACTIVE, non-deleted tenant memberships (ADR 0047).

    Tenant access is granted EXCLUSIVELY by ``UserOrganizationMembership``
    that the admin assigns AFTER login — no email-domain claiming, no
    auto-created membership (ADR 0047). This is the single source of truth
    for the post-login resolution: 0 rows → "no access", 1 → enter, >1 →
    picker.

    Runs on the BYPASSRLS admin engine because the caller's session has no
    active tenant yet (``app.tenant_id`` is unset), so the RLS policy on
    ``user_org_memberships`` would hide every row. Cross-tenant exposure is
    prevented by constraining the query to ``user_id = user_id`` — the
    user only ever sees their OWN memberships, never another user's.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(
                UserOrganizationMembership.tenant_id,
                UserOrganizationMembership.role,
                Organization.name.label("tenant_name"),
            )
            .join(
                Organization,
                Organization.id == UserOrganizationMembership.tenant_id,
            )
            .where(
                UserOrganizationMembership.user_id == user_id,
                UserOrganizationMembership.is_active.is_(True),
                UserOrganizationMembership.deleted_at.is_(None),
                # Only memberships into a live tenant count (a soft-deleted
                # organization grants no access).
                Organization.deleted_at.is_(None),
            )
            .order_by(Organization.name)
        )
        return [
            ResolvedMembership(
                tenant_id=row.tenant_id,
                tenant_name=row.tenant_name,
                role=row.role,
            )
            for row in result.all()
        ]


async def _is_active_member(user_id: UUID, tenant_id: UUID) -> bool:
    """True iff ``user_id`` has an ACTIVE, non-deleted membership in a live
    ``tenant_id`` (ADR 0047). The select-tenant endpoint's authorization
    check — a user can only activate a tenant they actually belong to."""
    memberships = await _load_active_memberships(user_id)
    return any(m.tenant_id == tenant_id for m in memberships)


async def _mint_tenant_session(
    sessions: SessionStore,
    *,
    user_id: UUID,
    tenant_id: UUID | None,
    is_system_admin: bool,
) -> LoginResponse:
    """Mint a session + JWT bound to ``tenant_id`` (or tenant-less if None).

    Shared by the password login, the post-login single-tenant
    auto-resolution, and the explicit tenant pick. The Redis session and
    the JWT share one TTL so they expire together; the JWT's ``tid`` claim
    is what ``get_principal`` reads to scope RLS for a REGULAR user (who,
    unlike a system admin, cannot override the tenant via ``X-Tenant-Id``).
    """
    settings = get_settings()
    session_id = uuid7()
    ttl_seconds = settings.jwt_expiration_minutes * 60
    await sessions.create(
        session_id,
        user_id=user_id,
        tenant_id=tenant_id,
        ttl_seconds=ttl_seconds,
    )
    token = encode_jwt(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )
    return LoginResponse(access_token=token, token_type="bearer", expires_in=ttl_seconds)


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: RegisterRequest) -> UserResponse:
    """Create a new user. Email uniqueness is enforced by the DB.

    First-user promotion: when the `users` table is empty (fresh
    install / dev bootstrap), the registered user is automatically
    flagged as `is_system_admin=true`. This is what gives the very
    first operator the cross-tenant superpowers wired in
    `auth/deps.py` (BYPASSRLS reads + `X-Tenant-Id` writes). All
    subsequent users default to `is_system_admin=false` and a
    superadmin must promote them via /admin/users if needed. The
    check + insert run inside the same transaction so a race
    between two simultaneous registers can never produce two
    superadmins.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        existing_users = await session.execute(select(User.id).limit(1))
        is_first_user = existing_users.scalar_one_or_none() is None

        user = User(
            id=uuid7(),
            email=payload.email.lower(),
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
            is_system_admin=is_first_user,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="email already registered",
            ) from exc
        # Re-read to grab DB-side defaults (is_active, is_system_admin).
        await session.refresh(user)
        return _to_user_response(user)


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=LoginResponse | MfaRequiredResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    sessions: SessionStore = Depends(get_session_store),
    challenges: MfaChallengeStore = Depends(get_mfa_challenge_store),
) -> LoginResponse | MfaRequiredResponse:
    """Verify credentials, then issue a session — OR, if the user has a
    confirmed TOTP factor, return an interim ``mfa_required`` challenge.

    A user WITHOUT a confirmed second factor logs in EXACTLY as before:
    the password check passes straight to a Redis session + JWT. A user
    WITH a confirmed TOTP factor gets NO session here — only a single-use,
    short-lived challenge token to complete at ``/auth/mfa/totp/verify``.
    """
    settings = get_settings()
    ip = get_client_ip(request)
    email = payload.email.lower()

    # Per-IP and per-email sliding windows. Both record the hit
    # whether or not the credentials end up matching — this prevents
    # an attacker from resetting the clock by cycling emails.
    allowed_ip, _ = await rate_limiter.check(
        f"rl:login:ip:{ip}",
        limit=settings.login_rate_limit_count,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    allowed_email, _ = await rate_limiter.check(
        f"rl:login:email:{email}",
        limit=settings.login_rate_limit_count,
        window_seconds=settings.login_rate_limit_window_seconds,
    )
    if not allowed_ip or not allowed_email:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts; retry later",
            headers={"Retry-After": str(settings.login_rate_limit_window_seconds)},
        )

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as db, db.begin():
        user = await _fetch_user_by_email(db, email)

        # Generic 401 — never leak whether the email exists, whether it's
        # inactive, or whether it is an SSO-only identity. An
        # SSO-provisioned user (Plan 08 task_08_07) has no usable local
        # password: its `password_hash` is a sentinel that is not a valid
        # argon2 encoding, so we MUST short-circuit here rather than feed
        # it to verify_password (which would raise on the bad hash). The
        # user logs in through their IdP instead.
        if not user or not user.is_active or user.is_sso_provisioned:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid email or password",
            )

        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid email or password",
            )

        user_id = user.id
        is_system_admin = user.is_system_admin

    # First factor passed. If the user has ANY confirmed second factor
    # (TOTP or WebAuthn), do NOT mint a session here — return an interim
    # challenge instead, advertising the methods the user can complete it
    # with. A user without MFA falls straight through to a session, exactly
    # as before. The check runs outside the login txn (it is a narrow,
    # tenant-agnostic existence probe on the admin role).
    methods = await user_mfa_methods(user_id)
    if methods:
        mfa_token = new_challenge_token()
        await challenges.create(
            mfa_token,
            MfaChallenge(user_id=user_id, tenant_id=None, is_system_admin=is_system_admin),
            ttl_seconds=settings.mfa_challenge_ttl_seconds,
        )
        return MfaRequiredResponse(mfa_token=mfa_token, mfa_methods=methods)

    # Issue a session id and persist it in Redis with the same
    # TTL as the JWT — both expire together.
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
        is_system_admin=is_system_admin,
    )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=ttl_seconds,
    )


# ---------------------------------------------------------------------------
# POST /auth/logout
# ---------------------------------------------------------------------------
@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: AuthPrincipal = Depends(get_principal),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Revoke the current session. Subsequent requests with the same
    JWT will be rejected (401) because the sid is gone from Redis."""
    await sessions.revoke(principal.session_id)


# ---------------------------------------------------------------------------
# GET /auth/session/resolve  (ADR 0047, task_sso_03)
# ---------------------------------------------------------------------------
@router.get("/session/resolve", response_model=SessionResolutionResponse)
async def resolve_session(
    principal: AuthPrincipal = Depends(get_principal),
    sessions: SessionStore = Depends(get_session_store),
) -> SessionResolutionResponse:
    """Resolve the tenant the authenticated identity may enter (ADR 0047).

    Called by the client RIGHT AFTER a successful login — password OR SSO,
    both produce the same tenant-less IDENTITY session — to turn the user's
    ACTIVE memberships into a typed next step:

      * **0 memberships** → ``state="no_access"``: the session stays valid
        (it proves identity) but the user has NO tenant; the admin-panel
        shows the "sin permisos, contacta al administrador" screen. NO
        token is minted and NO membership is auto-created (ADR 0047 —
        deny-by-default, access is granted only by an explicit admin
        assignment).
      * **1 membership** → ``state="single"``: a fresh TENANT-SCOPED token
        is minted and returned so the client enters that tenant directly.
      * **>1 memberships** → ``state="multiple"``: the client shows the
        tenant-picker and then POSTs ``/auth/session/select-tenant``.

    A System Admin is NOT special-cased here: this endpoint reports only
    real memberships. The superadmin's cross-tenant powers come from the
    ``X-Tenant-Id`` override + BYPASSRLS engine (``auth/deps.py``), not
    from this resolution — so an admin with no memberships still gets the
    portfolio view via the picker, exactly as today.
    """
    memberships = await _load_active_memberships(principal.user_id)

    if not memberships:
        return SessionResolutionResponse(
            state=RESOLUTION_STATE_NO_ACCESS,
            memberships=[],
        )

    if len(memberships) == 1:
        only = memberships[0]
        minted = await _mint_tenant_session(
            sessions,
            user_id=principal.user_id,
            tenant_id=only.tenant_id,
            is_system_admin=principal.is_system_admin,
        )
        return SessionResolutionResponse(
            state=RESOLUTION_STATE_SINGLE,
            memberships=memberships,
            access_token=minted.access_token,
            token_type=minted.token_type,
            expires_in=minted.expires_in,
        )

    return SessionResolutionResponse(
        state=RESOLUTION_STATE_MULTIPLE,
        memberships=memberships,
    )


# ---------------------------------------------------------------------------
# POST /auth/session/select-tenant  (ADR 0047, task_sso_03)
# ---------------------------------------------------------------------------
@router.post("/session/select-tenant", response_model=LoginResponse)
async def select_tenant(
    payload: SelectTenantRequest,
    principal: AuthPrincipal = Depends(get_principal),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Activate one of the user's tenants and mint a tenant-scoped token.

    The tenant-picker (``state="multiple"``) POSTs the chosen ``tenant_id``
    here. We re-assert an ACTIVE membership for ``(user_id, tenant_id)`` —
    the user can NEVER activate a tenant they don't belong to (a forged id
    returns 403) — then mint a fresh session + JWT carrying that tenant in
    its ``tid`` claim. This is the mechanism by which a REGULAR user (who
    cannot use the ``X-Tenant-Id`` superadmin override) acquires a
    tenant-scoped session.
    """
    if not await _is_active_member(principal.user_id, payload.tenant_id):
        # Same answer whether the tenant doesn't exist or the user simply
        # isn't a member — never reveal which.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no active membership in the requested tenant",
        )
    return await _mint_tenant_session(
        sessions,
        user_id=principal.user_id,
        tenant_id=payload.tenant_id,
        is_system_admin=principal.is_system_admin,
    )


# ---------------------------------------------------------------------------
# GET /auth/me
# ---------------------------------------------------------------------------
@router.get("/me", response_model=UserResponse)
async def me(
    principal: AuthPrincipal = Depends(get_principal),
) -> UserResponse:
    """Return the authenticated user's profile."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(select(User).where(User.id == principal.user_id))
        user = result.scalar_one_or_none()
        if user is None:
            # Token references a row that no longer exists. The Redis
            # session is still alive (otherwise get_principal would
            # have 401'd already), but the user got deleted — treat
            # the token as stale.
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="user no longer exists",
            )
        return _to_user_response(user)
