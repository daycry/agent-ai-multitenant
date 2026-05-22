"""Placeholder builtin tools (task_02_19).

memory_recall, memory_store and document_convert belong in the tool
catalogue, but their backends do not exist yet — persistent memory and
RAG arrive in Plan 04, Docling document conversion likewise. Until
then each returns a failed `ToolResult` carrying HTTP 501 (Not
Implemented) semantics, so an agent that calls one gets a clear,
structured "not yet" instead of a crash or a silent no-op.
"""

from __future__ import annotations

from agent_runtime.tools import ToolFn, ToolRegistry, ToolResult

# HTTP 501 — the status these tools report until they are implemented.
NOT_IMPLEMENTED_CODE = 501

# Placeholder tool name -> the plan that will implement it.
PLACEHOLDER_TOOLS: dict[str, str] = {
    "memory_recall": "Plan 04 (memory + RAG)",
    "memory_store": "Plan 04 (memory + RAG)",
    "document_convert": "Plan 04 (Docling ingestion)",
}


def make_placeholder_tool(name: str) -> ToolFn:
    """Build a placeholder tool that always reports 501 Not Implemented."""
    available_in = PLACEHOLDER_TOOLS.get(name, "a later plan")

    def _placeholder(args: dict[str, object]) -> ToolResult:  # noqa: ARG001
        return ToolResult(
            ok=False,
            error=f"{name} is not implemented yet — arrives in {available_in}",
            output={
                "code": NOT_IMPLEMENTED_CODE,
                "tool": name,
                "available_in": available_in,
            },
        )

    return _placeholder


def register_placeholder_tools(registry: ToolRegistry) -> None:
    """Register every placeholder tool onto `registry`."""
    for name in PLACEHOLDER_TOOLS:
        registry.register(name, make_placeholder_tool(name))
