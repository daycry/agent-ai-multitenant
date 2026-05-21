"""FastAPI dependencies for auth + tenant scoping.

The two key dependencies are:

  - `get_principal`         decode JWT + look up the server-side
                            session in Redis. 401 on any failure.
  - `get_tenant_session`    yields an AsyncSession with
                            `app.user_id` (and `app.tenant_id` when
                            present) bound for the request, so RLS
                            policies scope every query.

Endpoints that read tenant-scoped data MUST use `get_tenant_session`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.jwt import InvalidTokenError, decode_jwt
from api_server.auth.rate_limit import RateLimiter
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AuthPrincipal:
    """Decoded + validated context for the current request."""

    user_id: UUID
    session_id: UUID
    tenant_id: UUID | None
    is_system_admin: bool = False


def _parse_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


# ---------------------------------------------------------------------------
# Redis client (process-wide singleton)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_redis() -> Redis:
    """Lazy singleton Redis client. Tests reset via `reset_redis_cache()`."""
    settings = get_settings()
    client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return client


def reset_redis_cache() -> None:
    """Drop the cached Redis client so the next `get_redis()` call
    picks up a new REDIS_URL. Tests use this after monkey-patching
    env vars."""
    get_redis.cache_clear()


def get_session_store(redis: Redis = Depends(get_redis)) -> SessionStore:
    return SessionStore(redis)


def get_rate_limiter(redis: Redis = Depends(get_redis)) -> RateLimiter:
    return RateLimiter(redis)


# ---------------------------------------------------------------------------
# Principal dependency — JWT + Redis session check
# ---------------------------------------------------------------------------
async def get_principal(
    authorization: str | None = Header(default=None),
    sessions: SessionStore = Depends(get_session_store),
) -> AuthPrincipal:
    """Decode the JWT, verify the session id still exists in Redis,
    return the principal. 401 on any failure."""
    token = _parse_bearer(authorization)
    try:
        claims = decode_jwt(token)
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["sid"])
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token missing/invalid 'sub' or 'sid' claim",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    tenant_id: UUID | None = None
    if "tid" in claims and claims["tid"] is not None:
        try:
            tenant_id = UUID(claims["tid"])
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="token has invalid 'tid' claim",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    # Server-side check: a revoked session must surface immediately.
    if not await sessions.get(session_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="session has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    is_system_admin = bool(claims.get("sys", False))

    return AuthPrincipal(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


def require_system_admin(
    principal: AuthPrincipal = Depends(get_principal),
) -> AuthPrincipal:
    """Gate an endpoint to System Admin only. 403 otherwise."""
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system admin role required",
        )
    return principal


# ---------------------------------------------------------------------------
# Tenant-scoped session dependency
# ---------------------------------------------------------------------------
async def get_tenant_session(
    principal: AuthPrincipal = Depends(get_principal),
) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession with `app.user_id` (and `app.tenant_id` if
    the JWT carried one) bound for the lifetime of the request, so
    PostgreSQL RLS policies can scope every query."""
    # NOTE: PostgreSQL `SET LOCAL` is a utility command and does NOT
    # accept bound parameters via asyncpg's prepared-statement protocol
    # (it raises "syntax error at or near $1"). We use `set_config(...,
    # is_local := true)` instead, which IS a regular function call and
    # binds parameters cleanly while still applying transaction-scope.
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": str(principal.user_id)},
        )
        if principal.tenant_id is not None:
            await session.execute(
                text("SELECT set_config('app.tenant_id', :tid, true)"),
                {"tid": str(principal.tenant_id)},
            )
        yield session


async def get_admin_session(
    principal: AuthPrincipal = Depends(require_system_admin),
) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound to the BYPASSRLS admin engine.

    Used by /admin/* endpoints: System Admin sees and writes
    everything cross-tenant, including audit_log rows with
    tenant_id IS NULL.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        # app.user_id is still set so audit_log rows carry the actor.
        await session.execute(
            text("SELECT set_config('app.user_id', :uid, true)"),
            {"uid": str(principal.user_id)},
        )
        yield session


# ---------------------------------------------------------------------------
# Client IP — best-effort helper for rate limiting / audit
# ---------------------------------------------------------------------------
def get_client_ip(request: Request) -> str:
    """Pull the client IP out of the request. Prefers X-Forwarded-For
    (left-most entry) when present; falls back to the socket peer."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
