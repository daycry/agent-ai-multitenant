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

# Builtin tool → sensitive-action category (spec §7.7). Keyed on CANONICAL
# tool names (ADR 0048) so it matches what the runtime registers; a tool
# absent from this map is not sensitive and is never gated.
DEFAULT_TOOL_CATEGORIES: dict[str, str] = {
    "shell_exec": "code_execution",
    "stack_exec": "code_execution",
    "write_file": "file_write",
    "http_get": "network_access",
    "http_post": "network_access",
    "agent_invoke": "agent_delegation",
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
