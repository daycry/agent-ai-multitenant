"""X-API-Token authentication for the public REST API (Plan 13 task_13_03).

The public ``/api/v1`` surface (Phase B) is authenticated by a per-tenant
``X-API-Token`` HEADER (Plan 13 Decisiones Clave: header, NEVER a query
param; the token grants access SCOPED to its own tenant only). This is a
SEPARATE auth path from the interactive JWT/session auth in
:mod:`api_server.auth.deps` — it does not read the ``Authorization``
header, mint a Redis session or touch local/SSO/MFA login.

Flow for a presented token:

  1. Read the ``X-API-Token`` header (missing -> 401).
  2. Resolve it to its tenant. The request is unauthenticated until the
     hash is matched, so the lookup runs ONCE on the BYPASSRLS admin role
     (``WHERE token_hash = :digest``). The token is high-entropy random
     and stored only as a SHA-256 digest (see
     :mod:`api_server.auth.api_tokens`), so the lookup is an equality
     probe on the ``uq_api_token_hash`` index.
  3. Validate lifecycle: not revoked, not past ``expires_at`` (401).
  4. Validate the source IP against ``ip_allowlist`` when non-empty
     (403 when the caller's IP is outside every CIDR).
  5. Establish a TENANT-SCOPED principal + session so every downstream
     ``/api/v1`` query runs under that tenant's RLS (``app.tenant_id``
     bound), guaranteeing a tenant-A token can never see tenant-B data.

Caching (Plan 13 Alcance: "avoid a DB hit per request"). The
token-hash -> tenant resolution is cached in Redis under a short TTL
(:attr:`Settings.api_token_cache_ttl_seconds`). A second request with the
same token is served from the cache, no DB round-trip. Two staleness
guarantees keep a cached entry honest:

  * Revocation invalidates the cache EXPLICITLY: the Tenant-Admin DELETE
    deletes the cache key (:func:`invalidate_api_token_cache`), so a
    revoked token stops authenticating immediately, not after the TTL.
  * The short TTL is the worst-case ceiling if that explicit invalidation
    is ever missed (e.g. a token revoked directly in the DB). ``expires_at``
    is cached alongside the tenant, so an expired token is rejected from
    the cache too without waiting for the entry to age out.

Only a VALID (live, unexpired) resolution is ever cached — an unknown /
revoked / expired token is never written, so the cache can never make a
dead token look alive.

``last_used_at`` is bumped on the DB resolution (cache miss) only — it is
observability, not on the hot path (the model docstring says as much), and
the short cache TTL keeps it fresh enough.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import ip_address, ip_network
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.api_tokens import hash_api_token
from api_server.auth.deps import get_client_ip, get_rate_limiter, get_redis
from api_server.auth.rate_limit import RateLimiter
from api_server.config import get_settings
from api_server.db.models import ApiToken
from api_server.db.session import get_admin_sessionmaker, get_sessionmaker

# Redis key namespace for the token-hash -> tenant resolution cache. The
# SHA-256 digest (never the clear token) is the key suffix, so the cache
# holds no plaintext secret either.
_CACHE_PREFIX = "apitoken:"

# Redis key namespace for the per-token sliding-window rate-limit counter
# (task_13_04). Keyed by the token id (a UUID, not the secret), so each
# token has its OWN budget and one token's traffic never throttles another.
# Because token ids are unique across tenants, this also gives per-tenant
# isolation for free.
_RATE_LIMIT_PREFIX = "apitoken:rl:"

# Standard rate-limit response headers (the de-facto ``X-RateLimit-*`` set
# plus ``Retry-After`` on a 429), emitted on every public-API response.
_HDR_LIMIT = "X-RateLimit-Limit"
_HDR_REMAINING = "X-RateLimit-Remaining"
_HDR_RESET = "X-RateLimit-Reset"
_HDR_RETRY_AFTER = "Retry-After"


def _cache_key(token_hash: str) -> str:
    return f"{_CACHE_PREFIX}{token_hash}"


def _rate_limit_key(token_id: UUID) -> str:
    return f"{_RATE_LIMIT_PREFIX}{token_id}"


# ---------------------------------------------------------------------------
# Principal — the tenant context resolved from an X-API-Token
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ApiTokenPrincipal:
    """The tenant context an authenticated ``X-API-Token`` resolves to.

    The token IS the tenant context: there is no user behind a public-API
    call. ``scopes`` carries the coarse capabilities
    (:class:`api_server.db.models.ApiTokenScope`) the v1 endpoints (Phase B)
    will gate writes on. ``tenant_id`` is what binds the RLS session.
    ``rate_limit`` is the token's own per-minute request budget (the
    ``api_tokens.rate_limit`` column), carried here so the sliding-window
    limiter (task_13_04) applies the right budget with no extra DB hit — it
    rides the same cache as the tenant resolution.
    """

    tenant_id: UUID
    token_id: UUID
    scopes: tuple[str, ...]
    rate_limit: int


class ApiTokenAuthError(Exception):
    """Raised when an ``X-API-Token`` cannot authenticate.

    ``status_code`` is 401 for a missing / unknown / expired / revoked
    token and 403 when a known token is presented from an IP outside its
    allowlist (the credential is valid, the network location is not).
    """

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Resolution + cache
# ---------------------------------------------------------------------------
async def _resolve_from_db(digest: str) -> ApiTokenPrincipal:
    """Resolve a token digest to its tenant via the BYPASSRLS admin role.

    The request is unauthenticated until the digest is matched, so there
    is no ``app.tenant_id`` to scope by yet — the lookup runs on the admin
    (BYPASSRLS) engine. Validates lifecycle (revoked / expired) and bumps
    ``last_used_at`` as a best-effort side effect in the same txn. Raises
    :class:`ApiTokenAuthError` (401) on any failure.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        result = await session.execute(select(ApiToken).where(ApiToken.token_hash == digest))
        row = result.scalar_one_or_none()
        if row is None:
            raise ApiTokenAuthError(status.HTTP_401_UNAUTHORIZED, "invalid API token")
        now = datetime.now(tz=UTC)
        if not row.is_active(now=now):
            # Revoked or past expiry — authenticates nothing.
            raise ApiTokenAuthError(status.HTTP_401_UNAUTHORIZED, "API token revoked or expired")
        row.last_used_at = now
        return ApiTokenPrincipal(
            tenant_id=row.tenant_id,
            token_id=row.id,
            scopes=tuple(row.scopes),
            rate_limit=row.rate_limit,
        )


def _serialize(principal: ApiTokenPrincipal) -> str:
    return json.dumps(
        {
            "tenant_id": str(principal.tenant_id),
            "token_id": str(principal.token_id),
            "scopes": list(principal.scopes),
            "rate_limit": principal.rate_limit,
        }
    )


def _deserialize(raw: str) -> ApiTokenPrincipal:
    parsed = json.loads(raw)
    return ApiTokenPrincipal(
        tenant_id=UUID(parsed["tenant_id"]),
        token_id=UUID(parsed["token_id"]),
        scopes=tuple(parsed["scopes"]),
        rate_limit=int(parsed["rate_limit"]),
    )


async def resolve_api_token(token: str, redis: Redis) -> ApiTokenPrincipal:
    """Resolve a clear ``X-API-Token`` to its :class:`ApiTokenPrincipal`.

    Cache-first: a hit on ``apitoken:<digest>`` returns the cached tenant
    with no DB round-trip. On a miss the token is resolved against the DB
    (BYPASSRLS), validated (revoked / expired -> 401) and — only when valid
    — cached under the short TTL. A revoked / expired / unknown token is
    never cached, so the cache cannot keep a dead token alive.
    """
    digest = hash_api_token(token)
    key = _cache_key(digest)
    cached = await redis.get(key)
    if cached is not None:
        return _deserialize(cached)
    principal = await _resolve_from_db(digest)
    ttl = get_settings().api_token_cache_ttl_seconds
    await redis.set(key, _serialize(principal), ex=ttl)
    return principal


async def invalidate_api_token_cache(token_hash: str, redis: Redis) -> None:
    """Drop a token's cached resolution so a revocation takes effect now.

    Called by the Tenant-Admin revoke endpoint (``DELETE
    /auth/api-tokens/{id}``) so a revoked token stops authenticating
    immediately rather than after the cache TTL ages out. Best-effort: if
    the key is already gone (TTL elapsed) the delete is a harmless no-op.
    """
    await redis.delete(_cache_key(token_hash))


# ---------------------------------------------------------------------------
# IP allowlist
# ---------------------------------------------------------------------------
def _ip_allowed(client_ip: str, allowlist: list[str]) -> bool:
    """True iff ``client_ip`` falls in any CIDR of a non-empty allowlist.

    An empty allowlist means "any source IP" (returns True). A malformed
    client IP or a list with no matching CIDR is rejected. Each entry is
    parsed as a network (``strict=False`` so a bare host like ``1.2.3.4``
    is accepted as a /32, the way an operator would type it).
    """
    if not allowlist:
        return True
    try:
        candidate = ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if candidate in ip_network(entry, strict=False):
                return True
        except ValueError:
            # A malformed allowlist entry never silently widens access.
            continue
    return False


async def _enforce_ip_allowlist(principal: ApiTokenPrincipal, client_ip: str) -> None:
    """403 when the token has an allowlist and ``client_ip`` is outside it.

    The allowlist lives on the token row, not in the cache, so this reads
    it under the resolved tenant's RLS scope. A token with no allowlist is
    reachable from any IP.
    """
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(principal.tenant_id)},
        )
        result = await session.execute(
            select(ApiToken.ip_allowlist).where(ApiToken.id == principal.token_id)
        )
        allowlist = result.scalar_one_or_none() or []
    if not _ip_allowed(client_ip, list(allowlist)):
        raise ApiTokenAuthError(status.HTTP_403_FORBIDDEN, "source IP not in the token's allowlist")


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------
async def get_api_token_principal(
    request: Request,
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    redis: Redis = Depends(get_redis),
) -> ApiTokenPrincipal:
    """FastAPI dependency: the tenant resolved from the ``X-API-Token`` header.

    Reads the token from the HEADER only (Plan 13 Decisiones Clave: never a
    query param), resolves it (cache-first), then enforces the IP
    allowlist. Raises :class:`HTTPException` 401 (missing / invalid /
    expired / revoked) or 403 (IP not in the token's allowlist).
    """
    if not x_api_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing X-API-Token header",
        )
    try:
        principal = await resolve_api_token(x_api_token, redis)
        await _enforce_ip_allowlist(principal, get_client_ip(request))
    except ApiTokenAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return principal


def _set_rate_limit_headers(
    response: Response, *, limit: int, remaining: int, reset_at: int
) -> None:
    """Attach the standard ``X-RateLimit-*`` headers to a response."""
    response.headers[_HDR_LIMIT] = str(limit)
    response.headers[_HDR_REMAINING] = str(remaining)
    response.headers[_HDR_RESET] = str(reset_at)


async def enforce_api_token_rate_limit(
    response: Response,
    principal: ApiTokenPrincipal = Depends(get_api_token_principal),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> ApiTokenPrincipal:
    """Enforce the token's per-token sliding-window rate limit (task_13_04).

    Counts one hit per request in a Redis sliding window keyed by the
    token id, against the token's OWN ``rate_limit`` budget (carried on the
    principal, so no extra DB hit). Each token has its own budget — one
    token's traffic never throttles another, and since token ids are unique
    across tenants this isolates tenants too.

    On every call the standard ``X-RateLimit-Limit / -Remaining / -Reset``
    headers are set on the response. The (limit+1)th request inside the
    window raises HTTP 429 with a ``Retry-After`` plus the same headers; once
    the window slides past the early hits, requests succeed again.

    The v1 endpoints (Phase B) depend on this; it also re-exports the
    resolved principal so an endpoint can depend on this alone to get both
    the rate-limit gate and the tenant context.
    """
    settings = get_settings()
    result = await rate_limiter.check_with_headers(
        _rate_limit_key(principal.token_id),
        limit=principal.rate_limit,
        window_seconds=settings.api_token_rate_limit_window_seconds,
    )
    _set_rate_limit_headers(
        response,
        limit=result.limit,
        remaining=result.remaining,
        reset_at=result.reset_at,
    )
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API token rate limit exceeded",
            headers={
                _HDR_RETRY_AFTER: str(result.retry_after),
                _HDR_LIMIT: str(result.limit),
                _HDR_REMAINING: str(result.remaining),
                _HDR_RESET: str(result.reset_at),
            },
        )
    return principal


@asynccontextmanager
async def open_api_token_session(
    principal: ApiTokenPrincipal,
) -> AsyncIterator[AsyncSession]:
    """Open an app-role (NOBYPASSRLS) session bound to the token's tenant.

    Binds ``app.tenant_id`` for the transaction's lifetime (via
    ``set_config(..., is_local := true)``) so PostgreSQL RLS scopes every
    query to the token's tenant — a tenant-A token can never read or write
    tenant-B rows. Mirrors :func:`api_server.auth.deps.open_tenant_session`
    but takes its tenant from the resolved API-token principal, not a JWT.
    """
    async with get_sessionmaker()() as session, session.begin():
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(principal.tenant_id)},
        )
        yield session


async def get_api_token_session(
    principal: ApiTokenPrincipal = Depends(get_api_token_principal),
) -> AsyncIterator[AsyncSession]:
    """Yield a tenant-scoped (RLS-bound) session for a public-API request.

    Thin FastAPI-dependency wrapper over :func:`open_api_token_session`;
    the v1 endpoints (Phase B) depend on this so their queries run under
    the token's tenant.
    """
    async with open_api_token_session(principal) as session:
        yield session


__all__ = [
    "ApiTokenAuthError",
    "ApiTokenPrincipal",
    "enforce_api_token_rate_limit",
    "get_api_token_principal",
    "get_api_token_session",
    "invalidate_api_token_cache",
    "open_api_token_session",
    "resolve_api_token",
]
