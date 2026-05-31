"""Unit tests for the Marketplace ORM contract (Plan 09 task_09_01).

The migration + RLS are exercised in
``tests/integration/test_marketplace_migration.py`` (task_09_02). Here we
stay in-process and pin the column shape, enum values, defaults,
relationships, and the tenant-scoping decision the rest of Plan 09
depends on.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.db.marketplace import (
    InstallationStatus,
    MarketplaceAuditAction,
    MarketplaceAuditEntry,
    MarketplaceInstallation,
    MarketplaceListing,
    MarketplaceListingKind,
    MarketplaceSource,
    MarketplaceSourceType,
    MarketplaceTrustLevel,
)
from sqlalchemy import CheckConstraint, UniqueConstraint

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
def test_source_type_enum_values() -> None:
    assert {t.value for t in MarketplaceSourceType} == {
        "official",
        "private",
        "git",
        "url",
    }


def test_listing_kind_enum_values() -> None:
    """Aligns with the Plan 05 skill/tool/mcp taxonomy."""
    assert {k.value for k in MarketplaceListingKind} == {
        "skill",
        "tool",
        "mcp_server",
    }


def test_trust_level_enum_values() -> None:
    assert {t.value for t in MarketplaceTrustLevel} == {
        "verified",
        "community",
        "experimental",
    }


def test_installation_status_enum_values() -> None:
    assert {s.value for s in InstallationStatus} == {
        "enabled",
        "disabled",
        "revoked",
    }


def test_audit_action_enum_includes_core_actions() -> None:
    values = {a.value for a in MarketplaceAuditAction}
    # The four core actions the task names, plus the lifecycle ones.
    assert {"install", "uninstall", "revoke", "consent"} <= values


def test_enums_are_string_valued() -> None:
    """StrEnum: the value persists as a plain string (TEXT column)."""
    assert MarketplaceListingKind.SKILL == "skill"
    assert MarketplaceTrustLevel.VERIFIED == "verified"
    assert InstallationStatus.ENABLED == "enabled"
    assert MarketplaceAuditAction.INSTALL == "install"


# ---------------------------------------------------------------------------
# marketplace_sources — tenant-agnostic registry
# ---------------------------------------------------------------------------
def test_source_table_name_and_columns() -> None:
    assert MarketplaceSource.__tablename__ == "marketplace_sources"
    cols = {c.name for c in MarketplaceSource.__table__.columns}
    assert {
        "id",
        "name",
        "description",
        "source_type",
        "uri",
        "is_trusted",
        "requires_signature",
        "default_trust_level",
        "owner_tenant_id",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_source_is_tenant_agnostic() -> None:
    """A source is a platform-level registry: no NOT-NULL tenant_id.

    Private catalogs are modelled via the *nullable* owner_tenant_id, not
    via TenantScopedMixin's NOT-NULL tenant_id.
    """
    cols = {c.name for c in MarketplaceSource.__table__.columns}
    assert "tenant_id" not in cols
    owner_col = MarketplaceSource.__table__.columns["owner_tenant_id"]
    assert owner_col.nullable is True


def test_source_name_is_unique() -> None:
    uniques = {
        c.name for c in MarketplaceSource.__table__.constraints if isinstance(c, UniqueConstraint)
    }
    assert "uq_marketplace_sources_name" in uniques


def test_source_defaults() -> None:
    src = MarketplaceSource(name="official-catalog")
    # Server-side defaults are NULL on the in-memory object until flush;
    # assert the column carries the expected server_default text instead.
    assert MarketplaceSource.__table__.columns["source_type"].server_default is not None
    assert MarketplaceSource.__table__.columns["is_trusted"].server_default is not None
    assert MarketplaceSource.__table__.columns["default_trust_level"].server_default is not None
    assert src.name == "official-catalog"


def test_source_construction_with_all_attrs() -> None:
    src = MarketplaceSource(
        name="acme-private",
        description="ACME internal catalog",
        source_type=MarketplaceSourceType.PRIVATE,
        uri="git+https://git.acme.example/catalog.git",
        is_trusted=True,
        requires_signature=True,
        default_trust_level=MarketplaceTrustLevel.COMMUNITY,
        owner_tenant_id=uuid4(),
    )
    assert src.source_type == "private"
    assert src.requires_signature is True
    assert src.owner_tenant_id is not None


# ---------------------------------------------------------------------------
# marketplace_listings — hybrid global/private tenancy
# ---------------------------------------------------------------------------
def test_listing_table_name_and_columns() -> None:
    assert MarketplaceListing.__tablename__ == "marketplace_listings"
    cols = {c.name for c in MarketplaceListing.__table__.columns}
    assert {
        "id",
        "source_id",
        "tenant_id",
        "kind",
        "name",
        "version",
        "description",
        "author",
        "trust_level",
        "manifest",
        "requested_permissions",
        "signature",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_listing_tenant_id_is_nullable_for_global_catalog() -> None:
    """NULL tenant_id == a global (public catalog) listing; a non-NULL
    tenant_id == a private tenant listing."""
    tenant_col = MarketplaceListing.__table__.columns["tenant_id"]
    assert tenant_col.nullable is True


def test_listing_source_fk_cascades() -> None:
    fks = list(MarketplaceListing.__table__.columns["source_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_listing_unique_per_source_tenant_name_version() -> None:
    uniques = {
        c.name for c in MarketplaceListing.__table__.constraints if isinstance(c, UniqueConstraint)
    }
    assert "uq_marketplace_listings_source_tenant_name_version" in uniques


def test_listing_construction_global_catalog() -> None:
    listing = MarketplaceListing(
        source_id=uuid4(),
        tenant_id=None,
        kind=MarketplaceListingKind.TOOL,
        name="playwright",
        version="1.0.0",
        description="Browser automation",
        author="platform-team",
        trust_level=MarketplaceTrustLevel.VERIFIED,
        manifest={"frontmatter": {"name": "playwright"}},
        requested_permissions=[{"type": "network_policy", "value": "restricted"}],
        signature=None,
    )
    assert listing.tenant_id is None
    assert listing.kind == "tool"
    assert listing.trust_level == "verified"
    assert listing.requested_permissions[0]["type"] == "network_policy"


def test_listing_construction_private() -> None:
    tid = uuid4()
    listing = MarketplaceListing(
        source_id=uuid4(),
        tenant_id=tid,
        kind=MarketplaceListingKind.SKILL,
        name="acme-reviewer",
        version="2.1.3",
    )
    assert listing.tenant_id == tid
    assert listing.kind == "skill"


# ---------------------------------------------------------------------------
# marketplace_installations — tenant-owned
# ---------------------------------------------------------------------------
def test_installation_table_name_and_columns() -> None:
    assert MarketplaceInstallation.__tablename__ == "marketplace_installations"
    cols = {c.name for c in MarketplaceInstallation.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "listing_id",
        "project_id",
        "version",
        "status",
        "granted_permissions",
        "installed_by",
        "installed_at",
        "revoked_at",
        "revoked_by",
        "created_at",
        "updated_at",
        "deleted_at",
    } <= cols


def test_installation_is_tenant_owned_not_null() -> None:
    """Tenant-owned: tenant_id NOT NULL (via TenantScopedMixin)."""
    tenant_col = MarketplaceInstallation.__table__.columns["tenant_id"]
    assert tenant_col.nullable is False


def test_installation_listing_fk_cascades() -> None:
    fks = list(MarketplaceInstallation.__table__.columns["listing_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_installation_project_fk_is_nullable_and_cascades() -> None:
    """Optional project scoping; NULL means tenant-wide."""
    col = MarketplaceInstallation.__table__.columns["project_id"]
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


def test_installation_installed_by_fk_sets_null_on_user_delete() -> None:
    fks = list(MarketplaceInstallation.__table__.columns["installed_by"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "SET NULL"


def test_installation_has_live_unique_index() -> None:
    idx = {i.name: i for i in MarketplaceInstallation.__table__.indexes}
    assert "uq_marketplace_installations_live" in idx
    assert idx["uq_marketplace_installations_live"].unique is True


def test_installation_construction_defaults() -> None:
    inst = MarketplaceInstallation(
        tenant_id=uuid4(),
        listing_id=uuid4(),
        version="1.0.0",
    )
    assert inst.version == "1.0.0"
    # status / granted_permissions have server defaults (NULL pre-flush).
    assert MarketplaceInstallation.__table__.columns["status"].server_default is not None
    assert (
        MarketplaceInstallation.__table__.columns["granted_permissions"].server_default is not None
    )


# ---------------------------------------------------------------------------
# marketplace_audit_entries — tenant-owned, append-only
# ---------------------------------------------------------------------------
def test_audit_table_name_and_columns() -> None:
    assert MarketplaceAuditEntry.__tablename__ == "marketplace_audit_entries"
    cols = {c.name for c in MarketplaceAuditEntry.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "actor",
        "action",
        "listing_id",
        "installation_id",
        "detail",
        "created_at",
    } <= cols


def test_audit_is_tenant_owned_not_null() -> None:
    tenant_col = MarketplaceAuditEntry.__table__.columns["tenant_id"]
    assert tenant_col.nullable is False


def test_audit_is_append_only_no_soft_delete_or_update() -> None:
    """Immutable record: no updated_at / deleted_at, only created_at."""
    cols = {c.name for c in MarketplaceAuditEntry.__table__.columns}
    assert "updated_at" not in cols
    assert "deleted_at" not in cols
    assert "created_at" in cols


def test_audit_target_fks_set_null() -> None:
    """The listing/installation may be deleted; the audit row survives."""
    listing_fks = list(MarketplaceAuditEntry.__table__.columns["listing_id"].foreign_keys)
    install_fks = list(MarketplaceAuditEntry.__table__.columns["installation_id"].foreign_keys)
    assert listing_fks[0].ondelete == "SET NULL"
    assert install_fks[0].ondelete == "SET NULL"


def test_audit_target_columns_nullable() -> None:
    assert MarketplaceAuditEntry.__table__.columns["listing_id"].nullable is True
    assert MarketplaceAuditEntry.__table__.columns["installation_id"].nullable is True


def test_audit_construction() -> None:
    entry = MarketplaceAuditEntry(
        tenant_id=uuid4(),
        actor="user:" + str(uuid4()),
        action=MarketplaceAuditAction.CONSENT,
        listing_id=uuid4(),
        installation_id=uuid4(),
        detail={"permission": "allowed_domains", "value": ["api.x.com"]},
    )
    assert entry.action == "consent"
    assert entry.detail["permission"] == "allowed_domains"


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------
def test_source_listings_relationship() -> None:
    """A source has many listings; deleting it cascades."""
    rel = MarketplaceSource.__mapper__.relationships["listings"]
    assert rel.mapper.class_ is MarketplaceListing


def test_listing_source_back_reference() -> None:
    rel = MarketplaceListing.__mapper__.relationships["source"]
    assert rel.mapper.class_ is MarketplaceSource


def test_listing_installations_relationship() -> None:
    rel = MarketplaceListing.__mapper__.relationships["installations"]
    assert rel.mapper.class_ is MarketplaceInstallation


def test_installation_listing_back_reference() -> None:
    rel = MarketplaceInstallation.__mapper__.relationships["listing"]
    assert rel.mapper.class_ is MarketplaceListing


def test_relationship_round_trip_in_memory() -> None:
    """Build the object graph in memory (no DB) and verify back_populates."""
    src = MarketplaceSource(name="official", source_type=MarketplaceSourceType.OFFICIAL)
    listing = MarketplaceListing(
        source=src,
        kind=MarketplaceListingKind.SKILL,
        name="code-review",
        version="1.0.0",
    )
    assert listing in src.listings
    assert listing.source is src


# ---------------------------------------------------------------------------
# Sanity: no stray CheckConstraints we didn't intend (documentation guard)
# ---------------------------------------------------------------------------
def test_no_unexpected_check_constraints() -> None:
    """Phase A keeps it simple — no CHECK constraints on these tables yet
    (status/kind/trust validation is enum-enforced in the service layer
    and may become CHECKs in task_09_02's migration)."""
    for model in (
        MarketplaceSource,
        MarketplaceListing,
        MarketplaceInstallation,
        MarketplaceAuditEntry,
    ):
        checks = [c for c in model.__table__.constraints if isinstance(c, CheckConstraint)]
        assert checks == [], f"{model.__name__} has unexpected CHECK constraints"
