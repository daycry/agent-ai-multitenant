"""IdP group → tenant role mapping (Plan 08 task_08_11).

On every SSO login (OIDC + SAML) the IdP may assert the user's group
memberships (the OIDC ``groups`` claim / a SAML ``groups`` attribute).
A tenant can configure a mapping from those group names onto a tenant
role, so an enterprise managing access in its IdP ("members of the
``platform-admins`` AD group are tenant admins") sees that reflected in
the platform on the user's next login — no manual promotion needed.

The mapping is a small JSON object on ``sso_configurations``
(``group_role_mappings``): ``{"<idp-group>": "<tenant-role>"}``.

Resolution rules (deliberately conservative):

  * **Highest-privilege wins.** A user in several mapped groups gets the
    most privileged of the mapped roles.
  * **No mapping → keep the default** (``tenant_user``). An empty
    mapping, no asserted groups, or only unmapped groups all leave the
    user at the default role; the mapping never *removes* access.
  * **Never escalate to a platform/system role via groups.** Only the
    per-tenant roles ``tenant_admin`` / ``tenant_user`` are grantable
    here. System-wide admin is the ``users.is_system_admin`` boolean —
    independent of any membership — and ``system_operator`` is a
    platform role; neither is ever reachable through an IdP group, even
    if an operator (or a forged claim) puts that string in the mapping.
    Such an entry is ignored.

The matching is exact and case-sensitive on the group name (IdP group
names are stable identifiers, not free text), so a tenant's mapping
keys must match what the IdP asserts verbatim.
"""

from __future__ import annotations

from api_server.db.models import UserRole

# The ONLY roles an IdP group may grant. Ordered most→least privileged so
# "highest wins" is a simple index comparison. `system_operator` and the
# system-admin boolean are intentionally absent: a group can never mint a
# platform-level role.
_GRANTABLE_ROLES_BY_PRIVILEGE: tuple[str, ...] = (
    UserRole.TENANT_ADMIN.value,
    UserRole.TENANT_USER.value,
)

# The role a user keeps when no group maps to anything (JIT default).
DEFAULT_TENANT_ROLE: str = UserRole.TENANT_USER.value


def _privilege_rank(role: str) -> int:
    """Lower index == higher privilege. Unknown/ungrantable → +inf."""
    try:
        return _GRANTABLE_ROLES_BY_PRIVILEGE.index(role)
    except ValueError:
        return len(_GRANTABLE_ROLES_BY_PRIVILEGE)


def is_grantable_role(role: str) -> bool:
    """Whether ``role`` is a per-tenant role an IdP group may grant.

    Guards the config surface AND the login path: a mapping value that is
    not one of the grantable per-tenant roles (e.g. ``system_admin`` or
    ``system_operator``) is never honoured.
    """
    return role in _GRANTABLE_ROLES_BY_PRIVILEGE


def resolve_role_from_groups(
    groups: list[str],
    group_role_mappings: dict[str, str],
    *,
    default_role: str = DEFAULT_TENANT_ROLE,
) -> str:
    """Resolve the tenant role for a user from their asserted IdP groups.

    Args:
        groups: the group names the IdP asserted for this login (the
            already-extracted ``groups`` claim/attribute). May be empty.
        group_role_mappings: the tenant's ``{group: role}`` configuration.
            May be empty. Values that are not grantable per-tenant roles
            are ignored (a forged/misconfigured ``system_admin`` entry can
            never grant that).
        default_role: the role to keep when nothing maps. Defaults to
            ``tenant_user`` (the JIT default); never escalates above it.

    Returns:
        The most-privileged grantable role any of the user's groups maps
        to, or ``default_role`` when no group maps to a grantable role.
    """
    best = default_role
    best_rank = _privilege_rank(default_role)
    for group in groups:
        mapped = group_role_mappings.get(group)
        if mapped is None or not is_grantable_role(mapped):
            # Unmapped group, or a value that is not a grantable per-tenant
            # role (e.g. system_admin / system_operator) — never honoured.
            continue
        rank = _privilege_rank(mapped)
        if rank < best_rank:
            best = mapped
            best_rank = rank
    return best


__all__ = [
    "DEFAULT_TENANT_ROLE",
    "is_grantable_role",
    "resolve_role_from_groups",
]
