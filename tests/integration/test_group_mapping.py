"""Unit tests for the IdP group → tenant role resolver (ADR 0047).

ADR 0047 (global auth + access-by-membership) **removed** the
group-driven role assignment AT LOGIN: a successful OIDC callback / SAML
ACS now provisions only the GLOBAL identity and creates NO tenant
membership and reads NO IdP groups (``_provision_identity`` in
``routers/sso.py``). Access to a tenant — and the role within it — is
granted EXCLUSIVELY by the membership an admin assigns AFTER login.

The end-to-end "IdP group → tenant_admin on login" tests of the old
per-tenant model are therefore gone (the concept was deliberately
removed). What survives — and stays meaningful — is the pure resolver
function ``resolve_role_from_groups`` plus the grantable-role guard
``is_grantable_role``: the ``group_role_mappings`` column is still part of
the (now global) config and the schema validators still reject a mapping
that would grant a platform role, so the resolver's invariants (highest
privilege wins, system roles never grantable) are still worth pinning.

These are fast, pure-logic tests with NO DB and NO IdP — they need
neither postgres nor redis.
"""

from __future__ import annotations

from api_server.auth.sso.group_mapping import (
    DEFAULT_TENANT_ROLE,
    is_grantable_role,
    resolve_role_from_groups,
)


def test_resolver_maps_group_to_admin() -> None:
    role = resolve_role_from_groups(["platform-admins"], {"platform-admins": "tenant_admin"})
    assert role == "tenant_admin"


def test_resolver_unmapped_groups_keep_default() -> None:
    assert resolve_role_from_groups(["random-group"], {"platform-admins": "tenant_admin"}) == (
        DEFAULT_TENANT_ROLE
    )
    # No groups / no mapping → default.
    assert resolve_role_from_groups([], {}) == DEFAULT_TENANT_ROLE


def test_resolver_highest_privilege_wins() -> None:
    mapping = {"staff": "tenant_user", "admins": "tenant_admin"}
    # Order in the asserted-groups list must not matter.
    assert resolve_role_from_groups(["staff", "admins"], mapping) == "tenant_admin"
    assert resolve_role_from_groups(["admins", "staff"], mapping) == "tenant_admin"


def test_resolver_never_grants_system_role() -> None:
    # A misconfigured / forged mapping pointing a group at a platform role
    # is ignored — the user stays at the default, never a system role.
    assert not is_grantable_role("system_admin")
    assert not is_grantable_role("system_operator")
    assert resolve_role_from_groups(["evil"], {"evil": "system_admin"}) == DEFAULT_TENANT_ROLE
    assert resolve_role_from_groups(["ops"], {"ops": "system_operator"}) == DEFAULT_TENANT_ROLE
    # Even mixed with a legit grant, the system entry is simply skipped and
    # the legit grant still applies.
    assert (
        resolve_role_from_groups(
            ["evil", "admins"], {"evil": "system_admin", "admins": "tenant_admin"}
        )
        == "tenant_admin"
    )


def test_resolver_non_string_group_values_are_ignored() -> None:
    # The OIDC/SAML extractors already coerce to list[str]; the resolver is
    # also robust to an empty list.
    assert resolve_role_from_groups([], {"x": "tenant_admin"}) == DEFAULT_TENANT_ROLE
