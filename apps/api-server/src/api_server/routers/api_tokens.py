"""Tenant-Admin CRUD for public-API tokens (Plan 13 task_13_02).

``/auth/api-tokens`` — the Tenant Admin mints, lists and revokes the
per-tenant ``X-API-Token`` credentials that authenticate the public REST
API ``/api/v1`` (Plan 13 Decisiones Clave: the token grants access SCOPED
to its own tenant only; it travels in the HEADER, never a query param).
The public v1 endpoints (Phase B) and the resolving middleware (task_13_03)
are separate; this router is the operator-facing management surface.

These endpoints are JWT-authenticated and gated on the ``tenant_admin``
role (``require_tenant_admin``); they run on a tenant-scoped RLS session
(``get_tenant_session``), so a Tenant Admin can only ever see / revoke
their OWN tenant's tokens — the ``@pytest.mark.cross_tenant`` test pins
this. Mirrors the SCIM token-management pattern (Plan 08 task_08_08,
``routers/scim.py``).

Secret handling (CLAUDE.md: no plaintext secrets, never echoed twice). On
create the raw token is returned EXACTLY ONCE; only its SHA-256 digest is
persisted, so it can never be retrieved again. The list / never reveal the
secret — only the clear ``prefix`` for disambiguation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.api_token_auth import invalidate_api_token_cache
from api_server.auth.api_tokens import generate_api_token
from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.models import ApiToken
from api_server.routers._helpers import require_tenant_id
from api_server.schemas.api_tokens import (
    ApiTokenCreatedResponse,
    ApiTokenCreateRequest,
    ApiTokenResponse,
)

router = APIRouter(prefix="/auth/api-tokens", tags=["api-tokens"])


@router.get("", response_model=list[ApiTokenResponse])
async def list_api_tokens(
    _principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ApiTokenResponse]:
    """List this tenant's public-API tokens — NEVER the secret. tenant_admin.

    Shows prefix / name / scopes / expiry / last_used / revoked. RLS scopes
    the result to the caller's tenant, so a tenant never sees another
    tenant's tokens.
    """
    result = await session.execute(select(ApiToken).order_by(ApiToken.created_at.desc()))
    return [ApiTokenResponse.model_validate(row) for row in result.scalars().all()]


@router.post(
    "",
    response_model=ApiTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_api_token(
    payload: ApiTokenCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> ApiTokenCreatedResponse:
    """Mint a new public-API token for this tenant. tenant_admin only.

    The clear token is returned EXACTLY ONCE here; only its SHA-256 digest
    is stored (plus the clear ``prefix`` for listings), so the secret can
    never be retrieved again.
    """
    tenant_id = require_tenant_id(principal)
    minted = generate_api_token()
    row = ApiToken(
        id=uuid7(),
        tenant_id=tenant_id,
        token_hash=minted.token_hash,
        prefix=minted.prefix,
        name=payload.name,
        scopes=[scope.value for scope in payload.scopes],
        expires_at=payload.expires_at,
        rate_limit=payload.rate_limit,
        ip_allowlist=payload.ip_allowlist,
        created_by=principal.user_id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return ApiTokenCreatedResponse(
        **ApiTokenResponse.model_validate(row).model_dump(),
        token=minted.token,
    )


@router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(
    token_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> None:
    """Revoke a public-API token. tenant_admin only.

    Soft-revoke (sets ``revoked_at``); the row stays for audit and a
    revoked token authenticates nothing thereafter. RLS scopes the lookup
    to the caller's tenant, so a tenant cannot revoke another tenant's
    token (it 404s, never silently succeeds).

    Invalidates the X-API-Token resolution cache (task_13_03) so the
    revoked token stops authenticating IMMEDIATELY rather than after the
    cache TTL ages out.
    """
    require_tenant_id(principal)
    result = await session.execute(
        select(ApiToken).where(
            ApiToken.id == token_id,
            ApiToken.revoked_at.is_(None),
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API token not found",
        )
    row.revoked_at = datetime.now(tz=UTC)
    await session.flush()
    # Evict the cached resolution (keyed by the token's SHA-256 digest) so
    # the revocation is effective now, not after the short TTL.
    await invalidate_api_token_cache(row.token_hash, redis)
