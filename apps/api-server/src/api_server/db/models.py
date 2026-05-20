"""Domain models for phase 0 (foundations).

Five tables make up the auth + multi-tenancy substrate:

- organizations          one row per tenant.
- users                  global; one user may belong to multiple tenants.
- user_org_memberships   M:N between users and organizations + per-tenant role.
- sessions               server-side session metadata (the actual cookie
                         payload lives in Redis; this table is for audit).
- audit_log              append-only log of sensitive actions.

Domain models for agents, projects, plans, etc. arrive in phase 1.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class UserRole(enum.StrEnum):
    """Per-membership role inside an organization.

    System-wide admin is a separate boolean on User (`is_system_admin`)
    and is independent of any org membership.
    """

    TENANT_ADMIN = "tenant_admin"
    TENANT_USER = "tenant_user"
    SYSTEM_OPERATOR = "system_operator"


class AuditAction(enum.StrEnum):
    """Stable identifiers for high-level audit events.

    Extend by adding members; never rename existing ones — historical
    rows still reference the old string value.
    """

    USER_REGISTERED = "user.registered"
    USER_LOGIN = "user.login"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGOUT = "user.logout"
    SESSION_REVOKED = "session.revoked"
    TENANT_CREATED = "tenant.created"
    TENANT_UPDATED = "tenant.updated"
    TENANT_DELETED = "tenant.deleted"
    MEMBERSHIP_GRANTED = "membership.granted"
    MEMBERSHIP_REVOKED = "membership.revoked"


# ---------------------------------------------------------------------------
# Organization (= tenant). The platform is multi-tenant at the org level.
# ---------------------------------------------------------------------------
class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    memberships: Mapped[list[UserOrganizationMembership]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Organization(id={self.id!r}, slug={self.slug!r})"


# ---------------------------------------------------------------------------
# User (global). Email + Argon2id password hash. `is_system_admin` grants
# cross-tenant access; everything else is granted via memberships.
# ---------------------------------------------------------------------------
class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    last_login_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    memberships: Mapped[list[UserOrganizationMembership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"User(id={self.id!r}, email={self.email!r})"


# ---------------------------------------------------------------------------
# UserOrganizationMembership (M:N + role). Tenant-scoped.
# ---------------------------------------------------------------------------
class UserOrganizationMembership(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    __tablename__ = "user_org_memberships"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "tenant_id",
            name="uq_membership_user_tenant",
        ),
        Index("ix_membership_tenant_user", "tenant_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(
        primaryjoin="UserOrganizationMembership.tenant_id == Organization.id",
        foreign_keys="UserOrganizationMembership.tenant_id",
        back_populates="memberships",
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"UserOrganizationMembership("
            f"user_id={self.user_id!r}, tenant_id={self.tenant_id!r}, role={self.role!r})"
        )


# ---------------------------------------------------------------------------
# Session — audit metadata for server-side sessions. The cookie payload
# itself lives in Redis (sub-100ms reads); this table is for traceability
# and revocation history.
# ---------------------------------------------------------------------------
class Session(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_active", "user_id", "revoked_at"),)

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: a session may exist before the user picks an active tenant.
    tenant_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_active_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"Session(id={self.id!r}, user_id={self.user_id!r})"


# ---------------------------------------------------------------------------
# AuditLog — append-only. tenant_id can be NULL when a System Admin
# performs a cross-tenant action.
# ---------------------------------------------------------------------------
class AuditLog(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_log_action_created", "action", "created_at"),
    )

    tenant_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[UUID | None] = mapped_column(nullable=True)
    # Free-form structured detail of the change. Reads via JSON path
    # operators; writes via JSONB.
    changes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"AuditLog(id={self.id!r}, action={self.action!r})"


__all__ = [
    "AuditAction",
    "AuditLog",
    "Organization",
    "Session",
    "User",
    "UserOrganizationMembership",
    "UserRole",
]
