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
    assert set(registered) == {"memory_recall", "memory_store"}
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
