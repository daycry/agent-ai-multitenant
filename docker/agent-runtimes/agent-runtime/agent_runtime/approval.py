"""The human-approval gate inside the agent loop (task_02_33).

The agent loop runs sandboxed — it has no DB and cannot reach the
api-server's approval engine. So the gate works on data alone: the
worker passes the project's `human_approval_policy` into the task spec,
and the loop checks each tool call against it *before* the tool runs.

When a tool's category is marked `human_required`, the `plan` node
stops the loop with status `awaiting_human_approval` instead of acting.
The worker (task_02_30) turns that into a real `ApprovalRequest` row.

`requires_human` mirrors `api_server.db.approval_repo.requires_human_
approval` — the policy contract, not importable across the sandbox.
"""

from __future__ import annotations

from typing import Any

from shared_domain.tool_names import to_canonical

# Builtin tool → sensitive-action category. Keyed on CANONICAL tool names
# (ADR 0048); a tool absent from this map is not sensitive and is never gated.
#
# The VALUES are the canonical categories of shared_domain.approval_categories
# (the same vocabulary the policy presets are seeded with). They used to be
# code_execution/file_write/network_access/agent_delegation, which intersected
# NONE of the 13 preset categories, so requires_human always returned auto and
# nothing was ever gated (audit 2026-07-03, g6, fail-open). test_approval_gate_
# categories pins every value here to APPROVAL_CATEGORIES. NOTE: agent_invoke is
# gated as code_changes for now; a dedicated `agent_delegation` canonical
# category (with its UI/preset support) is deferred to prod-03.
DEFAULT_TOOL_CATEGORIES: dict[str, str] = {
    "shell_exec": "code_changes",
    "stack_exec": "code_changes",
    "write_file": "code_changes",
    # prod-03 A8 (auditoría 2026-07-06): estas tools estaban wired pero SIN
    # categoría, así que escapaban al gate incluso bajo customer-external.
    "delete_file": "code_changes",  # destructiva sobre el worktree (como write_file)
    "run_pytest": "code_changes",  # ejecutan código arbitrario del repo
    "run_lint": "code_changes",
    "run_typecheck": "code_changes",
    "run_build": "code_changes",
    "send_notification": "external_communication",  # el preset promete gatear comunicación
    "http_get": "external_http_get",
    "http_post": "external_http_post",
    "agent_invoke": "code_changes",
}


def requires_human(policy: dict[str, Any] | None, category: str) -> bool:
    """True if `category` needs a human under this project's policy.

    The policy JSONB is `{"categories": {<category>: "auto" |
    "human_required"}}` (a bare `{<category>: ...}` map is also
    accepted). An unlisted category defaults to `auto`.
    """
    if not policy:
        return False
    categories = policy.get("categories", policy)
    if not isinstance(categories, dict):
        return False
    return str(categories.get(category, "auto")) == "human_required"


class ApprovalGate:
    """Decides whether a tool call must pause for human approval."""

    def __init__(
        self,
        policy: dict[str, Any] | None,
        tool_categories: dict[str, str] | None = None,
    ) -> None:
        self._policy = policy
        self._tool_categories = tool_categories or DEFAULT_TOOL_CATEGORIES

    def review(self, tool: str | None) -> str | None:
        """Return the sensitive category gating `tool`, or None if the
        tool may run without approval."""
        if not tool:
            return None
        # Resolve legacy aliases (file_write → write_file, http_request →
        # http_get/http_post) to canonical names (ADR 0048) before lookup, so a
        # sensitive call cannot slip past the gate by mere name mismatch.
        for canonical in to_canonical(tool):
            category = self._tool_categories.get(canonical)
            if category is not None and requires_human(self._policy, category):
                return category
        return None
