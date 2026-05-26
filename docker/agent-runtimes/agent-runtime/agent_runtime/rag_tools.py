"""RAG tool adapter (Plan 04.5 task_04_5_04).

Wraps :meth:`InternalAgentAPI.rag_search` into a ``ToolFn`` the
:class:`ToolRegistry` can dispatch as ``rag_search``. The tool is a
**net-new** addition: unlike memory_recall/store, ``rag_search`` was
never a placeholder — it lands here for the first time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_runtime.internal_api import (
    InternalAgentAPI,
    InternalAPIError,
    InternalAPIHTTPError,
)
from agent_runtime.tools import ToolRegistry, ToolResult


@dataclass
class RagTools:
    """``rag_search`` bound to one HTTP client."""

    api: InternalAgentAPI

    def rag_search(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(ok=False, error="rag_search requires a non-empty 'query' string")
        try:
            limit = int(args.get("limit", 5))
            recall_k = int(args.get("recall_k", 20))
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="rag_search 'limit'/'recall_k' must be ints")
        try:
            hits = self.api.rag_search(query=query, limit=limit, recall_k=recall_k)
        except InternalAPIHTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"rag_search HTTP {exc.status_code}: {exc.body[:200]}",
                output={"status_code": exc.status_code},
            )
        except InternalAPIError as exc:
            return ToolResult(ok=False, error=f"rag_search failed: {exc}")
        return ToolResult(ok=True, output={"hits": hits, "count": len(hits)})


def register_rag_tools(registry: ToolRegistry, api: InternalAgentAPI) -> None:
    """Register ``rag_search`` on `registry`."""
    tools = RagTools(api)
    registry.register("rag_search", tools.rag_search)


__all__ = ["RagTools", "register_rag_tools"]
