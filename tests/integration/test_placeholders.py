"""Tests for the placeholder builtin tools machinery (task_02_19).

Originally three tools (memory_recall, memory_store, document_convert)
were placeholders. Plan 04.5 replaced all three with real wire-ups
(task_04_5_03 / task_04_5_05), so the catalogue is empty in v1. The
factory + register-on-registry helpers stay, so adding a future
placeholder is a one-line edit.
"""

from __future__ import annotations

import pytest
from agent_runtime.placeholder_tools import (
    NOT_IMPLEMENTED_CODE,
    PLACEHOLDER_TOOLS,
    make_placeholder_tool,
    register_placeholder_tools,
)
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration


def test_placeholder_catalogue_is_empty() -> None:
    """Every original placeholder has been wired up — the catalogue
    is empty by design. The factory still exists for future use."""
    assert PLACEHOLDER_TOOLS == {}


def test_placeholder_factory_still_returns_501_for_a_synthetic_name() -> None:
    """The factory is dormant but functional. Calling
    `make_placeholder_tool` for any name yields a 501 ToolFn — useful
    if a future plan wants to introduce a new placeholder."""
    result = make_placeholder_tool("future_tool")({})
    assert result.ok is False
    assert result.output["code"] == NOT_IMPLEMENTED_CODE == 501
    assert result.output["tool"] == "future_tool"
    assert "a later plan" in (result.error or "")


def test_register_placeholder_tools_is_a_noop_when_catalogue_empty() -> None:
    """No placeholders → register_placeholder_tools doesn't touch the
    registry. A real test of registration arrives the next time
    someone adds a placeholder."""
    registry = ToolRegistry()
    register_placeholder_tools(registry)
    assert registry.names() == []
