"""ADR 0128 — MCP tools are a PROJECT capability contributed to the run's
allowlist, not a per-agent grant. Unit tests for the pure allowlist-extension
logic (`extend_allowlist_with_project_mcp`)."""

from __future__ import annotations

from api_server.agent_tools_enforcement import (
    compute_effective_tools,
    extend_allowlist_with_project_mcp,
    filter_mcp_tools_by_role_policy,
)


def test_unrestricted_agent_stays_unrestricted() -> None:
    # base None = agent has no per-agent restriction; it can already call every
    # registered tool (incl. the project's MCP tools). Adding names would wrongly
    # turn it into a restricted allowlist.
    assert extend_allowlist_with_project_mcp(None, {"context7.query_docs"}) is None


def test_restricted_agent_gets_project_mcp_tools_added() -> None:
    base = ["read_file", "stack_exec"]
    out = extend_allowlist_with_project_mcp(
        base, {"context7.query_docs", "context7.resolve_library_id"}
    )
    assert out == sorted(
        {"read_file", "stack_exec", "context7.query_docs", "context7.resolve_library_id"}
    )


def test_no_project_mcp_leaves_base_untouched() -> None:
    base = ["read_file", "stack_exec"]
    assert extend_allowlist_with_project_mcp(base, set()) == sorted(base)


def test_result_is_deduplicated_and_sorted() -> None:
    base = ["stack_exec", "context7.query_docs"]
    out = extend_allowlist_with_project_mcp(base, {"context7.query_docs"})
    assert out == sorted({"stack_exec", "context7.query_docs"})
    assert len(out) == len(set(out))


# ---------------------------------------------------------------------------
# ADR 0128 fase 2 — política OPCIONAL rol→tool a nivel de proyecto. Un tool con
# entrada en la política se restringe a esos roles; un tool SIN entrada queda
# abierto a todos (default). role=None o política vacía => sin filtrado.
# ---------------------------------------------------------------------------
_TOOLS = frozenset(
    {"context7.query_docs", "atlassian.jira_search", "atlassian.confluence_create_page"}
)


def test_no_policy_passes_all_tools() -> None:
    assert filter_mcp_tools_by_role_policy(_TOOLS, None, "backend_dev") == _TOOLS
    assert filter_mcp_tools_by_role_policy(_TOOLS, {}, "backend_dev") == _TOOLS


def test_role_none_disables_filtering() -> None:
    policy = {"atlassian.jira_search": ["project_manager"]}
    assert filter_mcp_tools_by_role_policy(_TOOLS, policy, None) == _TOOLS


def test_listed_tool_restricted_to_its_roles() -> None:
    policy = {"atlassian.jira_search": ["project_manager"]}
    # PM keeps jira; backend loses jira but keeps the UNLISTED tools (open).
    assert filter_mcp_tools_by_role_policy(_TOOLS, policy, "project_manager") == _TOOLS
    assert filter_mcp_tools_by_role_policy(_TOOLS, policy, "backend_dev") == (
        _TOOLS - {"atlassian.jira_search"}
    )


def test_unlisted_tools_stay_open_when_policy_present() -> None:
    policy = {"atlassian.confluence_create_page": ["technical_writer"]}
    out = filter_mcp_tools_by_role_policy(_TOOLS, policy, "backend_dev")
    assert "context7.query_docs" in out  # unlisted → open
    assert "atlassian.jira_search" in out  # unlisted → open
    assert "atlassian.confluence_create_page" not in out  # listed, backend not allowed


# ---------------------------------------------------------------------------
# ADR 0128 fase 3 — the honest effective set (compute_effective_tools) folds in
# the project's MCP tools for a RESTRICTED agent, mirroring the dispatch UNION,
# so the diagnostic does not hide a tool the agent can actually call.
# ---------------------------------------------------------------------------
def test_effective_set_includes_project_mcp_for_restricted_agent() -> None:
    result = compute_effective_tools(
        ["read_file"],
        None,
        mode_name=None,
        shell_exec_assigned=False,
        allowed_commands_non_empty=False,
        wired_canonical_names={"read_file"},
        project_mcp_tool_names={"docling.convert"},
    )
    assert result.unrestricted is False
    assert set(result.effective) == {"read_file", "docling.convert"}


def test_project_mcp_ignored_for_unrestricted_agent() -> None:
    # assigned_names None = no per-agent restriction. The runtime already exposes
    # every registered tool (incl. project MCP), so effective stays empty by
    # design — mirroring extend_allowlist_with_project_mcp(None, …) -> None.
    result = compute_effective_tools(
        None,
        None,
        mode_name=None,
        shell_exec_assigned=False,
        allowed_commands_non_empty=False,
        project_mcp_tool_names={"docling.convert"},
    )
    assert result.unrestricted is True
    assert result.effective == []


def test_project_mcp_prevents_empty_effective_warning_in_mode() -> None:
    # An agent restricted to a tool the mode excludes would have an empty set,
    # but the project MCP tools it can still call make the set non-empty — so no
    # "empty effective set in mode" warning fires.
    result = compute_effective_tools(
        ["read_file"],
        [],  # mode allows nothing → read_file drops out of the intersection
        mode_name="discussion",
        shell_exec_assigned=False,
        allowed_commands_non_empty=False,
        wired_canonical_names={"read_file"},
        project_mcp_tool_names={"docling.convert"},
    )
    assert result.effective == ["docling.convert"]
    assert result.warnings == []
