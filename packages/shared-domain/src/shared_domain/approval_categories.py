"""Single source of the sensitive-action categories (spec §7.7-7.8).

The human-approval gate has TWO consumers that must agree on the category
vocabulary: the api-server seed of the four policy presets
(``api_server.seeds.builtin_approval_policies``) and the sandboxed runtime gate
(``agent_runtime.approval``). They diverged — the runtime emitted 4 categories
(``code_execution``/``file_write``/``network_access``/``agent_delegation``) that
did NOT intersect these 13, so ``requires_human`` always fell through to ``auto``
and NOTHING was gated, not even under the ``customer-external`` preset (audit
2026-07-03, g6, fail-open). This module is the one list both import; a contract
test pins the runtime tool→category map to it so they cannot drift again.

Lives in ``shared-domain`` because the runtime is sandboxed (no DB, no
api-server) but already imports ``shared_domain`` (e.g. ``tool_names``).
"""

from __future__ import annotations

#: The 13 canonical categories of sensitive actions. Order is stable for JSON
#: serialization of the preset ``categories`` maps. The admin-panel UI mirrors
#: this list with labels (``approval-policy/page.tsx``); keep them in sync.
APPROVAL_CATEGORIES: tuple[str, ...] = (
    "code_changes",
    "git_commit",
    "git_push",
    "external_http_get",
    "external_http_post",
    "secrets_access",
    "data_migration",
    "production_deploy",
    "infra_provision",
    "secret_rotation",
    "external_communication",
    "data_export_pii",
    "user_management",
)
