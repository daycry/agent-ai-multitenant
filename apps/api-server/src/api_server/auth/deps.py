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

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import structlog
from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.jwt import InvalidTokenError, decode_jwt
from api_server.auth.mfa.challenge_store import MfaChallengeStore
from api_server.auth.mfa.webauthn_challenge_store import WebauthnChallengeStore
from api_server.auth.rate_limit import RateLimiter
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.db.models import User, UserOrganizationMembership, UserRole
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
    # Hint from the `own` JWT claim (ADR 0074). NOT authoritative on its own —
    # `require_system_owner` re-verifies against the DB per request.
    is_system_owner: bool = False


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


def get_mfa_challenge_store(redis: Redis = Depends(get_redis)) -> MfaChallengeStore:
    """The Redis-backed store for interim MFA challenge tokens (task_08_09).

    Rides the same Redis client as the session store; the challenge token
    lives under its own key namespace and grants NO access on its own.
    """
    return MfaChallengeStore(redis)


def get_webauthn_challenge_store(redis: Redis = Depends(get_redis)) -> WebauthnChallengeStore:
    """The Redis-backed store for single-use WebAuthn ceremony challenges (task_08_10).

    Rides the same Redis client; the challenge bytes live under their own
    key namespaces (registration vs authentication) and are single-use.
    """
    return WebauthnChallengeStore(redis)


# ---------------------------------------------------------------------------
# Principal dependency — JWT + Redis session check
# ---------------------------------------------------------------------------
async def get_principal(
    authorization: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    sessions: SessionStore = Depends(get_session_store),
) -> AuthPrincipal:
    """Decode the JWT, verify the session id still exists in Redis,
    return the principal. 401 on any failure.

    For users with `is_system_admin=true`, an `X-Tenant-Id` request
    header overrides the JWT's `tid` claim. This lets a superadmin
    switch the tenant context per-request without re-issuing tokens —
    the admin-panel uses this to act on behalf of any tenant from the
    header's tenant picker. For non-admin users the header is ignored
    (the JWT is the only source of truth so tenants can't escape
    their own scope).
    """
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
    is_system_owner = bool(claims.get("own", False))

    # Superadmin tenant override via header. Non-admins can't use this
    # path — even if they send the header, we ignore it.
    if is_system_admin and x_tenant_id:
        try:
            tenant_id = UUID(x_tenant_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid X-Tenant-Id header (expected UUID)",
            ) from exc

    return AuthPrincipal(
        user_id=user_id,
        session_id=session_id,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
        is_system_owner=is_system_owner,
    )


async def _is_db_system_admin(user_id: UUID) -> bool:
    """Authoritative System Admin check against the DB (prod-09 task_prod09_04).

    The ``sys`` JWT claim is fixed at LOGIN and the session TTL is 24 h, so
    ``UPDATE users SET is_system_admin = false`` used to leave the degraded
    admin with a full cross-tenant, BYPASSRLS session for up to a day — with no
    way to end it (see the note in :meth:`SessionStore.revoke_user_sessions`:
    the per-user index only covers TENANT-scoped sessions, and an admin's
    session is tenant-less). Re-reading the flag per request is what makes the
    revocation immediate.

    Cheap: one indexed read by primary key on a global (un-RLSed) table, the
    same query ``/auth/me`` already runs on every page load. Uses the BYPASSRLS
    admin engine because ``users`` carries no ``tenant_id``.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User.is_system_admin).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return bool(result.scalar_one_or_none())


async def require_system_admin(
    principal: AuthPrincipal = Depends(get_principal),
) -> AuthPrincipal:
    """Gate an endpoint to System Admin only. 403 otherwise.

    TWO checks, in this order (task_prod09_04, authz-4):

      1. the ``sys`` claim — cheap, and it keeps a regular tenant user from ever
         causing a DB round-trip on the admin surface;
      2. ``users.is_system_admin`` re-read from the DB — the AUTHORITATIVE one.
         Without it a privilege retired in the database stayed alive inside every
         session already issued (24 h TTL), which is precisely the window an
         off-boarding is meant to close.

    Mirrors :func:`require_system_owner`, which has re-verified against the DB
    since ADR 0074 — the admin gate was the one left trusting its claim.
    """
    if not principal.is_system_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system admin role required",
        )
    if not await _is_db_system_admin(principal.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system admin privileges have been revoked",
        )
    return principal


async def _is_db_system_owner(user_id: UUID) -> bool:
    """Authoritative System Owner check against the DB (ADR 0074): the ``own``
    JWT claim is only a hint, so the córtex gate re-reads ``users.is_system_owner``
    per request — revoking ownership then takes effect immediately. Uses the
    BYPASSRLS admin engine because ``users`` is global (un-RLSed)."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        result = await session.execute(
            select(User.is_system_owner).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return bool(result.scalar_one_or_none())


async def require_system_owner(
    principal: AuthPrincipal = Depends(get_principal),
) -> AuthPrincipal:
    """Gate an endpoint to the System Owner (córtex F0, ADR 0074). 403 otherwise.

    Verified against the DB per request, NOT just the ``own`` claim."""
    if not await _is_db_system_owner(principal.user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="system owner role required",
        )
    return principal


# RETIRADA: `require_admin_or_owner` (2026-07-30)
# -----------------------------------------------
# La dependencia compuesta «System Admin OR System Owner» que el ADR 0074
# (decisión 4) previó para las superficies admin del owner existió desde
# entonces con CERO llamantes: ni un `Depends(require_admin_or_owner)` en todo
# `apps/`. Solo la referenciaban su propia definición, una línea de
# `tests/integration/test_cortex_f0_ownership.py` y la documentación.
#
# Código muerto en la superficie de AUTORIZACIÓN es el peor sitio para tenerlo:
# venía con test verde y docstring convincente, así que el siguiente que
# necesitase «admin o owner» lo habría cableado creyendo que estaba en uso y
# probado en producción.
#
# Y no hacía falta: el System Owner se crea en el bootstrap del PRIMER usuario
# junto con el flag de admin (`routers/auth.py`: `is_system_admin=is_first_user`
# **y** `is_system_owner=is_first_user`, con índice único parcial que lo hace
# singleton), así que el owner YA pasa por `require_system_admin`. La única
# situación que la compuesta cubría —owner sin ser admin— solo se alcanza con un
# UPDATE a mano en la base de datos.
#
# Si alguna vez hace falta de verdad, se reconstruye en cuatro líneas sobre
# `_is_db_system_admin` / `_is_db_system_owner`, que siguen aquí y sí tienen
# llamantes. Lo que no se debe reponer es una puerta sin endpoint.


# ---------------------------------------------------------------------------
# Tenant-scoped session dependency
# ---------------------------------------------------------------------------
_log = structlog.get_logger("api_server.auth.deps")

_AFTER_COMMIT_KEY = "_after_commit"


def schedule_after_commit(session: AsyncSession, factory: Callable[[], Awaitable[None]]) -> None:
    """Register a zero-arg coroutine factory to run AFTER this request's tenant
    session commits (see :func:`open_tenant_session`).

    Domain events must be published only once their triggering row is durable:
    publishing inline (before ``open_tenant_session`` commits on return) lets a
    fast consumer — the orchestrator — read the not-yet-committed row in
    ``_dispatch`` and silently skip it (root cause of the "consumer se atasca"
    symptom). Registering the publish here guarantees it fires post-commit.
    """
    session.info.setdefault(_AFTER_COMMIT_KEY, []).append(factory)


@asynccontextmanager
async def open_tenant_session(
    principal: AuthPrincipal,
) -> AsyncIterator[AsyncSession]:
    """Open an AsyncSession with `app.user_id` (and `app.tenant_id` when
    present) bound for its lifetime, so PostgreSQL RLS scopes every query.

    Two flavours of session, selected from the principal:

      - Regular tenant user: app_user (NOBYPASSRLS) — queries are
        filtered by RLS to rows whose tenant_id matches the JWT's
        tid. Writes go into that tenant.
      - Superadmin without tenant context (no JWT tid and no
        `X-Tenant-Id` header): migrations_user (BYPASSRLS) — reads
        return rows from all tenants. Writes that need
        `require_tenant_id` will 400 with a helpful message until
        the admin picks a tenant.
      - Superadmin with tenant context: app_user (NOBYPASSRLS) again,
        scoped to the picked tenant. The admin "acts as" that tenant
        for both reads and writes, which is what the per-tenant
        view in the admin-panel needs.

    Shared by the `get_tenant_session` FastAPI dependency and the
    WebSocket handlers in `routers/ws.py`, which cannot use FastAPI's
    dependency injection to obtain a tenant-scoped session.
    """
    # NOTE: PostgreSQL `SET LOCAL` is a utility command and does NOT
    # accept bound parameters via asyncpg's prepared-statement protocol
    # (it raises "syntax error at or near $1"). We use `set_config(...,
    # is_local := true)` instead, which IS a regular function call and
    # binds parameters cleanly while still applying transaction-scope.
    if principal.is_system_admin and principal.tenant_id is None:
        sessionmaker = get_admin_sessionmaker()
    else:
        sessionmaker = get_sessionmaker()

    async with sessionmaker() as session:
        async with session.begin():
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
        # The request transaction has COMMITTED here (the `session.begin()`
        # block exited without an exception — a route that raised would
        # propagate past this point and skip the callbacks). Run anything
        # registered via `schedule_after_commit` now, post-commit, so domain
        # events are published only once their row is durable. Best-effort: a
        # publish blip must never break the already-committed request.
        for factory in session.info.get(_AFTER_COMMIT_KEY, ()):
            try:
                await factory()
            except Exception as exc:  # - best-effort, never fail the request
                _log.warning("api_server.after_commit_failed", error=str(exc))


async def get_tenant_session(
    principal: AuthPrincipal = Depends(get_principal),
) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped AsyncSession for the request (RLS bound).

    Thin FastAPI-dependency wrapper over `open_tenant_session`; see that
    context manager for the engine-selection rules.
    """
    async with open_tenant_session(principal) as session:
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
# Tenant-scoped role helpers (Plan 06.8 task_06_8_01)
# ---------------------------------------------------------------------------
#
# Three FastAPI dependencies to gate endpoints by the JWT user's role in
# the active tenant. System admins always pass. The original helper
# lived in routers/tenant_settings.py as `_require_tenant_admin`; it's
# centralised here so every router uses the same predicate.
#
# Usage:
#
#     @router.post("/projects")
#     async def create_project(
#         principal: AuthPrincipal = Depends(require_tenant_admin),
#         session: AsyncSession = Depends(get_tenant_session),
#     ) -> ProjectResponse: ...
#
# `get_tenant_session` is still listed separately because FastAPI
# deduplicates the underlying `get_principal` call — both deps share
# the same principal in one request.


async def _load_active_membership(
    session: AsyncSession, user_id: UUID, tenant_id: UUID
) -> UserOrganizationMembership | None:
    """Return the active, non-deleted membership of `user_id` in
    `tenant_id`, or None."""
    result = await session.execute(
        select(UserOrganizationMembership).where(
            UserOrganizationMembership.user_id == user_id,
            UserOrganizationMembership.tenant_id == tenant_id,
            UserOrganizationMembership.is_active.is_(True),
            UserOrganizationMembership.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def require_tenant_member(
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> AuthPrincipal:
    """Gate to anyone with an active membership in the JWT's tenant.

    System admins always pass. Otherwise the JWT must carry a `tid`
    claim AND there must be an active, non-deleted membership row for
    `(user_id, tenant_id)`. 403 otherwise.

    Use this on **read** endpoints of tenant-scoped resources, and on
    write endpoints whose action is part of every member's day-to-day
    work (creating tasks, moving them across the kanban, commenting on
    plans).
    """
    if principal.is_system_admin:
        return principal
    if principal.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no active tenant context",
        )
    membership = await _load_active_membership(session, principal.user_id, principal.tenant_id)
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="user is not a member of this tenant",
        )
    return principal


async def require_tenant_admin(
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> AuthPrincipal:
    """Gate to `tenant_admin` role on the JWT's tenant. System admins pass.

    Use this on POST/PUT/DELETE of tenant-scoped resources (projects,
    agents, teams, MCP configs, KBs, tenant settings) so a regular
    `tenant_user` can't mutate them.
    """
    if principal.is_system_admin:
        return principal
    if principal.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no active tenant context",
        )
    membership = await _load_active_membership(session, principal.user_id, principal.tenant_id)
    if membership is None or membership.role != UserRole.TENANT_ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_admin role required",
        )
    return principal


async def principal_is_tenant_admin(session: AsyncSession, principal: AuthPrincipal) -> bool:
    """The non-raising counterpart of :func:`require_tenant_admin` — True if the
    principal is a system admin OR the active tenant's admin. For OPTIONAL overrides
    (e.g. the c1/T2 ``force`` escape hatch on an otherwise member-gated endpoint)."""
    if principal.is_system_admin:
        return True
    if principal.tenant_id is None:
        return False
    membership = await _load_active_membership(session, principal.user_id, principal.tenant_id)
    return membership is not None and membership.role == UserRole.TENANT_ADMIN.value


async def require_can_approve_plan(
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> AuthPrincipal:
    """Gate to roles allowed to approve plans (ADR 0079, Opción A).

    Accepts ``tenant_admin`` OR ``plan_approver`` (and system admins). Kept
    SEPARATE from ``require_tenant_admin`` on purpose: approving a plan is a
    delegable signature (segregation of duties), not full tenant administration,
    so a ``plan_approver`` can sign without gaining admin over everything else.
    """
    if principal.is_system_admin:
        return principal
    if principal.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="no active tenant context",
        )
    membership = await _load_active_membership(session, principal.user_id, principal.tenant_id)
    allowed = {UserRole.TENANT_ADMIN.value, UserRole.PLAN_APPROVER.value}
    if membership is None or membership.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_admin or plan_approver role required",
        )
    return principal


def require_tenant_role(
    role: UserRole,
) -> Callable[[AuthPrincipal, AsyncSession], Awaitable[AuthPrincipal]]:
    """Factory: build a FastAPI dependency that gates on a specific role.

    Use this for endpoints that need a role other than `tenant_admin`
    (e.g. exclusively `tenant_user`, for an action only regular members
    should perform). System admins always pass.

    For the common cases prefer the prebuilt `require_tenant_member`
    and `require_tenant_admin`.
    """

    async def _check(
        principal: AuthPrincipal = Depends(get_principal),
        session: AsyncSession = Depends(get_tenant_session),
    ) -> AuthPrincipal:
        if principal.is_system_admin:
            return principal
        if principal.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="no active tenant context",
            )
        membership = await _load_active_membership(session, principal.user_id, principal.tenant_id)
        if membership is None or membership.role != role.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{role.value} role required",
            )
        return principal

    return _check


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
