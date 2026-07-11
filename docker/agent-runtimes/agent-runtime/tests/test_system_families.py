"""Unit tests for the SYSTEM family wiring + allowlist exemption (H0/H3).

The runtime-only families (memory + orchestration) are NOT in the assignable
catalog, so they can never be in a per-agent allowlist. They must be wired
regardless of `agent_tools` and exempt from the allowlist, else no agent can
recall/store memory (H0) and assigning any tool silences the orchestrator (H3).
"""

from __future__ import annotations

from agent_runtime.builtin_families import (
    SYSTEM_FAMILY_TOOL_NAMES,
    register_system_families,
)
from agent_runtime.orchestration_tools import OrchestrationSink
from agent_runtime.tools import ToolRegistry


class _DummyApi:
    """Stand-in for InternalAgentAPI: MemoryTools only stores it at wiring."""


def test_register_system_families_wires_orchestration_and_memory() -> None:
    registry = ToolRegistry()
    registered = register_system_families(registry, api=_DummyApi(), sink=OrchestrationSink())
    assert set(registered) == {
        "memory_recall",
        "memory_store",
        "kanban_update",
        "task_comment",
        "agent_invoke",
        "rag_search",
    }
    assert set(registry.names()) >= SYSTEM_FAMILY_TOOL_NAMES


def test_register_system_families_skips_memory_without_api() -> None:
    """No internal-api token (bare run) → memory is skipped honestly, but the
    orchestration family (sink-only) is still wired."""
    registry = ToolRegistry()
    registered = register_system_families(registry, api=None, sink=OrchestrationSink())
    assert set(registered) == {"kanban_update", "task_comment", "agent_invoke"}
    assert "memory_recall" not in registry.names()


def test_register_system_families_honours_family_flags() -> None:
    registry = ToolRegistry()
    registered = register_system_families(
        registry,
        api=_DummyApi(),
        sink=OrchestrationSink(),
        flags={"orquestacion": False},
    )
    assert set(registered) == {"memory_recall", "memory_store", "rag_search"}
    assert "kanban_update" not in registry.names()


def test_effective_allowlist_adds_system_tools_to_a_real_restriction() -> None:
    from agent_runtime.__main__ import _effective_allowlist

    effective = _effective_allowlist(["read_file"])
    assert "read_file" in effective
    assert effective >= SYSTEM_FAMILY_TOOL_NAMES


def test_effective_allowlist_keeps_block_all_empty() -> None:
    from agent_runtime.__main__ import _effective_allowlist

    # An explicit empty allowlist (discussion mode) must stay empty — system
    # tools do not leak past block-all.
    assert _effective_allowlist([]) == frozenset()


# ---------------------------------------------------------------------------
# P0-3 (investigación 2026-07-11): la BÚSQUEDA en la KB es una capacidad de
# sistema. `rag_search` no estaba en SYSTEM_FAMILY_TOOL_NAMES, así que cualquier
# modo con whitelist que no incluyera `semantic_search` dejaba al run sin KB en
# silencio. Solo la búsqueda (read-only) se exime; los mutadores de la familia
# conocimiento (document_convert / promote_to_kb) siguen siendo asignaciones de
# catálogo.
# ---------------------------------------------------------------------------
def test_rag_search_is_a_system_capability() -> None:
    assert "rag_search" in SYSTEM_FAMILY_TOOL_NAMES
    assert "document_convert" not in SYSTEM_FAMILY_TOOL_NAMES
    assert "promote_to_kb" not in SYSTEM_FAMILY_TOOL_NAMES


def test_register_system_families_wires_rag_search_with_api() -> None:
    registry = ToolRegistry()
    registered = register_system_families(registry, api=_DummyApi(), sink=OrchestrationSink())
    assert "rag_search" in registered


def test_register_system_families_skips_rag_search_without_api() -> None:
    registry = ToolRegistry()
    registered = register_system_families(registry, api=None, sink=OrchestrationSink())
    assert "rag_search" not in registered


def test_rag_search_honours_conocimiento_flag() -> None:
    registry = ToolRegistry()
    registered = register_system_families(
        registry, api=_DummyApi(), sink=OrchestrationSink(), flags={"conocimiento": False}
    )
    assert "rag_search" not in registered
    assert "memory_recall" in registered
