"""memory_recall coerces model-guessed scopes instead of 422-ing (regression 2026-06-26).

Sonnet ignored the schema enum and sent scopes like ["project", "error"], which the
internal API rejected with HTTP 422 — wasting a whole agent turn. The adapter now
maps common aliases and drops the rest before the HTTP call.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.memory_tools import MemoryTools, _coerce_scopes
from agent_runtime.tools import ToolResult


def test_coerce_maps_aliases_and_drops_unknowns() -> None:
    assert _coerce_scopes(["project", "error"]) == ["project_shared"]
    assert _coerce_scopes(["TEAM", "global"]) == ["team_shared", "global"]
    assert _coerce_scopes(["nonsense"]) == []  # all invalid → empty (no filter)
    assert _coerce_scopes(["private", "private"]) == ["private"]  # de-duped


class _FakeAPI:
    """Captures the scopes the adapter actually forwards to the HTTP call."""

    def __init__(self) -> None:
        self.seen_scopes: Any = "unset"

    def memory_recall(self, **kwargs: Any) -> list[dict[str, Any]]:
        # The adapter calls api.memory_recall(query=…, scopes=…, limit=…) by keyword.
        self.seen_scopes = kwargs.get("scopes")
        return []


def test_memory_recall_forwards_coerced_scopes() -> None:
    api = _FakeAPI()
    result = MemoryTools(api=api).memory_recall(  # type: ignore[arg-type]
        {"query": "migraciones", "scopes": ["project", "error"]}
    )
    assert isinstance(result, ToolResult) and result.ok
    assert api.seen_scopes == ["project_shared"]  # not the raw ["project","error"] → no 422


def test_memory_recall_all_invalid_scopes_become_no_filter() -> None:
    api = _FakeAPI()
    MemoryTools(api=api).memory_recall({"query": "q", "scopes": ["bogus"]})  # type: ignore[arg-type]
    assert api.seen_scopes is None  # empty after coercion → no scope filter, not a 422
