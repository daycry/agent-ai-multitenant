"""Production/research tool classification is namespace-aware (audit C2 / F24).

A file written via an MCP server (``filesystem.write_file``) or a namespaced
custom tool must count as production so ``has_produced`` latches and the
self-review judges the real code instead of escalating a run that DID produce.
"""

from __future__ import annotations

from agent_runtime.graph import _base_tool_name, _is_producing_tool, _is_research_tool


def test_strips_namespace() -> None:
    assert _base_tool_name("filesystem.write_file") == "write_file"
    assert _base_tool_name("write_file") == "write_file"
    assert _base_tool_name(None) == ""


def test_namespaced_writer_is_producing() -> None:
    assert _is_producing_tool("fs.write_file")
    assert _is_producing_tool("write_file")
    assert _is_producing_tool("mytool.create_file")
    assert _is_producing_tool("shell_exec")
    assert not _is_producing_tool("read_file")
    assert not _is_producing_tool("kb.rag_search")


def test_namespaced_research_is_research() -> None:
    assert _is_research_tool("kb.rag_search")
    assert _is_research_tool("read_file")
    assert not _is_research_tool("write_file")
    assert not _is_research_tool("fs.write_file")
