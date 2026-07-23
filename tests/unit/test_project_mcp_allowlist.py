"""ADR 0128 — MCP tools are a PROJECT capability contributed to the run's
allowlist, not a per-agent grant. Unit tests for the pure allowlist-extension
logic (`extend_allowlist_with_project_mcp`)."""

from __future__ import annotations

from api_server.agent_tools_enforcement import extend_allowlist_with_project_mcp


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
