"""Pydantic schemas for public-API token management (Plan 13 task_13_02).

The Tenant Admin mints / lists / revokes per-tenant ``X-API-Token``
credentials through ``/auth/api-tokens`` (RBAC ``tenant_admin``, RLS
scoped to the caller's tenant). These schemas shape those request /
response bodies.

Secret handling (CLAUDE.md: no plaintext secrets, ever echoed twice). The
raw token is returned in the CREATE response EXACTLY ONCE
(:class:`ApiTokenCreatedResponse.token`) and never again — neither the
list nor any get response carries it. Only the SHA-256 digest reaches the
DB; the clear ``prefix`` (``<marker>_<id>``) is non-secret and surfaces in
listings so an operator can disambiguate tokens without revealing them.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from api_server.auth.api_tokens import DEFAULT_API_TOKEN_RATE_LIMIT
from api_server.db.models import ApiTokenScope


class ApiTokenCreateRequest(BaseModel):
    """Body for minting a new public-API token (tenant_admin).

    Only the operator-controlled lifecycle knobs are accepted; the secret
    and its hash are minted server-side. ``scopes`` defaults to read-only;
    ``rate_limit`` to the platform default per-minute budget.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    scopes: list[ApiTokenScope] = Field(default_factory=lambda: [ApiTokenScope.READ])
    expires_at: datetime | None = Field(default=None)
    rate_limit: int = Field(default=DEFAULT_API_TOKEN_RATE_LIMIT, ge=1)
    ip_allowlist: list[str] = Field(default_factory=list)

    @field_validator("scopes")
    @classmethod
    def _dedupe_non_empty_scopes(cls, value: list[ApiTokenScope]) -> list[ApiTokenScope]:
        """A token must carry at least one scope; drop duplicates, keep order."""
        if not value:
            raise ValueError("a token must have at least one scope")
        seen: set[ApiTokenScope] = set()
        ordered: list[ApiTokenScope] = []
        for scope in value:
            if scope not in seen:
                seen.add(scope)
                ordered.append(scope)
        return ordered


class ApiTokenResponse(BaseModel):
    """A public-API token's metadata — NEVER the secret.

    Surfaces the prefix/name/scopes/expiry/last_used/revoked an operator
    needs to manage the token; the raw token only ever appears in the
    CREATE response.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    prefix: str
    name: str
    scopes: list[str]
    rate_limit: int
    ip_allowlist: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiTokenCreatedResponse(ApiTokenResponse):
    """The mint response — carries the clear token EXACTLY ONCE.

    The clear ``token`` (``<prefix>_<secret>``) is returned only here, at
    creation time, and is never retrievable again (only its SHA-256 digest
    is stored).
    """

    token: str


__all__ = [
    "ApiTokenCreateRequest",
    "ApiTokenCreatedResponse",
    "ApiTokenResponse",
]
