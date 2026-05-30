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
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    LargeBinary,
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


class ApiTokenScope(enum.StrEnum):
    """What a public-API token (``X-API-Token``) is allowed to do (Plan 13).

    Coarse-grained, tenant-scoped capabilities — every scope is implicitly
    bounded to the token's own ``tenant_id`` (Plan 13 Decisiones Clave:
    the token grants access SCOPED to its own tenant only). A token carries
    a list of these (``ApiToken.scopes``); at minimum ``read``.

      * ``read``  — list/get the tenant's resources via ``/api/v1/...``.
      * ``write`` — create/update/delete the tenant's resources.

    Extend by adding members; never rename existing ones — persisted token
    rows still reference the old string value.
    """

    READ = "read"
    WRITE = "write"


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

    # Per-tenant gate for the conversational personal assistant (Plan 10
    # task_10_14). DEFAULT false: the feature is opt-in. When false, every
    # Tenant Admin of this tenant is denied (403) — the assistant simply
    # does not exist for them. The tenant-level assistant *identity*
    # (name/avatar/tone/language/system_prompt/enabled-tools) lives in
    # ``tenant_settings`` under the ``assistant`` category, not here.
    personal_assistant_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

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
    # True when the row was materialised by an SSO (OIDC/SAML) first login
    # via JIT provisioning (Plan 08 task_08_07). Such a user has NO usable
    # local password — `password_hash` holds a sentinel that no plaintext
    # can produce — and `POST /auth/login` rejects it with the generic 401
    # *before* it ever feeds the sentinel to the argon2 verifier.
    is_sso_provisioned: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
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
    # The IdP's stable identifier for this user within THIS tenant (SCIM
    # `externalId`, Plan 08 task_08_08). SCIM externalId is scoped to the
    # provisioning domain (the tenant's IdP), so it lives on the
    # tenant-scoped membership, not the global `users` row. NULL for users
    # not provisioned via SCIM (local register, OIDC/SAML JIT).
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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

    # --- OIDC discovery + client identity (NULL on a `saml` row) ---
    # The IdP issuer URL; discovery hits `<issuer>/.well-known/openid-configuration`.
    # Nullable since Phase B (migration 0033): a SAML row has no OIDC issuer.
    # The per-provider CHECK constraint requires them for `oidc` rows only.
    issuer: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    # --- IdP group -> tenant role mapping (Plan 08 task_08_11,
    # migration 0039). A ``{idp_group: tenant_role}`` object applied on
    # every SSO login (OIDC + SAML): the user's membership role is set to
    # the highest-privilege role any of their asserted groups maps to. An
    # empty object (the default) leaves the JIT default ``tenant_user``
    # untouched. Only the per-tenant roles ``tenant_admin`` /
    # ``tenant_user`` are honoured — a group can NEVER grant a platform
    # role (``system_admin`` is a `users` boolean; ``system_operator`` is
    # ignored here), see ``auth.sso.group_mapping``.
    group_role_mappings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- Login discovery: email-domain -> tenant/SSO (Plan 08
    # task_08_12, migration 0040). An operator-attested list of email
    # domains this config claims (e.g. ``["acme.com", "acme.io"]``). The
    # public ``GET /auth/discover`` endpoint maps an email's domain to the
    # enabled config that lists it, so the login UI can route the user to
    # their IdP. Stored lower-cased; matching is case-insensitive. NOT
    # globally unique across tenants (domains are attested, not verified) —
    # discovery resolves any collision to the oldest-created config.
    email_domains: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # --- SAML 2.0 identity provider (NULL on an `oidc` row; Plan 08
    # task_08_04, migration 0033). The per-provider CHECK constraint
    # requires entity_id + sso_url + x509_cert for `saml` rows.
    # The IdP's SAML EntityID — the `Issuer` it stamps on assertions.
    idp_entity_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # The IdP Single-Sign-On endpoint the SP AuthnRequest redirects to.
    idp_sso_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # The IdP signing certificate (PEM/base64) — verifies the assertion.
    idp_x509_cert: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The requested NameID format (defaults to emailAddress).
    name_id_format: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        server_default=text("'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'"),
    )
    # SAML attribute name -> local user field mapping (mirrors
    # `claim_mappings` for OIDC). Empty dict falls back to the NameID for
    # email and a best-effort common attribute set for the full name.
    attribute_mappings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # --- SAML SP signing / encryption (Plan 08 task_08_05, migration
    # 0034). The SP public cert is not secret (the IdP needs it) ->
    # plaintext PEM/base64. The SP private key follows the SAME never-
    # plaintext, exactly-one-source rule as the OIDC client secret:
    # a Vault pointer OR Fernet ciphertext, never both. CHECK constraints
    # enforce "at most one key source" and "a key+cert exist whenever any
    # signing/encryption feature is enabled".
    sp_x509_cert: Mapped[str | None] = mapped_column(Text, nullable=True)
    sp_private_key_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    sp_private_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-config security policy flags (map 1:1 onto python3-saml settings).
    # Sign the outbound AuthnRequest with the SP key.
    authn_requests_signed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Require the IdP to sign the assertion (defaults true — turning it
    # off is a deliberate, audited choice). task_08_04 hard-coded this;
    # it is now an operator-controllable column.
    want_assertions_signed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # Require the IdP to encrypt the assertion / the NameID to the SP cert
    # (the SP decrypts with its private key).
    want_assertions_encrypted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    want_name_id_encrypted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"SSOConfiguration(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"provider={self.provider!r}, enabled={self.enabled!r})"
        )


# ---------------------------------------------------------------------------
# ScimToken — per-tenant SCIM 2.0 bearer credential (Plan 08 task_08_08)
# ---------------------------------------------------------------------------
class ScimToken(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """A bearer token an IdP uses to provision users into a tenant via SCIM.

    Multi-tenancy: tenant-scoped via :class:`TenantScopedMixin` + RLS
    (the ``tenant_isolation`` policy in the migration). The token is what
    identifies the calling tenant — the SCIM endpoints are not behind the
    JWT/session auth (an IdP has no interactive session), so the token
    *is* the tenant context. Resolution runs once on the BYPASSRLS role to
    map ``token_hash`` -> ``tenant_id``; every subsequent SCIM query then
    runs under ``app.tenant_id`` bound to that tenant, so a token issued
    for tenant A can never read or write tenant B's users.

    Secret handling (CLAUDE.md: no plaintext secrets in the DB). The token
    is shown to the operator EXACTLY ONCE at mint time and never stored in
    clear: only its SHA-256 ``token_hash`` is persisted. A SHA-256 (not a
    salted argon2 hash) is used deliberately — the token is a long,
    high-entropy random value, so a single deterministic digest is both
    safe against brute force and supports the equality lookup the
    unauthenticated SCIM request needs (a salted hash could not be looked
    up by value). ``token_prefix`` keeps the first few characters in clear
    so the UI can disambiguate multiple tokens without revealing them.
    """

    __tablename__ = "scim_tokens"
    __table_args__ = (
        # The SHA-256 digest is globally unique (it identifies a tenant on
        # an unauthenticated request); a UNIQUE index also makes the
        # by-hash lookup an index probe.
        UniqueConstraint("token_hash", name="uq_scim_token_hash"),
        Index(
            "ix_scim_tokens_tenant_active",
            "tenant_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    # SHA-256 hex digest (64 chars) of the bearer token. Never the token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # First characters of the token, kept in clear for UI disambiguation.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # Operator-supplied label ("Okta production", ...).
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Set when the token is revoked — a revoked token authenticates nothing.
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Bumped on each successful SCIM call (observability; not on the hot path).
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ScimToken(id={self.id!r}, tenant={self.tenant_id!r}, prefix={self.token_prefix!r})"


# ---------------------------------------------------------------------------
# ApiToken — per-tenant credential for the public REST API (Plan 13
# task_13_01). Authenticates the ``X-API-Token`` header on ``/api/v1/...``.
# ---------------------------------------------------------------------------
class ApiToken(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """A per-tenant credential for the public REST API (``X-API-Token``).

    Multi-tenancy: tenant-scoped via :class:`TenantScopedMixin` + RLS (the
    ``tenant_isolation`` policy in the migration). The token IS the tenant
    context — the public ``/api/v1`` endpoints are not behind the
    JWT/session auth, so a presented ``X-API-Token`` is resolved once on
    the BYPASSRLS role to map ``token_hash`` -> ``tenant_id``; every
    subsequent v1 query then runs under ``app.tenant_id`` bound to that
    tenant (Plan 13 Decisiones Clave: the token grants access SCOPED to
    its own tenant only), so a token issued for tenant A can never read or
    write tenant B's data.

    Secret handling (CLAUDE.md: no plaintext secrets in the DB). The raw
    token is a long, high-entropy random value shown to the Tenant Admin
    EXACTLY ONCE at creation and never stored in clear: only its SHA-256
    ``token_hash`` is persisted. SHA-256 (not a salted argon2 hash) is used
    deliberately — the token is high-entropy, so a single deterministic
    digest is both safe against brute force and supports the equality
    lookup the unauthenticated request needs. ``prefix`` keeps the leading
    clear ``<marker>_<id>`` segment so listings can disambiguate tokens
    without revealing them. See :mod:`api_server.auth.api_tokens` for the
    mint/hash/verify helpers.

    Lifecycle controls:

      * ``scopes``       — coarse tenant-scoped capabilities
        (:class:`ApiTokenScope`); at minimum ``read``.
      * ``expires_at``   — optional vigencia; a token past it authenticates
        nothing. NULL = never expires.
      * ``revoked_at``   — soft-revoke; a revoked token authenticates
        nothing and stays for audit.
      * ``rate_limit``   — per-minute request budget (default
        ``DEFAULT_API_TOKEN_RATE_LIMIT``), enforced by the sliding-window
        limiter (task_13_04).
      * ``ip_allowlist`` — optional list of CIDRs; when non-empty, requests
        from a source IP outside every CIDR are rejected.
      * ``created_by``   — the user (Tenant Admin) who minted it.
      * ``last_used_at`` — bumped on use (observability; not on the hot
        path).
    """

    __tablename__ = "api_tokens"
    __table_args__ = (
        # The SHA-256 digest is globally unique (it identifies a tenant on
        # an unauthenticated request); a UNIQUE index also makes the
        # by-hash lookup an index probe.
        UniqueConstraint("token_hash", name="uq_api_token_hash"),
        Index(
            "ix_api_tokens_tenant_active",
            "tenant_id",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    # SHA-256 hex digest (64 chars) of the raw token. Never the token.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Leading clear ``<marker>_<id>`` segment, kept for UI listings.
    prefix: Mapped[str] = mapped_column(String(32), nullable=False)
    # Operator-supplied label ("CI pipeline", "Grafana export", ...).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Coarse tenant-scoped capabilities (values of :class:`ApiTokenScope`).
    # Always at least ``["read"]``.
    scopes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("""'["read"]'::jsonb""")
    )
    # Optional vigencia. NULL = never expires; past this instant the token
    # authenticates nothing.
    expires_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Per-minute request budget enforced by the sliding-window limiter.
    rate_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("100"))
    # Optional CIDR allowlist. Empty = any source IP. When non-empty, a
    # request from an IP outside every CIDR is rejected.
    ip_allowlist: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Tenant Admin who minted the token (kept for audit even if the user is
    # later removed from the tenant, hence ondelete=SET NULL + nullable).
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Bumped on each successful API call (observability; not on the hot path).
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Set when the token is revoked — a revoked token authenticates nothing.
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def is_active(self, *, now: datetime) -> bool:
        """True iff the token can authenticate at instant ``now``.

        A token is active when it is neither revoked nor past its
        ``expires_at`` (a NULL ``expires_at`` never expires). ``now`` must
        be timezone-aware to compare against the TZ-aware columns.
        """
        if self.revoked_at is not None:
            return False
        return not (self.expires_at is not None and self.expires_at <= now)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"ApiToken(id={self.id!r}, tenant={self.tenant_id!r}, prefix={self.prefix!r})"


# ---------------------------------------------------------------------------
# UserMfaTotp — per-user, per-tenant TOTP second-factor enrollment
# (Plan 08 task_08_09)
# ---------------------------------------------------------------------------
class UserMfaTotp(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """A user's TOTP (RFC 6238) second-factor enrollment within a tenant.

    MFA is an OPT-IN second factor added ALONGSIDE the existing auth: a
    user with no confirmed row here logs in EXACTLY as before. Only a user
    with ``confirmed_at IS NOT NULL`` is challenged for a 6-digit TOTP code
    after the password/SSO step succeeds.

    Tenant-scoped via :class:`TenantScopedMixin` + RLS (the
    ``tenant_isolation`` policy in the migration): enrollment, confirmation
    and recovery-code consumption all run under ``app.tenant_id`` bound to
    the active tenant, so one tenant's MFA state is invisible to another.
    There is at most one enrollment per ``(tenant_id, user_id)`` (a UNIQUE
    constraint); re-enrolling overwrites the unconfirmed secret.

    Secret handling (CLAUDE.md: no plaintext secrets in the DB):

      * ``secret_encrypted`` — the base32 TOTP seed, Fernet-encrypted at
        rest with the SAME ``API_SERVER_SSO_ENCRYPTION_KEY`` mechanism the
        OIDC client secret uses. The plaintext seed only ever lives in
        memory during enrollment-URI generation and code verification.
      * ``recovery_codes`` — a JSON array of one-time recovery codes, each
        stored ONLY as its SHA-256 hex digest (never the clear code). A
        code is consumed by removing its digest from the array, so each
        works exactly once.
    """

    __tablename__ = "user_mfa_totp"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_mfa_totp_tenant_user"),
        Index("ix_mfa_totp_tenant_user", "tenant_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Fernet ciphertext of the base32 TOTP seed. Never the clear seed.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Set when the user proves possession by submitting a valid code once.
    # NULL = enrollment started but not yet confirmed; such a row does NOT
    # gate login (the user can still get in with just their password).
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # One-time recovery codes, each stored as a SHA-256 hex digest only.
    # Consuming a code removes its digest from this array.
    recovery_codes: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"UserMfaTotp(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"user={self.user_id!r}, confirmed={self.confirmed_at is not None})"
        )


# ---------------------------------------------------------------------------
# WebauthnCredential — per-user, per-tenant WebAuthn/FIDO2 second factor
# (Plan 08 task_08_10)
# ---------------------------------------------------------------------------
class WebauthnCredential(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """A registered WebAuthn (FIDO2 / passkey) authenticator for a user.

    A SECOND alternative to TOTP in the SAME opt-in MFA challenge flow: a
    user with no row here logs in EXACTLY as before; a user with at least
    one credential is challenged after the password/SSO step and completes
    login by signing a WebAuthn assertion instead of typing a TOTP code.

    Tenant-scoped via :class:`TenantScopedMixin` + RLS (the
    ``tenant_isolation`` policy in the migration): registration and
    authentication run under ``app.tenant_id`` bound to the active tenant,
    so one tenant's credentials are invisible to another. A user may
    register several authenticators within a tenant, so the row is NOT
    unique per ``(tenant_id, user_id)`` — only the credential id is unique
    (a WebAuthn credential id is globally unique by construction).

    Nothing stored here is a secret (CLAUDE.md): a WebAuthn credential
    persists only the PUBLIC key (the private key never leaves the
    authenticator), the public credential id, and the signature counter.
    The counter is the anti-cloning control: each successful assertion must
    present a counter strictly greater than the stored one, so a captured
    (replayed) assertion with a stale counter is rejected and the credential
    is treated as compromised.
    """

    __tablename__ = "webauthn_credentials"
    __table_args__ = (
        # The WebAuthn credential id is globally unique by construction; a
        # UNIQUE index also makes the by-credential-id lookup an index probe
        # and prevents the same authenticator registering twice.
        UniqueConstraint("credential_id", name="uq_webauthn_credential_id"),
        Index("ix_webauthn_tenant_user", "tenant_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Raw WebAuthn credential id (the authenticator's public handle). Stored
    # as bytes; surfaced to the browser base64url-encoded.
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # COSE-encoded PUBLIC key used to verify assertion signatures. NOT a
    # secret — the matching private key never leaves the authenticator.
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Signature counter from the last accepted assertion. A new assertion
    # must present a strictly greater counter (unless the authenticator does
    # not implement counters, i.e. both are 0) — this defeats cloned-token
    # replay.
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    # Optional operator/user label ("YubiKey 5", "MacBook Touch ID", ...).
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Transports advertised by the authenticator (usb, nfc, ble, internal),
    # echoed back in allowCredentials so the browser prompts the right way.
    transports: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Set when the registration ceremony completes. A registration is only
    # ever persisted on a verified attestation, so this is non-NULL for every
    # stored row; it exists for symmetry with the TOTP factor and to gate
    # login uniformly ("a confirmed factor exists").
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Bumped on each successful assertion (observability; not on the hot path).
    last_used_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"WebauthnCredential(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"user={self.user_id!r}, sign_count={self.sign_count!r})"
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


# ---------------------------------------------------------------------------
# Incoming webhooks (Plan 13 Fase C, task_13_08) — INBOUND: an external tool
# (GitHub/Jira/Sentry/...) POSTs an event we VERIFY (the inverse of Plan 10's
# OUTGOING signing). A config + its received events are tenant + PROJECT
# scoped + RLS — an event for project A never acts on tenant B.
# ---------------------------------------------------------------------------
class IncomingWebhookConfig(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """A per-project incoming-webhook endpoint config (Plan 13 task_13_08).

    One row configures how a SPECIFIC external origin (``github`` / ``jira``
    / ...) may POST events into ONE project. The ``{config_id}`` in the public
    URL ``/webhooks/incoming/{origin}/{config_id}`` resolves to this row, and
    THROUGH it to the row's ``tenant_id`` + ``project_id`` — so the resolution
    is what binds an inbound event to exactly one project's tenant (an event
    for project A can never act on tenant B).

    Multi-tenancy (CLAUDE.md principle 1): tenant-scoped via
    :class:`TenantScopedMixin` + the ``tenant_isolation`` RLS policy, AND
    additionally project-scoped (``project_id`` NOT NULL, FK
    ``ON DELETE CASCADE``). Resolving the config on the PUBLIC endpoint runs
    once on the BYPASSRLS role (the request is unauthenticated until the HMAC
    is verified); the secret it returns only ever validates a signature for
    THIS config's own project/tenant.

    Secret handling (CLAUDE.md: no plaintext secrets, never echoed/logged). The
    HMAC signing secret is stored ONLY as Fernet ciphertext
    (``signing_secret_encrypted``), encrypted at rest with the webhook
    encryption key (see :mod:`api_server.webhooks.secrets`). It is resolved in
    memory to verify a signature and is never returned by any API.
    """

    __tablename__ = "incoming_webhook_configs"
    __table_args__ = (
        Index(
            "ix_incoming_webhook_configs_tenant_project",
            "tenant_id",
            "project_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "origin IN ('github', 'gitlab', 'jira', 'sentry', 'linear', 'generic')",
            name="ck_incoming_webhook_configs_origin",
        ),
    )

    # The project this config feeds — the SECOND tenancy axis (with tenant_id).
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # External sender family (values of webhooks.IncomingWebhookOrigin); selects
    # the per-origin signature scheme. Stored as the URL path segment.
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    # Operator label ("CI on acme/api", "Sentry prod", ...).
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Fernet ciphertext of the HMAC signing secret — NEVER the clear value.
    signing_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # A disabled config rejects every event (404) without touching the secret.
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # Bumped on each successfully VERIFIED event (observability; not hot-path).
    last_event_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"IncomingWebhookConfig(id={self.id!r}, tenant={self.tenant_id!r}, "
            f"project={self.project_id!r}, origin={self.origin!r})"
        )


class IncomingWebhookEvent(Base, UUIDPrimaryKeyMixin, TenantScopedMixin):
    """One received incoming-webhook event, recorded for replay (Plan 13).

    Persisted AFTER the HMAC signature is verified (the verification gate runs
    BEFORE any expensive work). Stores the raw body + the headers needed to
    re-derive the signature so the event can be replayed for debugging
    (task_13_12). Append-only by convention (no UPDATE/DELETE path), like
    :class:`TaskAuditEvent`.

    Multi-tenancy: tenant-scoped via :class:`TenantScopedMixin` + RLS, and
    carries ``config_id`` + ``project_id`` so a listing / replay is scoped to
    the owning config's project. ``tenant_id`` / ``project_id`` are copied from
    the resolved config at insert time so a tenant only ever sees its own
    events under RLS.

    The raw body is the EXACT bytes the signature was computed over (stored
    decoded as text — webhook payloads are JSON/UTF-8). The signing secret is
    NEVER stored here (only on the config, encrypted); the ``signature`` header
    we DID verify is recorded for audit, which is safe (it is a MAC, not the
    key).
    """

    __tablename__ = "incoming_webhook_events"
    __table_args__ = (
        # An external sender that retries a delivery reuses the SAME delivery
        # id; a partial unique index makes a redelivery idempotent (the second
        # insert collides) without forbidding a NULL id (senders that omit it).
        Index(
            "uq_incoming_webhook_events_delivery",
            "config_id",
            "delivery_id",
            unique=True,
            postgresql_where=text("delivery_id IS NOT NULL"),
        ),
        Index("ix_incoming_webhook_events_config", "config_id", "received_at"),
    )

    config_id: Mapped[UUID] = mapped_column(
        ForeignKey("incoming_webhook_configs.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised from the config so a replay query is project-scoped without
    # a join (and RLS still scopes by tenant_id).
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # External sender family (mirrors the config's origin at receive time).
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    # The sender's per-delivery id (GitHub X-GitHub-Delivery, ...) when present;
    # the dedup key for idempotent redelivery. NULL when the sender omits it.
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The declared event type header (GitHub X-GitHub-Event, ...); informational
    # for the mapping/template phases (task_13_09 / task_13_10).
    event_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The signature header value we verified (a MAC — safe to store; NOT the
    # secret). Kept for audit / replay re-verification.
    signature: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # The EXACT raw request body the signature was computed over (JSON text).
    raw_body: Mapped[str] = mapped_column(Text, nullable=False)
    # True once verification passed — only verified events are persisted today,
    # but the column makes the contract explicit + future-proofs a "rejected
    # attempts" audit if ever wanted.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"IncomingWebhookEvent(id={self.id!r}, config={self.config_id!r}, "
            f"origin={self.origin!r}, verified={self.verified!r})"
        )


__all__ = [
    "ApiToken",
    "ApiTokenScope",
    "AuditAction",
    "AuditLog",
    "IncomingWebhookConfig",
    "IncomingWebhookEvent",
    "Organization",
    "PlatformSetting",
    "ReviewSession",
    "SSOConfiguration",
    "SSOProvider",
    "ScimToken",
    "Session",
    "TaskAuditEvent",
    "TenantSetting",
    "User",
    "UserOrganizationMembership",
    "UserRole",
]
