"""Placeholder builtin tools (task_02_19).

Tools whose backend doesn't exist yet answer with a structured 501.

History:

  * task_02_19 introduced this with three placeholders: memory_recall,
    memory_store and document_convert.
  * Plan 04.5 task_04_5_03 replaced memory_recall + memory_store with
    real adapters that call ``/internal/agent/*`` (see
    :mod:`agent_runtime.memory_tools`).
  * document_convert moves out next in task_04_5_05.

So this module currently only holds ``document_convert``.
"""

from __future__ import annotations

from agent_runtime.tools import ToolFn, ToolRegistry, ToolResult

# HTTP 501 — the status these tools report until they are implemented.
NOT_IMPLEMENTED_CODE = 501

# Placeholder tool name -> the plan that will implement it.
PLACEHOLDER_TOOLS: dict[str, str] = {
    "document_convert": "Plan 04.5 task_04_5_05 (Docling ingestion wire-up)",
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
