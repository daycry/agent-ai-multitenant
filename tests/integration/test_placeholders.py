"""Integration tests for the placeholder builtin tools (task_02_19).

memory_recall, memory_store and document_convert must answer with a
clear 501 until their backends arrive in Plan 04.
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


@pytest.mark.parametrize("name", ["memory_recall", "memory_store", "document_convert"])
def test_placeholder_tool_returns_501(name: str) -> None:
    result = make_placeholder_tool(name)({})
    assert result.ok is False
    assert result.output["code"] == NOT_IMPLEMENTED_CODE == 501
    assert result.output["tool"] == name


@pytest.mark.parametrize("name", ["memory_recall", "memory_store", "document_convert"])
def test_placeholder_names_its_target_plan(name: str) -> None:
    result = make_placeholder_tool(name)({})
    assert "Plan 04" in result.output["available_in"]
    assert "Plan 04" in (result.error or "")


def test_placeholder_ignores_whatever_args_it_is_given() -> None:
    tool = make_placeholder_tool("memory_recall")
    # Arbitrary arguments must not crash it — it still answers 501.
    result = tool({"query": "anything", "limit": 99, "nested": {"x": [1, 2]}})
    assert result.ok is False
    assert result.output["code"] == 501


def test_register_placeholder_tools_wires_all_three() -> None:
    registry = ToolRegistry()
    register_placeholder_tools(registry)
    assert (
        set(registry.names())
        == set(PLACEHOLDER_TOOLS)
        == {
            "memory_recall",
            "memory_store",
            "document_convert",
        }
    )


def test_registered_placeholder_is_callable_and_returns_501() -> None:
    registry = ToolRegistry()
    register_placeholder_tools(registry)
    result = registry.call("document_convert", {"path": "report.pdf"})
    assert result.ok is False
    assert result.output["code"] == 501
    assert result.output["tool"] == "document_convert"
