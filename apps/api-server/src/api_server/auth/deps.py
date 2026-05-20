"""FastAPI dependencies for auth + tenant scoping.

The key dependency is `get_tenant_session`:

  1. Extract the JWT from the Authorization: Bearer header.
  2. Decode it (raises 401 on failure).
  3. Open an async SQLAlchemy session inside a transaction.
  4. Emit `SET LOCAL app.user_id = '<uuid>'` and (if present)
     `SET LOCAL app.tenant_id = '<uuid>'`. PostgreSQL RLS policies
     consume those settings to scope every subsequent query.
  5. Yield the session to the endpoint.
  6. On exit, the transaction commits (or rolls back on exception)
     and the SET LOCAL values are discarded automatically.

Endpoints that read tenant-scoped data MUST use this dependency;
otherwise queries will simply see zero rows (which is the safer
failure mode but obscures the bug).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.jwt import InvalidTokenError, decode_jwt
from api_server.db.session import get_sessionmaker


@dataclass(frozen=True)
class AuthPrincipal:
    """Decoded JWT context for the current request."""

    user_id: UUID
    tenant_id: UUID | None


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


async def get_principal(
    authorization: str | None = Header(default=None),
) -> AuthPrincipal:
    """Decode the JWT and return its principal. 401 on any failure."""
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
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token missing/invalid 'sub' claim",
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

    return AuthPrincipal(user_id=user_id, tenant_id=tenant_id)


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
