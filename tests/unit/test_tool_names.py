"""Unit tests for the canonical tool-name source of truth (ADR 0048, task_06_18_03).

The catalog, chat-modes and runtime historically used three divergent names for
the same logical action (``read_file`` / ``file_read`` / ``file_read``). This
module is the single source of truth: the **canonical** names are the catalog
names the operator sees and assigns, and a retro-compatible **alias** layer maps
the legacy chat-mode/runtime names onto them so intersections stop coming out
empty by mere name mismatch. ``http_request`` (chat-mode) is the one alias that
expands to *both* HTTP verbs (``http_get`` + ``http_post``).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_canonical_names_are_the_catalog_names() -> None:
    from shared_domain.tool_names import CANONICAL_TOOL_NAMES

    # The catalog names the operator sees/assigns ARE the canonical ones.
    for name in (
        "read_file",
        "write_file",
        "list_files",
        "http_get",
        "http_post",
        "send_notification",
    ):
        assert name in CANONICAL_TOOL_NAMES, name
    # The legacy chat-mode names are NOT canonical (they are aliases).
    for legacy in ("file_read", "file_write", "file_list", "http_request", "notify_user"):
        assert legacy not in CANONICAL_TOOL_NAMES, legacy


def test_alias_resolves_to_canonical() -> None:
    from shared_domain.tool_names import to_canonical

    assert to_canonical("file_read") == frozenset({"read_file"})
    assert to_canonical("file_write") == frozenset({"write_file"})
    assert to_canonical("file_list") == frozenset({"list_files"})
    assert to_canonical("notify_user") == frozenset({"send_notification"})


def test_canonical_name_is_idempotent() -> None:
    from shared_domain.tool_names import to_canonical

    # A name already canonical resolves to itself.
    assert to_canonical("read_file") == frozenset({"read_file"})
    assert to_canonical("shell_exec") == frozenset({"shell_exec"})


def test_http_request_alias_expands_to_both_verbs() -> None:
    from shared_domain.tool_names import to_canonical

    assert to_canonical("http_request") == frozenset({"http_get", "http_post"})


def test_unknown_name_passes_through_unchanged() -> None:
    from shared_domain.tool_names import to_canonical

    # A custom/MCP tool name we don't alias must pass through untouched.
    assert to_canonical("some_custom_tool") == frozenset({"some_custom_tool"})
    assert to_canonical("filesystem-mcp.read_file") == frozenset({"filesystem-mcp.read_file"})


def test_to_canonical_set_unions_and_expands() -> None:
    from shared_domain.tool_names import to_canonical_set

    # A mixed set of legacy + canonical names collapses to canonical names,
    # expanding http_request into both verbs.
    assert to_canonical_set(["file_read", "http_request"]) == frozenset(
        {"read_file", "http_get", "http_post"}
    )
    # Empty in, empty out.
    assert to_canonical_set([]) == frozenset()
