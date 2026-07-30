"""memory_recall must constrain `scopes` to the valid enum (regression 2026-06-26).

The model repeatedly sent invalid scopes ("project", "error") because the schema
left `items` unconstrained → the internal API rejected them with HTTP 422, burning
a model call each time. The advertised schema now pins the exact enum.
"""

from __future__ import annotations

from workers.agent_tool_schemas import _RUNTIME_ONLY_SCHEMAS

_VALID_SCOPES = {"private", "team_shared", "project_shared", "global"}


def test_memory_recall_scopes_items_have_the_valid_enum() -> None:
    schema = _RUNTIME_ONLY_SCHEMAS["memory_recall"]
    items = schema["parameters"]["properties"]["scopes"]["items"]
    assert set(items["enum"]) == _VALID_SCOPES
    assert items["type"] == "string"
