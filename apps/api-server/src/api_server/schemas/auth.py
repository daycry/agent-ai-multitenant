"""Pydantic request/response schemas for the /auth/* endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Payload for POST /auth/register."""

    email: EmailStr
    # Phase-0 minimum: 8 chars. Phase 1 will plug a stronger policy
    # (zxcvbn-style) behind the same shape.
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    """Payload for POST /auth/login."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class LoginResponse(BaseModel):
    """Result of a successful login."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until the JWT expires.")


class UserResponse(BaseModel):
    """User shape returned by /auth/register and /auth/me."""

    id: UUID
    email: EmailStr
    full_name: str | None
    is_system_admin: bool
    is_active: bool
