"""Memory tool adapters (Plan 04.5 task_04_5_03).

Wraps :class:`InternalAgentAPI` calls into the ``ToolFn`` shape the
:class:`ToolRegistry` expects. These replace the 501 placeholders
that ``placeholder_tools.py`` registered for ``memory_recall`` and
``memory_store`` once the platform-side endpoints are wired up.

The tools are intentionally **best-effort**: a network glitch or a
403 from the api-server folds into ``ToolResult(ok=False, error=...)``
so the agent loop keeps running. The model can read the error and
either retry or move on.
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

_VALID_SCOPES = ("private", "team_shared", "project_shared", "global")
# LLMs ignore the schema enum and send near-misses ("project", "team", "error"…),
# which the internal API rejects with HTTP 422 — burning a whole turn. We coerce
# the common intents and drop the rest instead of failing the call.
_SCOPE_ALIASES = {
    "project": "project_shared",
    "project_share": "project_shared",
    "team": "team_shared",
    "team_share": "team_shared",
    "shared": "project_shared",
    "org": "global",
    "organization": "global",
    "organisation": "global",
    "personal": "private",
    "self": "private",
}


def _coerce_scopes(scopes: list[str]) -> list[str]:
    """Normalise model-supplied scopes to the valid vocabulary.

    Lowercases, maps common aliases ("project" → "project_shared"), drops anything
    unknown, and de-dups preserving order. An empty result means "no scope filter"
    so the caller passes ``None`` and the API uses the available defaults — never a
    422 over a scope the model guessed wrong.
    """
    out: list[str] = []
    for raw in scopes:
        key = raw.strip().lower()
        mapped = key if key in _VALID_SCOPES else _SCOPE_ALIASES.get(key)
        if mapped and mapped not in out:
            out.append(mapped)
    return out


@dataclass
class MemoryTools:
    """``memory_recall`` + ``memory_store`` bound to one HTTP client."""

    api: InternalAgentAPI

    def memory_recall(self, args: dict[str, Any]) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(ok=False, error="memory_recall requires a non-empty 'query' string")
        scopes = args.get("scopes")
        if scopes is not None and not (
            isinstance(scopes, list) and all(isinstance(s, str) for s in scopes)
        ):
            return ToolResult(
                ok=False,
                error="memory_recall 'scopes' must be a list of strings if provided",
            )
        # Coerce model near-misses to the valid vocabulary; empty → no filter.
        if scopes is not None:
            scopes = _coerce_scopes(scopes) or None
        limit_raw = args.get("limit", 5)
        try:
            limit = int(limit_raw)
        except (TypeError, ValueError):
            return ToolResult(ok=False, error="memory_recall 'limit' must be an int")
        try:
            hits = self.api.memory_recall(query=query, scopes=scopes, limit=limit)
        except InternalAPIHTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"memory_recall HTTP {exc.status_code}: {exc.body[:200]}",
                output={"status_code": exc.status_code},
            )
        except InternalAPIError as exc:
            return ToolResult(ok=False, error=f"memory_recall failed: {exc}")
        return ToolResult(ok=True, output={"hits": hits, "count": len(hits)})

    def memory_store(self, args: dict[str, Any]) -> ToolResult:
        validated = _validate_store_args(args)
        if isinstance(validated, ToolResult):
            return validated
        content, type_, scope, tags = validated
        try:
            stored = self.api.memory_store(content=content, type_=type_, scope=scope, tags=tags)
        except InternalAPIHTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"memory_store HTTP {exc.status_code}: {exc.body[:200]}",
                output={"status_code": exc.status_code},
            )
        except InternalAPIError as exc:
            return ToolResult(ok=False, error=f"memory_store failed: {exc}")
        return ToolResult(ok=True, output=stored)


def _validate_store_args(
    args: dict[str, Any],
) -> tuple[str, str, str | None, list[str]] | ToolResult:
    """Parse + validate memory_store args. Returns the tuple on success
    or a failed ``ToolResult`` describing the first problem."""
    content = args.get("content")
    if not isinstance(content, str) or not content.strip():
        return ToolResult(ok=False, error="memory_store requires a non-empty 'content' string")
    type_ = args.get("type", "semantic")
    if type_ not in ("episodic", "semantic"):
        return ToolResult(ok=False, error="memory_store 'type' must be 'episodic' or 'semantic'")
    scope = args.get("scope")
    if scope is not None and not isinstance(scope, str):
        return ToolResult(ok=False, error="memory_store 'scope' must be a string")
    tags = args.get("tags") or []
    if not (isinstance(tags, list) and all(isinstance(t, str) for t in tags)):
        return ToolResult(ok=False, error="memory_store 'tags' must be a list of strings")
    return content, type_, scope, tags


def register_memory_tools(registry: ToolRegistry, api: InternalAgentAPI) -> None:
    """Register ``memory_recall`` + ``memory_store`` on `registry`.

    Replaces whatever was previously registered under those names
    (the 501 placeholders from ``placeholder_tools``).
    """
    tools = MemoryTools(api)
    registry.register("memory_recall", tools.memory_recall)
    registry.register("memory_store", tools.memory_store)


__all__ = ["MemoryTools", "register_memory_tools"]
