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

SIGNING KEY (prod-09 task_prod09_03, secrets-9). These tokens are signed with
``settings.internal_token_secret`` — a secret DEDICATED to the worker→api
channel, NOT the ``jwt_secret`` that signs human sessions. They used to share one
key, which meant the workers container (which legitimately holds the minting
secret) could also mint a System-Admin session for any user id: the `kind=agent`
claim separated the two families only as long as every verifier remembered to
check it. Separate keys make cross-domain forgery impossible by construction
rather than by discipline. Operationally the workers container must receive
``API_SERVER_INTERNAL_TOKEN_SECRET`` (same value as the api-server) and no longer
needs ``API_SERVER_JWT_SECRET`` at all.

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
from api_server.db.domain import Agent, Project
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
    # The task this run is executing, when the worker minted the token for a
    # task dispatch (Plan 06.17 task_06_17_13 / ADR 0054). It lets the internal
    # endpoints resolve the EFFECTIVE project_id (``task.project_id``) for a
    # GLOBAL agent so it can read the RAG/memory of the project it is working
    # on. ``None`` for tokens minted without a task (chat, ad-hoc, legacy) — the
    # endpoints then keep the strict ``agent.project_id`` behaviour. The
    # project is NEVER trusted from the token: the endpoints look the task up
    # server-side and validate ``task.tenant_id == principal.tenant_id``.
    task_id: UUID | None = None


# ---------------------------------------------------------------------------
# Mint
# ---------------------------------------------------------------------------
def mint_agent_token(
    *,
    agent_id: UUID,
    tenant_id: UUID,
    task_id: UUID | None = None,
    ttl: timedelta | None = None,
) -> str:
    """Sign a short-lived bearer token for the agent-runtime sandbox.

    Called by the worker (Plan 04.5 task_04_5_03/04/05 integration)
    right before launching the container. The minted string is
    injected into the container as the ``AGENTIC_INTERNAL_TOKEN``
    env var.

    Plan 06.17 task_06_17_13 / ADR 0054: when the run executes a project
    task, the worker passes the ``task_id`` so the token carries the run's
    task context (claim ``task``). The internal endpoints use it to resolve
    the EFFECTIVE project_id for a global agent (``task.project_id``) — always
    re-validating ``task.tenant_id == tenant_id`` server-side, never trusting a
    project from the client. ``None`` (chat / ad-hoc / legacy mints) keeps the
    claim absent and the endpoints fall back to the strict ``agent.project_id``.
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
    if task_id is not None:
        claims["task"] = str(task_id)
    encoded: str = jwt.encode(
        claims,
        # DEDICATED key, never `jwt_secret` (task_prod09_03 / secrets-9).
        settings.internal_token_secret.get_secret_value(),
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

    Since task_prod09_03 a human JWT does not even reach that claim check: it is
    signed with ``jwt_secret`` and verified here against
    ``internal_token_secret``, so it fails at the SIGNATURE. The ``kind`` check
    stays as defence in depth (and to keep the error message honest).
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            # DEDICATED key, never `jwt_secret` (task_prod09_03 / secrets-9).
            settings.internal_token_secret.get_secret_value(),
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

    # Optional task context (Plan 06.17 task_06_17_13). A malformed ``task``
    # claim is rejected (it is a security-relevant pointer); its absence is
    # normal (chat / legacy mints) and leaves ``task_id`` None.
    task_id: UUID | None = None
    raw_task = claims.get("task")
    if raw_task is not None:
        try:
            task_id = UUID(str(raw_task))
        except (ValueError, TypeError) as exc:
            raise InvalidAgentTokenError("token has an invalid task claim") from exc

    return AgentPrincipal(
        agent_id=agent_id, tenant_id=tenant_id, issued_at=issued_at, task_id=task_id
    )


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
         hasn't been soft-deleted between token mint and use, AND —
         when the agent is bound to a project (`project_id IS NOT
         NULL`) — that the parent `Project` row still exists and is
         not soft-deleted. Soft-deleting a project must immediately
         revoke its agents' tokens even though the 24h TTL is far
         from over (audit gid auth-rbac-casbin-5). Goes through the
         *admin* (BYPASSRLS) sessionmaker because the agent token
         isn't bound to a human session — we need to look up agents
         across tenants by id alone, while still pinning the
         tenant_id from the token for the rest of the request.

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

    # Defence in depth — a deleted agent (or an agent whose project
    # has been soft-deleted) shouldn't keep working until the token
    # expires.
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        if not await _agent_exists(session, principal.agent_id, principal.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="agent not found or revoked",
            )

    return principal


async def _agent_exists(session: AsyncSession, agent_id: UUID, tenant_id: UUID) -> bool:
    """True iff the agent is live AND its project (if any) is live.

    The base check is unchanged: the agent row must exist, belong to
    the token's tenant, and not be soft-deleted. On top of that, a
    `project_local` agent carries a `project_id`; we LEFT JOIN the
    parent `Project` and require it to be present and not soft-deleted.
    Global agents (`project_id IS NULL`) have no project to validate,
    so the outer join leaves the project columns NULL and the OR below
    accepts them. This makes soft-deleting a project revoke its agents'
    tokens at once (audit gid auth-rbac-casbin-5).
    """
    result = await session.execute(
        select(Agent.id)
        .outerjoin(
            Project,
            (Project.id == Agent.project_id) & (Project.tenant_id == Agent.tenant_id),
        )
        .where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
            # No project bound -> nothing to validate. Project bound ->
            # it must resolve to a live (non-soft-deleted) row.
            (Agent.project_id.is_(None))
            | ((Project.id.isnot(None)) & (Project.deleted_at.is_(None))),
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
