"""Tool classification — research vs producing vs read-only (refactor P5).

Pure predicates over tool NAMES (namespace-stripped) that the agent loop's
safeguards key on: what counts as research (novelty tracking), what counts as
producing (the ``has_produced`` latch), what is read-only (exempt from the hard
repetitive-loop abort) and which tool failures are the PLATFORM's fault (they
must not accumulate sterility).

`agent_runtime.graph` re-exports everything here (its historical home).
"""

from __future__ import annotations

from typing import Any

# Read/search tools that gather context but produce no deliverable. A run that
# only calls these is researching, not making progress.
# G4a (ADR 0103): search_code is a READ-ONLY inspection tool — it earns novelty like
# any research call and is NOT a mutator (so repeating it can't hard-abort the run).
_RESEARCH_TOOLS = frozenset(
    {"list_files", "read_file", "memory_recall", "rag_search", "search_code"}
)
# Tools that produce/modify the deliverable — calling one means real progress
# (and that the agent HAS produced, which changes the nudge from "write" to "finish").
_PRODUCING_TOOLS = frozenset(
    {"write_file", "edit_file", "create_file", "shell_exec", "stack_exec", "apply_patch"}
)


def _base_tool_name(tool: str | None) -> str:
    """The tool name without its MCP/custom namespace (``filesystem.write_file`` →
    ``write_file``).

    Audit cluster C2 (F24): production/research classification matched bare
    builtin names only, so a file written via an MCP server (``fs.write_file``)
    or a namespaced custom tool was invisible — ``has_produced`` never latched and
    the self-review saw no code, escalating a run that DID produce. Stripping the
    namespace lets the same writer verbs count whatever wires them.
    """
    return (tool or "").rsplit(".", 1)[-1]


def _is_research_tool(tool: str | None) -> bool:
    return _base_tool_name(tool) in _RESEARCH_TOOLS


def _read_target(tool: str | None, args: dict[str, Any]) -> str | None:
    """The normalized target a research call reads, or ``None``.

    A NEW target is exploration (progress toward understanding); a REPEATED target
    is read-churn. Cosmetic ``offset``/``limit`` are ignored so paging the same file
    does not masquerade as a new target. Namespace-stripped so an MCP/custom reader
    counts the same as the builtin."""
    base = _base_tool_name(tool)
    if base in {"read_file", "list_files"}:
        return f"{base}:{args.get('path') or '.'}"
    if base in {"rag_search", "memory_recall"}:
        query = str(args.get("query") or "").strip()
        return f"{base}:{query}" if query else None
    if base == "search_code":  # G4a (ADR 0103)
        needle = str(args.get("query") or args.get("pattern") or "").strip()
        return f"{base}:{needle}" if needle else None
    return None


def _is_producing_tool(tool: str | None) -> bool:
    return _base_tool_name(tool) in _PRODUCING_TOOLS


# Read-only / idempotent tools EXEMPT from the hard repetitive-loop abort (Tema C):
# repeating them wastes turns but cannot corrupt the deliverable, so they only earn
# the repetition nudge (B1) and are bounded by max_iterations/wall_clock — never a
# hard abort. The research/inspection tools are the read-only allowlist.
_READONLY_TOOLS = _RESEARCH_TOOLS


def _is_readonly_tool(tool: str | None) -> bool:
    return _base_tool_name(tool) in _READONLY_TOOLS


def _is_mutating_tool(tool: str | None) -> bool:
    """Whether repeating ``tool`` could change the deliverable (Tema C).

    A MUTATOR (a producing tool, OR any unknown/unclassified verb — conservative by
    default, e.g. ``echo``) trips the hard repetitive-loop abort/escalation; a known
    READ-ONLY tool (``_READONLY_TOOLS``) does NOT — repeating it merely wastes turns,
    which the iteration / wall-clock budgets already bound, so it gets the B1 nudge
    instead. Defaulting unknowns to MUTATING preserves the existing hard-abort
    guarantee (a runaway writer — or any non-read-only verb — is always caught).
    """
    return not _is_readonly_tool(tool)


# G3b (ADR 0103): substrings that mark a tool failure as the PLATFORM's fault (the
# tool was denied / absent / has no executor, or the filesystem refused) rather than
# the agent's own bad input (a guessed non-existent path). A platform failure must not
# accumulate sterility — the agent did nothing wrong. A file-not-found on a path the
# agent GUESSED still counts as sterile churn (anti-gaming, r5a).
_PLATFORM_ERROR_MARKERS: tuple[str, ...] = (
    "not allowed",
    "unknown tool",
    "no executor",
    "not registered",
    "permission denied",
    "eacces",
    "read-only file system",
    "worktree is empty",
)


def _is_platform_error(observation: Any) -> bool:
    err = observation.get("error") if isinstance(observation, dict) else None
    if not err:
        return False
    low = str(err).lower()
    return any(marker in low for marker in _PLATFORM_ERROR_MARKERS)
