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
# System health
# ---------------------------------------------------------------------------
class ServiceHealth(BaseModel):
    name: str
    status: str  # "ok" | "degraded" | "down"
    detail: str | None = None


class SystemHealthResponse(BaseModel):
    status: str
    services: list[ServiceHealth]
