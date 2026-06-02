"""Pydantic schemas for /admin/* endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------
class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")


class TenantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    is_active: bool | None = None


class TenantResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


# ---------------------------------------------------------------------------
# Users (cross-tenant listing)
# ---------------------------------------------------------------------------
class UserListItem(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str | None
    is_system_admin: bool
    is_active: bool


# ---------------------------------------------------------------------------
# User memberships (System Admin manages user↔tenant + role) — ADR 0047
# ---------------------------------------------------------------------------
# The closed set of per-membership roles, mirroring db.models.UserRole. The
# admin assigns one of these when granting a tenant; `system_admin` is a
# separate global flag on the user, never a membership role.
_MEMBERSHIP_ROLES = ("tenant_admin", "tenant_user", "system_operator")


class MembershipResponse(BaseModel):
    """One user↔tenant membership row, enriched with the tenant's name/slug
    so the /admin/users UI can render it without a second lookup."""

    id: UUID
    user_id: UUID
    tenant_id: UUID
    tenant_name: str
    tenant_slug: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class MembershipCreateRequest(BaseModel):
    """Assign a user to a tenant with a role (ADR 0047)."""

    tenant_id: UUID
    role: str = Field(pattern=r"^(tenant_admin|tenant_user|system_operator)$")


class MembershipUpdateRequest(BaseModel):
    """Change a membership's role and/or active state. Both optional; at
    least one must be present (enforced in the handler)."""

    role: str | None = Field(default=None, pattern=r"^(tenant_admin|tenant_user|system_operator)$")
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------
class ServiceHealth(BaseModel):
    name: str
    status: str  # "ok" | "degraded" | "down"
    detail: str | None = None


class SystemHealthResponse(BaseModel):
    status: str
    services: list[ServiceHealth]
