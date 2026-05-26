"""Docling tool adapters (Plan 04.5 task_04_5_05).

* ``document_convert`` — replaces the last 501 placeholder from
  Plan 02 task_02_19. v1 reads the chunks of an existing Document
  (a fast DB lookup); a full re-parse path lands when
  chat-file-upload arrives in Plan 07.
* ``promote_to_kb`` — copies an existing Document into another KB
  the agent's project also has access to. Source and target KB
  grants both gate the call.
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
class DoclingTools:
    """``document_convert`` + ``promote_to_kb`` bound to one HTTP client."""

    api: InternalAgentAPI

    def document_convert(self, args: dict[str, Any]) -> ToolResult:
        document_id = args.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            return ToolResult(
                ok=False,
                error="document_convert requires a non-empty 'document_id' string",
            )
        try:
            out = self.api.document_convert(document_id=document_id)
        except InternalAPIHTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"document_convert HTTP {exc.status_code}: {exc.body[:200]}",
                output={"status_code": exc.status_code},
            )
        except InternalAPIError as exc:
            return ToolResult(ok=False, error=f"document_convert failed: {exc}")
        return ToolResult(ok=True, output=out)

    def promote_to_kb(self, args: dict[str, Any]) -> ToolResult:
        validated = _validate_promote_args(args)
        if isinstance(validated, ToolResult):
            return validated
        document_id, target_kb_id, title = validated
        try:
            out = self.api.promote_to_kb(
                document_id=document_id, target_kb_id=target_kb_id, title=title
            )
        except InternalAPIHTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"promote_to_kb HTTP {exc.status_code}: {exc.body[:200]}",
                output={"status_code": exc.status_code},
            )
        except InternalAPIError as exc:
            return ToolResult(ok=False, error=f"promote_to_kb failed: {exc}")
        return ToolResult(ok=True, output=out)


def _validate_promote_args(
    args: dict[str, Any],
) -> tuple[str, str, str | None] | ToolResult:
    document_id = args.get("document_id")
    if not isinstance(document_id, str) or not document_id.strip():
        return ToolResult(ok=False, error="promote_to_kb requires a non-empty 'document_id' string")
    target_kb_id = args.get("target_kb_id")
    if not isinstance(target_kb_id, str) or not target_kb_id.strip():
        return ToolResult(
            ok=False, error="promote_to_kb requires a non-empty 'target_kb_id' string"
        )
    title = args.get("title")
    if title is not None and not isinstance(title, str):
        return ToolResult(ok=False, error="promote_to_kb 'title' must be a string if given")
    return document_id, target_kb_id, title


def register_docling_tools(registry: ToolRegistry, api: InternalAgentAPI) -> None:
    """Register ``document_convert`` + ``promote_to_kb`` on `registry`.

    Replaces the 501 placeholder previously registered under
    ``document_convert``.
    """
    tools = DoclingTools(api)
    registry.register("document_convert", tools.document_convert)
    registry.register("promote_to_kb", tools.promote_to_kb)


__all__ = ["DoclingTools", "register_docling_tools"]
