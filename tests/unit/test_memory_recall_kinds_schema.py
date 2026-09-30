"""task_mem_04 — el esquema de `memory_recall` anuncia `kinds` con el vocabulario cerrado."""

from __future__ import annotations

import pytest
from api_server.memorizer.kinds import MEMORY_KINDS
from workers.agent_tool_schemas import _RUNTIME_ONLY_SCHEMAS

pytestmark = pytest.mark.unit


def test_memory_recall_advertises_an_optional_kinds_enum() -> None:
    schema = _RUNTIME_ONLY_SCHEMAS["memory_recall"]["parameters"]
    kinds = schema["properties"]["kinds"]
    assert kinds["type"] == "array"
    assert tuple(kinds["items"]["enum"]) == MEMORY_KINDS
    assert "kinds" not in schema["required"]
