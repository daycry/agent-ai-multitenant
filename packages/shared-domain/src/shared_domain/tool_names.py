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
# (``api_server.seeds.builtin_tools``). A CI contract test (task_06_18_14)
# asserts this set stays in sync with the seed.
#
# The ``git_*`` family was retired from the seed in task_06_18_06 (ADR 0049):
# there is no ``register_git_tools`` executor, so offering it as assignable
# would lie about availability. It is intentionally absent from the catalog
# set below until a runtime executor exists.
_CATALOG_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "apply_patch",
        "delete_file",
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
#
# ``semantic_search`` is the catalog/knowledge name the operator assigns; the
# runtime executes it as ``rag_search`` (``RagTools.rag_search`` registered by
# ``register_builtin_families``). task_06_18_06 reconciles the two in this
# single source of truth so an assigned ``semantic_search`` resolves to the
# executable ``rag_search`` instead of dying as ``unknown tool`` (ADR 0049).
_ALIAS_TO_CANONICAL: dict[str, frozenset[str]] = {
    "file_read": frozenset({"read_file"}),
    "file_write": frozenset({"write_file"}),
    "file_delete": frozenset({"delete_file"}),
    "file_list": frozenset({"list_files"}),
    "http_request": frozenset({"http_get", "http_post"}),
    "notify_user": frozenset({"send_notification"}),
    "semantic_search": frozenset({"rag_search"}),
}


# ---------------------------------------------------------------------------
# Runtime-wired set (ADR 0049, task_06_18_06)
# ---------------------------------------------------------------------------
# The names the agent-runtime can actually REGISTER and execute. This is the
# single source of truth the api-server consults to derive ``is_runtime_wired``
# on ``ToolResponse`` — so the catalog never offers as assignable something
# that ends up a silent ``unknown tool``. It mirrors, by canonical name, what
# the runtime boot path registers (a CI contract test in task_06_18_14 asserts
# the two stay in sync):
#
#   * builtin families (``register_builtin_families``): file / network /
#     orchestration / notification / knowledge (``rag_search`` + Docling) /
#     memory.
#   * the ``run_*`` ``docker_command`` tools wired from the serialised
#     ``tool_specs`` (``register_tool_specs``).
#   * ``shell_exec`` wired per project from ``allowed_commands`` (Plan 06.16).
#
# NOT wired (hence ``is_runtime_wired`` False): ``git_*`` (no executor —
# retired from the seed), ``apply_patch`` / ``search_code`` (the file family
# registers only read/write/list) and ``summarize_text`` (no executor yet).
RUNTIME_WIRED_TOOL_NAMES: frozenset[str] = frozenset(
    {
        # file family
        "read_file",
        "write_file",
        "delete_file",
        "list_files",
        # network family
        "http_get",
        "http_post",
        # orchestration family
        "kanban_update",
        "task_comment",
        "agent_invoke",
        # notification family
        "send_notification",
        # knowledge family (semantic_search aliases onto rag_search)
        "rag_search",
        "document_convert",
        "promote_to_kb",
        # memory family
        "memory_recall",
        "memory_store",
        # run_* docker_command tools
        "run_pytest",
        "run_lint",
        "run_typecheck",
        "run_build",
        # per-project shell
        "shell_exec",
    }
)


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


def is_runtime_wired(name: str) -> bool:
    """Whether a tool ``name`` resolves to something the runtime can execute.

    Resolves the name through the alias layer first (so the catalog
    ``semantic_search`` counts via ``rag_search``, and a legacy ``file_read``
    via ``read_file``) and reports whether ANY resulting canonical name is in
    :data:`RUNTIME_WIRED_TOOL_NAMES`. A tenant-custom / MCP name that is not
    aliased resolves to itself and is wired only if it literally appears in the
    set (custom tools become wired through their own ``implementation_type``
    handling, not this builtin set — they default to not-wired here).
    """
    return bool(to_canonical(name) & RUNTIME_WIRED_TOOL_NAMES)


__all__ = [
    "CANONICAL_TOOL_NAMES",
    "RUNTIME_WIRED_TOOL_NAMES",
    "is_runtime_wired",
    "to_canonical",
    "to_canonical_set",
]
