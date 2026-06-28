"""Unit tests for the phase-0 SQLAlchemy models.

These tests do NOT hit a database. They verify that:

  - Every model imports cleanly and has the expected columns.
  - The shared mixins land on the right tables (UUID PK, timestamps,
    soft-delete, tenant-scope).
  - Enums expose the agreed string values (changing one is a contract
    break for already-persisted audit rows).
  - Default UUID generator produces v7 ids.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from api_server.db import models as m
from api_server.db.base import _new_uuid7


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
def test_user_role_values() -> None:
    assert m.UserRole.TENANT_ADMIN.value == "tenant_admin"
    assert m.UserRole.TENANT_USER.value == "tenant_user"
    assert m.UserRole.SYSTEM_OPERATOR.value == "system_operator"
    # plan_approver: aprobador de planes sin ser tenant_admin (ADR 0079, Opción A).
    assert m.UserRole.PLAN_APPROVER.value == "plan_approver"
    assert {r.value for r in m.UserRole} == {
        "tenant_admin",
        "tenant_user",
        "system_operator",
        "plan_approver",
    }


def test_audit_action_known_set() -> None:
    expected = {
        "user.registered",
        "user.login",
        "user.login_failed",
        "user.logout",
        "session.revoked",
        "tenant.created",
        "tenant.updated",
        "tenant.deleted",
        "membership.granted",
        "membership.revoked",
        # SSO audit actions (Plan 08 task_08_01 / task_08_07).
        "sso.login",
        "sso.user_provisioned",
    }
    assert {a.value for a in m.AuditAction} == expected


# ---------------------------------------------------------------------------
# UUID v7 generator
# ---------------------------------------------------------------------------
def test_uuid7_is_uuid_version_7() -> None:
    uid = _new_uuid7()
    assert isinstance(uid, UUID)
    # The version nibble is the 13th hex char (index 14 in canonical form
    # with hyphens), and must be "7" for UUID v7.
    assert uid.hex[12] == "7"


def test_uuid7_is_monotonic_within_a_burst() -> None:
    # UUID v7 ids generated in sequence must be strictly ordered because
    # the timestamp prefix moves forward and ties are broken by a counter.
    ids = [_new_uuid7() for _ in range(20)]
    sorted_ids = sorted(ids)
    assert ids == sorted_ids


# ---------------------------------------------------------------------------
# Organization
# ---------------------------------------------------------------------------
def test_organization_table_shape() -> None:
    cols = {c.name for c in m.Organization.__table__.columns}
    assert {"id", "name", "slug", "is_active", "created_at", "updated_at", "deleted_at"}.issubset(
        cols
    )
    assert m.Organization.__table__.c.slug.unique


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------
def test_user_table_shape() -> None:
    cols = {c.name for c in m.User.__table__.columns}
    assert {
        "id",
        "email",
        "password_hash",
        "full_name",
        "is_system_admin",
        "is_active",
        "last_login_at",
        "created_at",
        "updated_at",
        "deleted_at",
    }.issubset(cols)
    assert m.User.__table__.c.email.unique


def test_user_email_max_length_320() -> None:
    # RFC 5321 caps an email at 254 chars; we allow 320 to be safe with
    # historical / non-conformant addresses already in the wild.
    assert m.User.__table__.c.email.type.length == 320


# ---------------------------------------------------------------------------
# UserOrganizationMembership — tenant-scoped
# ---------------------------------------------------------------------------
def test_membership_is_tenant_scoped() -> None:
    cols = {c.name for c in m.UserOrganizationMembership.__table__.columns}
    assert "tenant_id" in cols
    assert "user_id" in cols
    assert "role" in cols


def test_membership_unique_per_user_tenant() -> None:
    # A user must not be able to hold two memberships in the same tenant
    # — additional roles should ALTER the row, not insert another.
    constraint_names = {c.name for c in m.UserOrganizationMembership.__table__.constraints}
    assert "uq_membership_user_tenant" in constraint_names


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
def test_session_table_shape() -> None:
    cols = {c.name for c in m.Session.__table__.columns}
    assert {
        "id",
        "user_id",
        "tenant_id",
        "expires_at",
        "last_active_at",
        "revoked_at",
        "ip_address",
        "user_agent",
        "created_at",
        "updated_at",
    }.issubset(cols)
    # tenant_id is nullable: a brand-new session has no active tenant.
    assert m.Session.__table__.c.tenant_id.nullable is True


# ---------------------------------------------------------------------------
# AuditLog — append-only, tenant_id nullable
# ---------------------------------------------------------------------------
def test_audit_log_table_shape() -> None:
    cols = {c.name for c in m.AuditLog.__table__.columns}
    assert {
        "id",
        "tenant_id",
        "user_id",
        "action",
        "resource_type",
        "resource_id",
        "changes",
        "ip_address",
        "user_agent",
        "created_at",
    }.issubset(cols)
    # No updated_at / deleted_at: append-only.
    assert "updated_at" not in cols
    assert "deleted_at" not in cols


def test_audit_log_tenant_id_nullable() -> None:
    # System Admin actions are cross-tenant — tenant_id must be NULLABLE.
    assert m.AuditLog.__table__.c.tenant_id.nullable is True


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name",
    [
        "AuditAction",
        "AuditLog",
        "Organization",
        "Session",
        "User",
        "UserOrganizationMembership",
        "UserRole",
    ],
)
def test_public_api_exports(name: str) -> None:
    assert hasattr(m, name), f"models module does not export {name}"
