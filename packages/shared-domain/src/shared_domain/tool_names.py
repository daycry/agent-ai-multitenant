"""Canonical tool-name source of truth (ADR 0048, Plan 06.18 task_06_18_03).

Three layers historically used divergent names for the same logical action: the
**catalog** (``read_file`` — what the operator sees and assigns), the
**chat-modes** (``file_read``) and the **runtime** (``file_read``). The
per-agent ∩ chat-mode tool intersection is computed on raw strings, so a tool
assigned as ``read_file`` and allowed by a mode as ``file_read`` intersected to
the empty set — the silent "unknown tool" failure described in ADR 0048.

This module is the single source of truth. The **canonical** names are the
catalog names; a retro-compatible **alias** layer maps the legacy
chat-mode/runtime names onto them (no hard rename, so existing ``agent_tools``
rows and chat-mode allowlists keep working). ``http_request`` is the one alias
that expands to *both* HTTP verbs (``http_get`` + ``http_post``); every other
alias is one-to-one. Unknown names (tenant-custom tools, MCP ``<server>.<tool>``)
pass through unchanged.

Kept dependency-free in ``packages/shared-domain`` so api-server, orchestrator
and the agent-runtime can all import it (mirrors how ``shared-llm`` /
``shared-mcp`` are shared).
"""

from __future__ import annotations

from collections.abc import Iterable

# The catalog names the operator sees and assigns
# (``api_server.seeds.builtin_tools`` — 19 tools). A CI contract test
# (task_06_18_14) asserts this set stays in sync with the seed.
_CATALOG_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "apply_patch",
        "git_commit",
        "git_diff",
        "git_log",
        "git_status",
        "http_get",
        "http_post",
        "list_files",
        "read_file",
        "run_build",
        "run_lint",
        "run_pytest",
        "run_typecheck",
        "search_code",
        "semantic_search",
        "send_notification",
        "shell_exec",
        "summarize_text",
        "write_file",
    }
)

# Orchestration tools the runtime registers under the SAME name in every layer
# (catalog absent, but chat-modes + runtime agree) — canonical, no alias needed.
_ORCHESTRATION_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "kanban_update",
        "task_comment",
        "agent_invoke",
    }
)

#: The full set of canonical tool names known to the platform.
CANONICAL_TOOL_NAMES: frozenset[str] = _CATALOG_TOOL_NAMES | _ORCHESTRATION_TOOL_NAMES

# Legacy alias -> canonical name(s). Retro-compatible (ADR 0048): the chat-mode
# and runtime namespaces resolve onto the catalog names through this map.
_ALIAS_TO_CANONICAL: dict[str, frozenset[str]] = {
    "file_read": frozenset({"read_file"}),
    "file_write": frozenset({"write_file"}),
    "file_list": frozenset({"list_files"}),
    "http_request": frozenset({"http_get", "http_post"}),
    "notify_user": frozenset({"send_notification"}),
}


def to_canonical(name: str) -> frozenset[str]:
    """Resolve a tool name to its canonical name(s).

    Returns a ``frozenset`` because one legacy alias (``http_request``) expands
    to both HTTP verbs. A name that is already canonical — or one we do not
    alias (tenant-custom / MCP ``<server>.<tool>``) — resolves to itself.
    """
    if name in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[name]
    return frozenset({name})


def to_canonical_set(names: Iterable[str]) -> frozenset[str]:
    """Canonicalise a collection of names, unioning and expanding aliases."""
    out: set[str] = set()
    for name in names:
        out |= to_canonical(name)
    return frozenset(out)


__all__ = ["CANONICAL_TOOL_NAMES", "to_canonical", "to_canonical_set"]
