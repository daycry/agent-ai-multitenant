"""Pydantic request/response schemas for the /auth/* endpoints."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# Single source of truth for password length bounds. Login and register
# share these so a credential that can be *registered* can always be
# *submitted* at login (no off-by-one validation gap — api-routers-validation-7).
# Phase-0 minimum: 8 chars. Phase 1 will plug a stronger policy
# (zxcvbn-style) behind the same shape.
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class RegisterRequest(BaseModel):
    """Payload for POST /auth/register."""

    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
    full_name: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    """Payload for POST /auth/login.

    Length bounds match :class:`RegisterRequest` (api-routers-validation-7).
    A 1-char password could never have been registered, so accepting it
    at login only widened the brute-force surface and the error-message
    inconsistency. Validation here is a cheap pre-filter; the real check
    is the constant-time hash comparison in the auth router.
    """

    email: EmailStr
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


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


# ---------------------------------------------------------------------------
# Post-login tenant resolution by membership (ADR 0047, task_sso_03)
# ---------------------------------------------------------------------------
# After a successful login (SSO OR password) the session proves IDENTITY
# only — no active tenant. The client calls GET /auth/session/resolve to
# learn which tenant(s) the user may enter, derived EXCLUSIVELY from their
# ACTIVE `UserOrganizationMembership` rows (no email-domain claiming, no
# auto-created membership). The typed `state` drives the UI:
#
#   - "no_access" → the "sin permisos, contacta al administrador" screen
#                   (the session stays valid; the user just has no tenant).
#   - "single"    → exactly one tenant; the response carries a freshly
#                   minted TENANT-SCOPED token so the client enters directly.
#   - "multiple"  → the tenant-picker lets the user choose; the client then
#                   POSTs /auth/session/select-tenant to activate one.
#   - "admin"     → a System Admin with NO membership; enters in PORTFOLIO
#                   mode (no active tenant) with the tenant-less identity
#                   token, switching tenant from the header picker. Never the
#                   no-access screen — that would be a chicken-and-egg lockout
#                   (they could not reach /admin/users to grant a membership).

# Typed resolution states (string literals so the JSON is self-describing
# and the admin-panel can switch on them without a magic number).
RESOLUTION_STATE_NO_ACCESS = "no_access"
RESOLUTION_STATE_SINGLE = "single"
RESOLUTION_STATE_MULTIPLE = "multiple"
RESOLUTION_STATE_ADMIN = "admin"


class ResolvedMembership(BaseModel):
    """One tenant the authenticated user may enter (active membership)."""

    tenant_id: UUID
    tenant_name: str
    role: str


class SessionResolutionResponse(BaseModel):
    """Typed post-login tenant resolution (ADR 0047, task_sso_03).

    `state` is one of `no_access` / `single` / `multiple` / `admin`.
    `memberships` lists every ACTIVE tenant membership (empty iff `no_access`
    or `admin`). For the `single` state ONLY, `access_token` carries a
    tenant-scoped JWT minting the active tenant so the client can enter
    without a second round-trip; for the other states it is `None` and the
    client shows the no-access screen, the picker, or — for `admin` — enters
    the portfolio view with the tenant-less identity token it already holds.
    """

    state: str
    memberships: list[ResolvedMembership]
    access_token: str | None = None
    token_type: str | None = None
    expires_in: int | None = Field(
        default=None, description="Seconds until the JWT expires (only when access_token is set)."
    )


class SelectTenantRequest(BaseModel):
    """Payload for POST /auth/session/select-tenant — the picker's choice."""

    tenant_id: UUID
