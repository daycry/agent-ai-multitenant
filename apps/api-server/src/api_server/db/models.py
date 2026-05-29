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
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

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
    # SSO (Plan 08 task_08_01)
    SSO_LOGIN = "sso.login"
    SSO_USER_PROVISIONED = "sso.user_provisioned"


class SSOProvider(enum.StrEnum):
    """Identity-provider families a tenant can configure (Plan 08).

    Phase A ships the generic ``oidc`` flow; the per-IdP templates of
    task_08_02 (Azure AD, Google, Okta, ...) are presets that all
    persist as ``provider='oidc'`` plus a stored issuer/scope set, so
    the column stays a small closed set. SAML lands in Phase B.
    """

    OIDC = "oidc"
    SAML = "saml"


# ---------------------------------------------------------------------------
# Organization (= tenant). The platform is multi-tenant at the org level.
# ---------------------------------------------------------------------------
class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    # Per-tenant hourly rate for the human cost calculation
    # (Plan 03 task_03_26). CLAUDE.md §6 mandates "tarifa única tenant".
    # NULL means "use the platform default" (50 €/h).
    hourly_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=10, scale=2), nullable=True
    )
    hourly_rate_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)

    # No ORM `memberships` relationship: tenant_id is NOT a formal FK
    # to organizations.id (the migration intentionally omits the
    # constraint so RLS policies cannot create circular dependencies
    # during bulk loads). Use explicit queries instead.

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

    # Memberships are reached via explicit queries (see endpoints in
    # task 00_11). Keeping the model graph thin avoids RLS / FK
    # coupling pitfalls that bit us in phase 0.

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

    # No ORM `user` / `organization` back-refs — see Organization /
    # User class comments. Resolve via explicit JOIN when needed.

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


# ---------------------------------------------------------------------------
# PlatformSetting — global, platform-wide configuration (spec §7.9).
#
# Deliberately NOT tenant-scoped: a platform setting is the same for
# everyone and a tenant cannot override it. `max_review_retries` is the
# first such setting (Plan 02 task_02_13). Write access is gated to the
# System Admin by db/platform_settings.py.
# ---------------------------------------------------------------------------
class PlatformSetting(Base, TimestampMixin):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    # The System Admin who last wrote this setting (NULL once they are
    # deleted — the setting itself outlives the user).
    updated_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"PlatformSetting(key={self.key!r})"


# ---------------------------------------------------------------------------
# Tenant-level settings (Plan 06.7 task_06_7_01)
# ---------------------------------------------------------------------------
class TenantSetting(Base):
    """Generic per-tenant key/value config table with category dimension.

    Replaces the "one column on organizations per feature" pattern.
    The registry of *known* (category, key) pairs lives in code
    (``api_server.settings_registry``); the DB stores only values
    the tenant has actually configured. Reads fall back to the
    registry's default when the row is missing.

    PK is ``(tenant_id, category, key)`` so two tenants can hold the
    same setting independently and a single tenant can hold many
    settings across categories.
    """

    __tablename__ = "tenant_settings"

    tenant_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        primary_key=True,
    )
    category: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"TenantSetting(tenant={self.tenant_id} {self.category}.{self.key})"


# ---------------------------------------------------------------------------
# SSOConfiguration — per-tenant enterprise SSO config (Plan 08 task_08_01)
# ---------------------------------------------------------------------------
class SSOConfiguration(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """Per-tenant OIDC (and, later, SAML) provider configuration.

    Multi-tenancy: tenant-scoped via :class:`TenantScopedMixin` + RLS
    (`tenant_isolation` policy in the migration). Tenant A's row is
    invisible to a session bound to tenant B — the database refuses to
    return it, so an OIDC login can never resolve another tenant's IdP.

    Secret handling (CLAUDE.md principle: no plaintext secrets in the
    DB). The OIDC ``client_secret`` is stored in EXACTLY ONE of two
    forms, never both, never in clear text:

      * ``client_secret_ref``: a Vault pointer (``vault:<mount>/data/...``)
        resolved at login time through the same VaultResolver the MCP
        layer uses (`api_server.auth.sso.secrets`). Preferred when Vault
        is wired (``API_SERVER_VAULT_TOKEN`` set).
      * ``client_secret_encrypted``: Fernet ciphertext (encrypted at
        rest with ``API_SERVER_SSO_ENCRYPTION_KEY``) for deployments
        without Vault. The plaintext only ever lives in memory during
        the token exchange.

    ``claim_mappings`` maps OIDC userinfo/ID-token claims onto local
    user fields, e.g. ``{"email": "email", "full_name": "name"}``. A
    missing key falls back to the OIDC standard claim of the same name.
    """

    __tablename__ = "sso_configurations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", name="uq_sso_config_tenant_provider"),
        Index(
            "ix_sso_configurations_tenant_enabled",
            "tenant_id",
            "enabled",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    provider: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'oidc'"))
    # Human-friendly label shown in the tenant's login picker.
    display_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # --- OIDC discovery + client identity ---
    # The IdP issuer URL; discovery hits `<issuer>/.well-known/openid-configuration`.
    issuer: Mapped[str] = mapped_column(String(512), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    # Exactly one of these holds the secret; the other is NULL. A CHECK
    # constraint in the migration enforces "never both, never plaintext".
    client_secret_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Space-free list of OIDC scopes; `openid` is always implied.
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("""'["openid", "email", "profile"]'::jsonb""")
    )
    # claim -> local user field mapping (see class docstring).
    claim_mappings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"SSOConfiguration(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"provider={self.provider!r}, enabled={self.enabled!r})"
        )


# ---------------------------------------------------------------------------
# Review sessions — persistence of `workers.review_runtime.ReviewSession`
# (Plan 06.5 task_06_5_01). The manager was in-memory; this table makes
# it durable across worker restarts.
# ---------------------------------------------------------------------------
class ReviewSession(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    __tablename__ = "review_sessions"

    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), nullable=False
    )
    # Serialized `ReviewRuntimeSpec` — full enough to re-hydrate the
    # session and re-issue signed URLs after a worker restart.
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'running'")
    )
    container_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    verdict: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(nullable=True)
    rerun_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    suspended_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ReviewSession(id={self.id!r}, plan={self.plan_id!r}, status={self.status!r})"


# ---------------------------------------------------------------------------
# Task audit events — append-only history of one task (Plan 06.5
# task_06_5_02). Mirrors `api_server.task_lifecycle.AuditEvent` 1:1.
# The append-only invariant is enforced by the repository (no UPDATE /
# DELETE paths), not by a DB trigger — same trade-off `AuditLog` makes.
# ---------------------------------------------------------------------------
class TaskAuditEvent(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    __tablename__ = "task_audit_events"

    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Free-form: "user:<uuid>", "agent:reviewer", "system:plan_runner".
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"TaskAuditEvent(task={self.task_id!r}, kind={self.kind!r}, at={self.at!r})"


__all__ = [
    "AuditAction",
    "AuditLog",
    "Organization",
    "PlatformSetting",
    "ReviewSession",
    "SSOConfiguration",
    "SSOProvider",
    "Session",
    "TaskAuditEvent",
    "TenantSetting",
    "User",
    "UserOrganizationMembership",
    "UserRole",
]
