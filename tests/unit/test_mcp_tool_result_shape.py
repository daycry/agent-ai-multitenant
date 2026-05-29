"""Unit guard for the MCPToolResult shape (Plan 06.14 task_06_14_15, mcp-tools-3).

The `raw` field that used to carry the server's full, untrusted JSON-RPC
payload was dropped because nothing read it and it would leak if a result
were ever logged or persisted. These tests pin that contract so a future
refactor can't silently reintroduce the leak.
"""

from __future__ import annotations

import dataclasses

import pytest
from shared_mcp.types import MCPToolResult

pytestmark = pytest.mark.unit


def test_mcp_tool_result_has_no_raw_field() -> None:
    fields = {f.name for f in dataclasses.fields(MCPToolResult)}
    assert fields == {"content", "is_error"}
    assert "raw" not in fields


def test_mcp_tool_result_constructs_with_minimal_fields() -> None:
    result = MCPToolResult(content="hello")
    assert result.content == "hello"
    assert result.is_error is False
    assert not hasattr(result, "raw")


def test_mcp_tool_result_rejects_raw_kwarg() -> None:
    # Passing the old `raw=` kwarg must now be a hard error, not a
    # silently-ignored field.
    with pytest.raises(TypeError):
        MCPToolResult(content="x", raw={"leak": "me"})  # type: ignore[call-arg]
