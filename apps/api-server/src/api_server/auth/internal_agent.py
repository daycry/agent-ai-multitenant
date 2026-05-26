"""Sandbox-scoped auth for the `/internal/agent/*` endpoints
(Plan 04.5 task_04_5_01).

The agent-runtime sandbox needs a way to call back into the api-server
during its loop — to recall memories, run RAG searches, persist new
memories. ADR 0012 says the sandbox holds no DB credentials and no
human JWT. So we mint a **purpose-specific bearer token** at the
moment the worker launches the container:

  - subject: `agent_id`
  - tenant: `tenant_id` (so RLS still works)
  - kind:   the literal string ``"agent"`` (so the human-JWT validator
            rejects this token, and vice versa)
  - exp:    24 h after issue (the sandbox's lifetime is far shorter
            but giving extra headroom avoids races at the boundary)

The middleware in this module validates the token, refuses any
non-`"agent"` JWT, loads the active `Agent` row to confirm it still
exists / isn't soft-deleted, and binds `agent_id` + `tenant_id` on
the request state. Endpoints under `/internal/agent/*` depend on
:func:`get_agent_principal` instead of `get_principal`.

Tests in `tests/integration/test_internal_agent_auth.py`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import _parse_bearer
from api_server.config import get_settings
from api_server.db.domain import Agent
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker

# Token TTL the worker mints with. Sandbox containers live far less
# than this (minutes), but giving slack avoids edge cases at handoff.
_AGENT_TOKEN_DEFAULT_TTL = timedelta(hours=24)

# The discriminator that separates agent tokens from human JWTs in
# the same signing key space. The human side never sets this claim;
# the agent side must.
_AGENT_KIND_CLAIM = "agent"


@dataclass(frozen=True)
class AgentPrincipal:
    """Decoded context for a request from the agent-runtime sandbox."""

    agent_id: UUID
    tenant_id: UUID
    # The raw token's `iat` so logs can correlate sandbox runs.
    issued_at: datetime


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------
def mint_agent_token(
    *,
    agent_id: UUID,
    tenant_id: UUID,
    ttl: timedelta | None = None,
) -> str:
    """Sign a short-lived bearer token for the agent-runtime sandbox.

    Called by the worker (Plan 04.5 task_04_5_03/04/05 integration)
    right before launching the container. The minted string is
    injected into the container as the ``AGENTIC_INTERNAL_TOKEN``
    env var.
    """
    settings = get_settings()
    now = datetime.now(tz=UTC)
    expires = now + (ttl or _AGENT_TOKEN_DEFAULT_TTL)
    claims: dict[str, Any] = {
        "sub": str(agent_id),
        "tid": str(tenant_id),
        "kind": _AGENT_KIND_CLAIM,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    encoded: str = jwt.encode(
        claims,
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    return encoded


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------
class InvalidAgentTokenError(Exception):
    """The token is malformed, expired, signed with the wrong key, or
    is a human JWT (missing ``kind=agent``)."""


def decode_agent_token(token: str) -> AgentPrincipal:
    """Validate a sandbox token + return the principal.

    Raises :class:`InvalidAgentTokenError` on any failure. Notable
    distinction: a token that decodes correctly but lacks the
    ``kind=agent`` claim is rejected — humans must not be able to
    use their JWTs against `/internal/agent/*`.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError as exc:
        raise InvalidAgentTokenError(str(exc)) from exc

    if claims.get("kind") != _AGENT_KIND_CLAIM:
        raise InvalidAgentTokenError("token is not an agent token")

    try:
        agent_id = UUID(claims["sub"])
        tenant_id = UUID(claims["tid"])
        issued_at = datetime.fromtimestamp(int(claims["iat"]), tz=UTC)
    except (KeyError, ValueError, TypeError) as exc:
        raise InvalidAgentTokenError("token missing/invalid sub/tid/iat claim") from exc

    return AgentPrincipal(agent_id=agent_id, tenant_id=tenant_id, issued_at=issued_at)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
async def get_agent_principal(
    authorization: str | None = Header(default=None),
) -> AgentPrincipal:
    """FastAPI dependency for `/internal/agent/*` routes.

    Two layers:
      1. JWT validation (signature + expiry + `kind=agent` claim).
      2. DB lookup of the `Agent` row to confirm it still exists and
         hasn't been soft-deleted between token mint and use. Goes
         through the *admin* (BYPASSRLS) sessionmaker because the
         agent token isn't bound to a human session — we need to
         look up agents across tenants by id alone, while still
         pinning the tenant_id from the token for the rest of the
         request.

    Returns the principal. Endpoints downstream pull `tenant_id`
    from it to set `app.tenant_id` on the RLS session.
    """
    token = _parse_bearer(authorization)
    try:
        principal = decode_agent_token(token)
    except InvalidAgentTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"invalid agent token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Defence in depth — a deleted agent shouldn't keep working until
    # the token expires.
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        if not await _agent_exists(session, principal.agent_id, principal.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="agent not found or revoked",
            )

    return principal


async def _agent_exists(session: AsyncSession, agent_id: UUID, tenant_id: UUID) -> bool:
    result = await session.execute(
        select(Agent.id).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none() is not None


async def get_agent_tenant_session(
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> AsyncIterator[AsyncSession]:
    """Yield a session with ``app.tenant_id`` bound to the agent's
    tenant.

    Used by `/internal/agent/*` endpoints that read or write
    tenant-scoped tables (memory_entries, knowledge_bases, ...).
    RLS does the cross-tenant isolation; we set the GUC so the
    NOBYPASSRLS app_user role honours it.

    Unlike :func:`api_server.auth.deps.get_tenant_session` we do NOT
    set ``app.user_id`` — the agent has no human user attached. Only
    the `sessions` table requires that GUC, and agents never touch it.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(principal.tenant_id)},
        )
        yield session


__all__ = [
    "AgentPrincipal",
    "InvalidAgentTokenError",
    "decode_agent_token",
    "get_agent_principal",
    "get_agent_tenant_session",
    "mint_agent_token",
]
