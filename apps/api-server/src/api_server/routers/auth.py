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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.deps import (
    AuthPrincipal,
    get_client_ip,
    get_principal,
    get_rate_limiter,
    get_session_store,
)
from api_server.auth.jwt import encode_jwt
from api_server.auth.passwords import hash_password, verify_password
from api_server.auth.rate_limit import RateLimiter
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.models import User
from api_server.db.session import get_sessionmaker
from api_server.schemas.auth import (
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    UserResponse,
)

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


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(payload: RegisterRequest) -> UserResponse:
    """Create a new user. Email uniqueness is enforced by the DB."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        user = User(
            id=uuid7(),
            email=payload.email.lower(),
            password_hash=hash_password(payload.password),
            full_name=payload.full_name,
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
@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    sessions: SessionStore = Depends(get_session_store),
) -> LoginResponse:
    """Verify credentials, mint a JWT + Redis session, return both."""
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

        # Generic 401 — never leak whether the email exists.
        if not user or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid email or password",
            )

        if not verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid email or password",
            )

        # Issue a session id and persist it in Redis with the same
        # TTL as the JWT — both expire together.
        session_id = uuid7()
        ttl_seconds = settings.jwt_expiration_minutes * 60
        await sessions.create(
            session_id,
            user_id=user.id,
            tenant_id=None,
            ttl_seconds=ttl_seconds,
        )

        token = encode_jwt(
            user_id=user.id,
            session_id=session_id,
            tenant_id=None,
            is_system_admin=user.is_system_admin,
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
