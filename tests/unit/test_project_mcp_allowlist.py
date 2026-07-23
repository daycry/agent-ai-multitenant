"""ADR 0128 — MCP tools are a PROJECT capability contributed to the run's
allowlist, not a per-agent grant. Unit tests for the pure allowlist-extension
logic (`extend_allowlist_with_project_mcp`)."""

from __future__ import annotations

from api_server.agent_tools_enforcement import (
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
