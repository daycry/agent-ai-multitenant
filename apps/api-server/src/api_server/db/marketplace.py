"""Marketplace ORM models (Plan 09 task_09_01).

The marketplace lets a tenant discover, install, and revoke skills /
tools / MCP servers from a curated public catalog or its own private
catalog, with per-permission consent and an append-only audit trail.

Four tables make up the substrate:

  - **`marketplace_sources`** — a registry/source of listings: the
    official curated catalog, a tenant's private catalog, or an
    external git/url source. Carries trust attributes (whether the
    source is signed/verified). **Tenant-agnostic by design**: a source
    is a platform-level concept (a registry endpoint), not per-tenant
    data. A *private* tenant catalog is modelled as a row whose
    ``owner_tenant_id`` is set (nullable FK), but the table itself is
    not RLS-protected — visibility is resolved in the service layer
    (public sources + the caller's own private source). This mirrors how
    ``platform_settings`` stays global while still tracking ``updated_by``.

  - **`marketplace_listings`** — a publishable entry (a skill, tool, or
    MCP server) with kind, name, version, author, ``trust_level``, a
    reference to its source, and a JSONB ``manifest`` (SKILL.md / tool
    manifest metadata) plus an optional cryptographic ``signature``.
    **Hybrid tenancy**: ``tenant_id`` is NULLABLE. A NULL ``tenant_id``
    is a *global catalog* listing (the public marketplace), visible to
    every tenant; a non-NULL ``tenant_id`` is a *private* listing owned
    by that tenant. RLS (added in task_09_02) follows the same partial
    pattern as builtin skills/tools: a tenant sees its own private rows
    plus all global (NULL-tenant) rows, and may only write its own.

  - **`installations`** — a listing installed into a tenant (optionally
    scoped to a project): the resolved version, lifecycle ``status``
    (enabled / disabled / revoked), the set of ``granted_permissions``
    the project owner consented to, who installed it, and timestamps.
    **Tenant-owned**: ``tenant_id NOT NULL`` + RLS — tenant A can never
    see, install over, or revoke tenant B's installation.

  - **`audit_entries`** — append-only marketplace audit: who did what
    (install / uninstall / revoke / consent / share …) to which listing
    or installation, with a JSONB ``detail`` and a timestamp.
    **Tenant-owned**: ``tenant_id NOT NULL`` + RLS. Not soft-deletable
    and not mutable — it is an immutable record (mirrors ``executions``
    and the foundations ``audit_log``).

All tenant-owned tables use the same UUID v7 + timestamp mixins and the
RLS isolation guarantee as the rest of the domain. The migration that
creates the tables, indexes, FKs, and RLS policies is task_09_02; this
module ships only the ORM shape + enums so the rest of Plan 09 can build
against a stable contract.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from api_server.db.base import (
    Base,
    SoftDeleteMixin,
    TenantScopedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


# =============================================================================
# Enums (StrEnum so values are stable strings persisted as TEXT)
# =============================================================================
class MarketplaceSourceType(enum.StrEnum):
    """Where a source's listings come from.

    - ``official``:   the curated public catalog shipped by the platform.
    - ``private``:    a single tenant's own internal catalog (Fase E).
    - ``git``:        an external git repository the operator trusts.
    - ``url``:        an external HTTP(S) index/registry endpoint.
    """

    OFFICIAL = "official"
    PRIVATE = "private"
    GIT = "git"
    URL = "url"


class MarketplaceListingKind(enum.StrEnum):
    """What a listing installs.

    Aligns with the Plan 05 mcp-tools taxonomy: a marketplace entry is a
    skill (declarative capability), a tool (executable function), or a
    whole MCP server (a bundle of tools)."""

    SKILL = "skill"
    TOOL = "tool"
    MCP_SERVER = "mcp_server"


class MarketplaceTrustLevel(enum.StrEnum):
    """Trust tier of a listing (spec §32; Fase B task_09_04 expands the
    semantics into applied guardrails).

    - ``verified``:      signed/reviewed by the platform team. Lightest
                         guardrails.
    - ``community``:     published by a third party. Always requires
                         explicit per-permission consent from the
                         project owner.
    - ``experimental``:  unvetted. Heaviest guardrails + sandbox.

    The level gates which guardrails apply, NOT availability.
    """

    VERIFIED = "verified"
    COMMUNITY = "community"
    EXPERIMENTAL = "experimental"


class ListingReviewStatus(enum.StrEnum):
    """Where a listing sits in the publication pipeline (ADR 0142 D6).

    ``draft → pending_review → published | rejected``. Nothing reaches the
    catalog without a system admin looking at it: **the catalog browse filters
    on ``published``**, and a listing in any other state exists only for its
    author tenant (who needs to read the rejection reason) — see
    :func:`api_server.marketplace.review.is_visible_in_catalog`.

    - ``draft``:          authored, not submitted. Never seen by anyone else.
    - ``pending_review``: waiting in the admin queue.
    - ``published``:      approved and in the catalog.
    - ``rejected``:       turned down with a written reason; the author fixes
                          it and re-submits (rejected → pending_review is a
                          legal edge — a rejection is not a death sentence).

    NOT a trust level. ``trust_level`` (verified / community / experimental)
    grades the guardrails and is promoted separately by the same admin; a
    listing can be ``published`` + ``community`` forever.
    """

    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"


class InstallationStatus(enum.StrEnum):
    """Lifecycle of an installation.

    - ``enabled``:   installed and ALLOWED to be used by the tenant's agents.
    - ``disabled``:  installed but temporarily turned off (reversible).
    - ``revoked``:   permanently uninstalled; the row is kept for audit.

    NOTE (ADR 0081): an ``enabled`` installation records *intent + permission*,
    NOT a live capability. The install pipeline does not yet **materialize** the
    listing's skill/tool into the tenant's native catalog (``tools`` / ``skills``)
    nor run the pre-install security gates (signature / static-analysis / sandbox)
    on the fresh-install path — both are Phase B/C, deferred pending the registry
    runtime + an out-of-process sandbox the api-server can invoke (it has no Docker
    socket by design). Until then, ``enabled`` does not mean an agent can actually
    invoke it. See ADR 0081 and ``marketplace/install.py``.
    """

    ENABLED = "enabled"
    DISABLED = "disabled"
    REVOKED = "revoked"


class DeploymentStatus(enum.StrEnum):
    """Lifecycle of a :class:`MarketplaceDeployment` (ADR 0142).

    - ``active``:   deployed and materialised in the target project.
    - ``disabled``: the deployment exists but is turned off (reversible;
                    Fase 4 parks a deployment here when a version update
                    breaks its config schema).
    - ``retired``:  torn down. The row is KEPT for audit — retiring undoes
                    exactly what ``created_refs`` says the deployment created
                    and nothing else (ADR 0142 §5).

    Only ``active`` participates in the partial-unique
    ``uq_marketplace_deployments_active`` index, so re-deploying over a
    retired pair is allowed while a second live deployment of the same
    (installation, project) is not.
    """

    ACTIVE = "active"
    DISABLED = "disabled"
    RETIRED = "retired"


class MarketplaceAuditAction(enum.StrEnum):
    """Append-only marketplace audit actions.

    Extend by adding members; never rename existing ones — historical
    rows still reference the old string value."""

    INSTALL = "install"
    UNINSTALL = "uninstall"
    REVOKE = "revoke"
    CONSENT = "consent"
    CONSENT_DENIED = "consent_denied"
    ENABLE = "enable"
    DISABLE = "disable"
    UPDATE = "update"
    SHARE = "share"
    # ADR 0142: the deployment lifecycle. ``deploy`` records that an
    # installation was materialised into a concrete project (with the config
    # + role_map that produced it); ``retire`` records the exact teardown.
    DEPLOY = "deploy"
    RETIRE = "retire"
    # ADR 0142 D6: la revisión de la publicación. Cada transición de
    # ``review_status`` deja su fila, con el motivo cuando es un rechazo.
    SUBMIT_REVIEW = "submit_review"
    APPROVE = "approve"
    REJECT = "reject"
    PROMOTE = "promote"
    # ADR 0142 D7: los despliegues re-encajados tras un cambio de versión.
    # Acción PROPIA y no un segundo ``update``: la instalación se mueve de
    # versión una vez, y contar dos filas ``update`` por una sola actualización
    # deja el rastro ambiguo ("¿se actualizó dos veces?"). Lo detectó un test
    # del plan 09 que cuenta exactamente eso.
    REFRESH = "refresh"


# =============================================================================
# marketplace_sources (tenant-agnostic registry)
# =============================================================================
class MarketplaceSource(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A registry/source that supplies marketplace listings.

    Tenancy decision: **tenant-agnostic** — a source is a platform-level
    registry endpoint, not per-tenant data, so it carries no
    ``tenant_id`` and no RLS policy. A *private* tenant catalog is a row
    whose nullable ``owner_tenant_id`` is set; the service layer resolves
    visibility (public sources + the caller's own private source). Write
    access to ``official`` sources is gated to the System Admin in the
    service layer, exactly like builtin skills/tools.
    """

    __tablename__ = "marketplace_sources"
    __table_args__ = (
        UniqueConstraint("name", name="uq_marketplace_sources_name"),
        Index(
            "ix_marketplace_sources_owner_tenant",
            "owner_tenant_id",
            postgresql_where=text("owner_tenant_id IS NOT NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'official'")
    )
    # For git/url sources: where listings are fetched from. NULL for the
    # built-in official catalog (served from the platform itself).
    uri: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Trust attributes of the SOURCE (distinct from a listing's trust
    # level): whether the platform trusts this registry enough to skip
    # certain checks, and whether listings from it are expected to be
    # signed.
    is_trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    requires_signature: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # The default trust level newly-published listings from this source
    # inherit (a private tenant source typically publishes ``community``).
    default_trust_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'experimental'")
    )

    # Set => this is a tenant's PRIVATE catalog; NULL => a public source.
    # No FK to organizations to avoid the RLS/FK coupling the foundations
    # tables deliberately avoid; resolved by explicit queries.
    owner_tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    listings: Mapped[list[MarketplaceListing]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"MarketplaceSource(id={self.id!r}, name={self.name!r})"


# =============================================================================
# marketplace_listings (hybrid: global catalog OR private tenant listing)
# =============================================================================
class MarketplaceListing(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A publishable skill / tool / MCP-server entry.

    Tenancy decision: **hybrid**. ``tenant_id`` is NULLABLE — a NULL row
    is a *global catalog* listing (the public marketplace, visible to
    every tenant); a non-NULL row is a *private* listing owned by that
    tenant. The RLS policy added in task_09_02 mirrors the builtin
    skills/tools pattern: ``tenant_id IS NULL OR tenant_id = current
    tenant`` for SELECT, own-tenant-only for writes.

    We do NOT inherit :class:`TenantScopedMixin` here because that mixin
    declares ``tenant_id`` NOT NULL; the marketplace catalog needs the
    NULL-means-global semantics, so the column is declared explicitly.
    """

    __tablename__ = "marketplace_listings"
    __table_args__ = (
        # A (source, name, version) triple is unique within its tenancy
        # scope — two tenants may both publish "acme-skill 1.0.0"
        # privately, and a private listing may shadow a global one.
        UniqueConstraint(
            "source_id",
            "tenant_id",
            "name",
            "version",
            name="uq_marketplace_listings_source_tenant_name_version",
        ),
        Index(
            "ix_marketplace_listings_tenant_kind",
            "tenant_id",
            "kind",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Fast lookup of the public catalog (the common browse path).
        Index(
            "ix_marketplace_listings_global_kind",
            "kind",
            postgresql_where=text("tenant_id IS NULL AND deleted_at IS NULL"),
        ),
        # ADR 0142 D6. Closed vocabulary at the DB level so a script or a
        # migration cannot invent a fifth state the review code never handles.
        CheckConstraint(
            "review_status IN ('draft', 'pending_review', 'published', 'rejected')",
            name="ck_marketplace_listings_review_status",
        ),
        # The admin review queue: everything NOT published, which is the small
        # side of the table. A partial index keeps the queue read cheap without
        # paying for the catalog rows.
        Index(
            "ix_marketplace_listings_review_queue",
            "review_status",
            postgresql_where=text("review_status <> 'published' AND deleted_at IS NULL"),
        ),
    )

    source_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_sources.id", ondelete="CASCADE"),
        nullable=False,
    )

    # NULL => global catalog listing; non-NULL => private to that tenant.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    # Semver string (validated/compared in task_09_12). TEXT-stored so we
    # never lose precision; no native semver type in PostgreSQL.
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)

    trust_level: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'experimental'")
    )

    # -- ADR 0142 D6: the review pipeline -----------------------------------
    # ``server_default='published'`` is a DELIBERATE asymmetry, and the reason
    # is worth writing down because the opposite default looks safer and is
    # not: the ONLY untrusted publisher is the tenant-facing
    # ``POST /marketplace/private/listings``, and that path sets
    # ``pending_review`` **explicitly** (asserted by
    # ``test_publishing_leaves_the_listing_pending_review``). Everything else
    # that writes this table is platform-curated — the official catalog seed
    # (:mod:`api_server.marketplace.seed`) and the 0129 backfill. Defaulting
    # those to ``draft`` would empty the live catalog on deploy and on every
    # re-seed, which is a louder outage than the failure the strict default
    # guards against.
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'published'")
    )
    # The system admin who approved / rejected / promoted it, and when. NULL on
    # the rows the 0129 backfill published: nobody reviewed them, and stamping
    # a reviewer there would be a lie in the audit trail.
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    # Mandatory on a rejection (enforced in
    # :func:`api_server.marketplace.review.reject_listing`, not by a CHECK: the
    # column is also NULL for every non-rejected row). Cleared on re-submit so
    # a stale accusation never outlives the verdict it belonged to.
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The SKILL.md / tool-manifest metadata (frontmatter, dependencies,
    # requested permissions, …). JSONB so the shape evolves migration-free.
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The permissions this listing requests (allowed_domains,
    # allowed_paths, network_policy, …). The project owner consents to a
    # subset at install time (task_09_07). Declarative list of permission
    # descriptors.
    requested_permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    # Optional detached cryptographic signature over the artifact;
    # verified for sources with ``requires_signature``. NULL when unsigned.
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[MarketplaceSource] = relationship(back_populates="listings")
    installations: Mapped[list[MarketplaceInstallation]] = relationship(
        back_populates="listing",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"MarketplaceListing(id={self.id!r}, name={self.name!r}, "
            f"version={self.version!r}, kind={self.kind!r})"
        )


# =============================================================================
# installations (tenant-owned)
# =============================================================================
class MarketplaceInstallation(
    Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, SoftDeleteMixin
):
    """A listing installed into a tenant (optionally a single project).

    Tenancy decision: **tenant-owned** — ``tenant_id NOT NULL`` (via
    :class:`TenantScopedMixin`) + RLS (task_09_02). Tenant A can never
    see, install over, or revoke tenant B's installation.
    """

    __tablename__ = "marketplace_installations"
    __table_args__ = (
        # A given listing is installed at most once per (tenant, project) while
        # live; revoked rows are kept for audit and excluded via the partial
        # WHERE. COALESCE(project_id, zero-uuid) so TENANT-WIDE installs
        # (project_id NULL) dedupe too (L4, migration 0096): PostgreSQL treats
        # NULLs as distinct, so a plain index over project_id would NOT prevent
        # two concurrent tenant-wide installs of the same listing.
        Index(
            "uq_marketplace_installations_live",
            "tenant_id",
            "listing_id",
            text("COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid)"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status != 'revoked'"),
        ),
        Index(
            "ix_marketplace_installations_tenant_status",
            "tenant_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional project scoping. NULL => installed tenant-wide.
    project_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
    )

    # The resolved semver actually installed (a listing may be re-pointed
    # to a newer version on update — task_09_12).
    version: Mapped[str] = mapped_column(String(64), nullable=False)

    # ADR 0142: the version row this installation CONSENTED to. The update
    # flow (Fase 4) diffs this snapshot against the listing's current version
    # and re-asks consent for the delta only; a rollback re-pins an older row.
    #
    # NULLABLE by deliberate deviation from the plan's "NOT NULL tras el
    # backfill": the backfill DOES leave zero NULLs (asserted by
    # ``tests/integration/test_marketplace_v2_migration.py``), but the writer
    # that keeps it populated on *publish* is Fase 3/4 of this same plan
    # (task_mkt2_09 / task_mkt2_11) and lives in ``routers/marketplace.py``.
    # A NOT NULL column whose only writer arrives two phases later turns every
    # fresh private-listing install into a 500. Instead,
    # ``marketplace.deploy.ensure_listing_version`` get-or-creates the row and
    # pins it on first deploy, so the pin is never missing where it matters.
    pinned_version_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listing_versions.id", ondelete="CASCADE"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'enabled'")
    )

    # The subset of the listing's requested permissions the project owner
    # actually consented to (task_09_07). Empty until consent is recorded.
    granted_permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # The subset of the listing's requested permissions the project owner
    # explicitly DENIED (task_09_07). The "pending" set is derived (requested
    # minus granted minus denied). An install whose trust level requires
    # per-permission consent stays ``disabled`` while any required
    # permission is still pending or denied.
    denied_permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    installed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    installed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    listing: Mapped[MarketplaceListing] = relationship(back_populates="installations")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"MarketplaceInstallation(id={self.id!r}, "
            f"listing_id={self.listing_id!r}, status={self.status!r})"
        )


# =============================================================================
# marketplace_listing_versions (one row per published version — ADR 0142)
# =============================================================================
class MarketplaceListingVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One published version of a listing: the snapshot an install pins.

    ADR 0142 splits "what the thing IS" (this row) from "what a tenant
    consented to" (the installation) and "the values a project uses" (the
    deployment). A version row freezes the manifest, the declared
    permissions and the ``config_schema`` **as published**, so:

      - the update flow can diff the version an installation PINNED against
        the version now current and re-ask consent for the delta only;
      - a rollback is "re-pin an older row", not "hope the manifest is still
        around";
      - a deployment records which version it has applied.

    Tenancy decision: **hybrid, mirroring the listing**. ``tenant_id`` is
    NULLABLE and always carries the OWNER listing's ``tenant_id`` — NULL for a
    global catalog listing (visible to every tenant), the owner tenant for a
    private one. The migration installs the same three policies
    ``marketplace_listings`` has (own-tenant FOR ALL, global read, shared
    read), so a version is exactly as visible as the listing it belongs to and
    never more.

    Append-only in spirit: rows are inserted on publish and never rewritten
    (the review transition of Fase 3 stamps ``reviewed_by``/``reviewed_at``
    once). No soft delete — deleting the listing cascades.
    """

    __tablename__ = "marketplace_listing_versions"
    __table_args__ = (
        # One row per (listing, semver). The publish flow is therefore
        # idempotent by construction and a re-publish of the same version is
        # a conflict, not a silent second history entry.
        UniqueConstraint(
            "listing_id",
            "version",
            name="uq_marketplace_listing_versions_listing_version",
        ),
        Index("ix_marketplace_listing_versions_listing", "listing_id"),
        Index(
            "ix_marketplace_listing_versions_tenant",
            "tenant_id",
            postgresql_where=text("tenant_id IS NOT NULL"),
        ),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Mirrors the listing's tenancy: NULL => global catalog version.
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    version: Mapped[str] = mapped_column(String(64), nullable=False)

    # The manifest AS PUBLISHED (including the optional ``targets`` and
    # ``config_schema`` keys the v2 manifest gained). Frozen: the listing's
    # own ``manifest`` column may move on, this one is the historical record.
    manifest: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    requested_permissions: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Broken out of the manifest so the deployment form can be discovered
    # without walking the whole manifest. NULL => this version declares no
    # per-project configuration (the deployment shows no form).
    config_schema: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)

    published_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stamped by the review transition (Fase 3, task_mkt2_09). NULL => not
    # reviewed yet; the backfilled rows of already-published listings are NULL
    # on purpose (nobody reviewed them — pretending otherwise would be a lie
    # in the audit trail).
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"MarketplaceListingVersion(id={self.id!r}, listing_id={self.listing_id!r}, "
            f"version={self.version!r})"
        )


# =============================================================================
# marketplace_deployments (tenant-owned — the ADR 0142 entity)
# =============================================================================
class MarketplaceDeployment(Base, UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin):
    """An installation materialised into ONE concrete project (ADR 0142).

    This is the row that turns "comprar" into "recibir". Installing adds a
    capability to the tenant's pool; **deploying** writes the concrete rows
    that make an agent able to use it:

      - ``kind=mcp_server`` → an entry in ``projects.mcp_servers`` plus the
        role→tool policy in ``projects.mcp_tool_roles`` (ADR 0128 — the
        deployment FILLS the existing policy, it does not invent a parallel
        one);
      - ``kind∈{tool,skill}`` → ``agent_tools`` / ``agent_skills`` rows for the
        agents of the project's team whose role appears in ``role_map``.

    Every row written that way is recorded in :attr:`created_refs`, and that
    is the whole contract of an exact teardown: retiring removes EXACTLY those
    references and nothing else, so a tool the operator granted by hand to the
    same agent survives the retirement (ADR 0142 §5).

    Tenancy decision: **tenant-owned** — ``tenant_id NOT NULL`` (via
    :class:`TenantScopedMixin`) + RLS with FORCE. An installation of tenant A
    can neither be deployed into a project of tenant B nor be seen from it.
    """

    __tablename__ = "marketplace_deployments"
    __table_args__ = (
        # Closed vocabulary at the DB level, not just in the enum: a bad
        # ``status`` written by a migration/script must not be possible.
        CheckConstraint(
            "status IN ('active', 'disabled', 'retired')",
            name="ck_marketplace_deployments_status",
        ),
        # The latch that makes re-deploying idempotent: at most ONE active
        # deployment per (installation, project). ``disabled`` and ``retired``
        # rows stay out of the index so history accumulates freely and a
        # re-deploy after a retirement is allowed.
        Index(
            "uq_marketplace_deployments_active",
            "installation_id",
            "project_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        # "What is deployed in this project?" — the project-side read of the
        # UI, restricted to what is live.
        Index(
            "ix_marketplace_deployments_project_active",
            "project_id",
            postgresql_where=text("status = 'active'"),
        ),
        # "Where is this installation deployed?" — the installation-side read
        # (includes retired rows: the ficha shows history).
        Index("ix_marketplace_deployments_installation", "installation_id"),
    )

    installation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    # The VALUES for this project, validated against the deployed version's
    # ``config_schema``. Secrets NEVER live here: a field declared
    # ``secret: true`` only accepts a ``vault:`` pointer (ADR 0142 §3).
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # ``{capability_name: [agent_role, ...]}`` — which roles of the project's
    # team receive what. Derived from the manifest's ``targets`` plus whatever
    # the deployer adjusted.
    role_map: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # The listing version this deployment has APPLIED (semver string, same
    # shape as ``MarketplaceInstallation.version``). Fase 4 compares it with
    # the installation's pinned version to decide whether a refresh is due.
    deployed_version: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    # Why it is ``disabled`` (ADR 0142 D7, migration 0130). Set when a version
    # update cannot be applied — typically the new ``config_schema`` added a
    # required field with no default, and applying half of it would leave the
    # project with a half-configured capability, which is worse than not having
    # it. Without this column ``disabled`` is a mute state: the operator sees a
    # switched-off capability and nowhere to read what is missing.
    disabled_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # The rows this deployment created, by kind. The contract of the exact
    # teardown; see :class:`api_server.marketplace.deploy.CreatedRefs` for the
    # canonical shape.
    created_refs: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    deployed_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    retired_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"MarketplaceDeployment(id={self.id!r}, installation_id={self.installation_id!r}, "
            f"project_id={self.project_id!r}, status={self.status!r})"
        )


# =============================================================================
# marketplace_shares (cross-tenant sharing grant — opt-in, audited)
# =============================================================================
class MarketplaceShare(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """An explicit, opt-in grant sharing one tenant's PRIVATE listing with
    another tenant (Plan 09 task_09_17).

    Cross-tenant sharing is the one place tenant boundaries are deliberately
    crossed — so it is *never* an implicit RLS bypass. A share is a row that
    records: which private listing is shared (``listing_id``), the OWNER
    tenant that shared it (``owner_tenant_id``), and the single TARGET tenant
    that may now see/install it (``target_tenant_id``). Default = nothing
    shared: with no live share row, the target tenant sees nothing.

    Tenancy decision — **dual-scoped + System-Admin-auditable**:

      - The OWNER tenant *manages* its grants (create / list / revoke). The
        RLS ``FOR ALL`` policy keys management on ``owner_tenant_id`` = the
        current tenant, so a tenant can only ever share its own listings and
        revoke its own grants (the WITH CHECK rejects a forged
        ``owner_tenant_id``).
      - The TARGET tenant may *read* the share rows naming it as recipient (a
        SELECT-only policy on ``target_tenant_id``), so it can see "shared by
        tenant X" — but cannot create or revoke a grant.
      - The visibility of the *shared listing itself* to the target is wired
        by a SELECT-only RLS policy on ``marketplace_listings`` that exposes a
        listing iff a live share grants it to the current tenant. Revoking the
        share removes that visibility immediately. The target therefore sees
        the listing ONLY through the explicit grant — never via a private-row
        bypass.
      - The System Admin (BYPASSRLS session) enumerates ALL shares for audit.

    Every create/revoke also writes a :class:`MarketplaceAuditEntry`
    (``action=share``) so the platform audit trail records each share event.
    We declare ``owner_tenant_id`` explicitly (not via
    :class:`TenantScopedMixin`) because the management vs. recipient split
    needs two tenant columns, neither of which is the single ``tenant_id`` the
    mixin assumes.
    """

    __tablename__ = "marketplace_shares"
    __table_args__ = (
        # At most one LIVE share per (listing, target). A revoked / deleted
        # grant frees the slot for a fresh re-share. NULLs never collide here
        # (both columns are NOT NULL), so this dedupes cleanly.
        Index(
            "uq_marketplace_shares_live",
            "listing_id",
            "target_tenant_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND revoked_at IS NULL"),
        ),
        Index(
            "ix_marketplace_shares_owner",
            "owner_tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_marketplace_shares_target",
            "target_tenant_id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_marketplace_shares_listing_id", "listing_id"),
    )

    listing_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
        nullable=False,
    )
    # The tenant that owns the listing and created the grant.
    owner_tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # The single tenant the listing is shared WITH.
    target_tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    granted_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"MarketplaceShare(id={self.id!r}, listing_id={self.listing_id!r}, "
            f"owner_tenant_id={self.owner_tenant_id!r}, "
            f"target_tenant_id={self.target_tenant_id!r})"
        )


# =============================================================================
# audit_entries (tenant-owned, append-only)
# =============================================================================
class MarketplaceAuditEntry(Base, UUIDPrimaryKeyMixin):
    """Append-only marketplace audit trail.

    Tenancy decision: **tenant-owned** — ``tenant_id NOT NULL`` + RLS
    (task_09_02). One row per sensitive marketplace action (install,
    uninstall, revoke, consent, …). Not soft-deletable and not mutable —
    an immutable record (mirrors ``executions`` / foundations
    ``audit_log``). We declare ``tenant_id`` explicitly (rather than via
    :class:`TenantScopedMixin`) because this table has no
    ``updated_at`` / ``deleted_at`` and only a single ``created_at``.
    """

    __tablename__ = "marketplace_audit_entries"
    __table_args__ = (
        Index("ix_marketplace_audit_tenant_created", "tenant_id", "created_at"),
        Index("ix_marketplace_audit_action_created", "action", "created_at"),
        Index(
            "ix_marketplace_audit_installation",
            "installation_id",
            postgresql_where=text("installation_id IS NOT NULL"),
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    # Who performed the action. Free-form so it can name a user
    # ("user:<uuid>"), the System Admin, or a system actor — mirrors
    # TaskAuditEvent.actor.
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)

    # The target of the action. Either/both may be NULL depending on the
    # action (e.g. a CONSENT_DENIED before any installation row exists).
    listing_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_listings.id", ondelete="SET NULL"),
        nullable=True,
    )
    installation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("marketplace_installations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Free-form structured detail (which permission was consented, the
    # static-analysis verdict, the version diff on an update, …).
    detail: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"MarketplaceAuditEntry(id={self.id!r}, action={self.action!r}, actor={self.actor!r})"
        )


__all__ = [
    "DeploymentStatus",
    "InstallationStatus",
    "ListingReviewStatus",
    "MarketplaceAuditAction",
    "MarketplaceAuditEntry",
    "MarketplaceDeployment",
    "MarketplaceInstallation",
    "MarketplaceListing",
    "MarketplaceListingKind",
    "MarketplaceListingVersion",
    "MarketplaceShare",
    "MarketplaceSource",
    "MarketplaceSourceType",
    "MarketplaceTrustLevel",
]
