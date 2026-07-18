"""Integration tests for the orchestration builtin tools (task_02_18).

Contrato AUD16-02 (2026-07-16): el drain worker-side previsto nunca aterrizó,
así que ``kanban_update``/``notify_user``/``agent_invoke`` devuelven un error
HONESTO (``ok=False``, "not wired") en vez de fingir un efecto que moriría en
el contenedor. Solo ``task_comment`` tiene consumidor real (el worker drena su
efecto a PlanComment) y sigue emitiendo al sink.
"""

from __future__ import annotations

import pytest
from agent_runtime.orchestration_tools import (
    OrchestrationSink,
    OrchestrationTools,
    register_orchestration_tools,
)
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration


def _tools() -> tuple[OrchestrationTools, OrchestrationSink]:
    sink = OrchestrationSink()
    return OrchestrationTools(sink), sink


# ---------------------------------------------------------------------------
# kanban_update
# ---------------------------------------------------------------------------
def test_kanban_update_is_honestly_not_wired() -> None:
    """AUD16-02: sin drain, un ok=True sería éxito falso — error honesto y
    NADA en el sink (la validación de args sigue pasando antes)."""
    tools, sink = _tools()
    result = tools.kanban_update({"task_id": "task-1", "status": "in_progress"})
    assert result.ok is False
    assert "not wired" in (result.error or "")
    assert sink.effects == []


def test_kanban_update_rejects_an_invalid_status() -> None:
    tools, sink = _tools()
    result = tools.kanban_update({"task_id": "task-1", "status": "shipped"})
    assert result.ok is False
    assert "invalid kanban status" in (result.error or "")
    assert sink.effects == []  # nothing emitted on a bad request


def test_kanban_update_requires_a_task_id() -> None:
    tools, _ = _tools()
    assert tools.kanban_update({"status": "done"}).ok is False


# ---------------------------------------------------------------------------
# task_comment
# ---------------------------------------------------------------------------
def test_task_comment_emits_an_effect() -> None:
    tools, sink = _tools()
    result = tools.task_comment({"task_id": "task-1", "body": "looks good"})
    assert result.ok is True
    assert sink.effects[0] == {
        "effect": "task_comment",
        "task_id": "task-1",
        "body": "looks good",
    }


def test_task_comment_rejects_an_empty_body() -> None:
    tools, sink = _tools()
    assert tools.task_comment({"task_id": "task-1", "body": "  "}).ok is False
    assert sink.effects == []


# ---------------------------------------------------------------------------
# notify_user
# ---------------------------------------------------------------------------
def test_notify_user_is_honestly_not_wired() -> None:
    tools, sink = _tools()
    result = tools.notify_user({"user_id": "user-9", "message": "review needed"})
    assert result.ok is False
    assert "not wired" in (result.error or "")
    assert sink.effects == []


def test_notify_user_requires_a_message() -> None:
    tools, _ = _tools()
    assert tools.notify_user({"user_id": "user-9"}).ok is False


# ---------------------------------------------------------------------------
# agent_invoke
# ---------------------------------------------------------------------------
def test_agent_invoke_is_honestly_not_wired() -> None:
    tools, sink = _tools()
    result = tools.agent_invoke({"agent_id": "agent-7", "prompt": "summarise the PR"})
    assert result.ok is False
    assert "not wired" in (result.error or "")
    assert sink.effects == []


def test_agent_invoke_requires_a_prompt() -> None:
    tools, _ = _tools()
    assert tools.agent_invoke({"agent_id": "agent-7"}).ok is False


# ---------------------------------------------------------------------------
# Sink + registry wiring
# ---------------------------------------------------------------------------
def test_only_wired_effects_accumulate() -> None:
    """Solo task_comment (con consumidor real) emite; las not-wired no
    ensucian el sink aunque se llamen entre medias."""
    tools, sink = _tools()
    tools.kanban_update({"task_id": "t", "status": "in_progress"})
    tools.task_comment({"task_id": "t", "body": "done a bit"})
    tools.kanban_update({"task_id": "t", "status": "in_review"})
    assert [e["effect"] for e in sink.effects] == ["task_comment"]


def test_register_orchestration_tools_wires_all_four() -> None:
    registry = ToolRegistry()
    sink = OrchestrationSink()
    register_orchestration_tools(registry, sink)
    assert set(registry.names()) == {
        "kanban_update",
        "task_comment",
        "notify_user",
        "agent_invoke",
    }
    # Callable through the registry; the not-wired contract also applies here
    # (AUD16-02) — task_comment is the one that lands in the sink.
    result = registry.call("kanban_update", {"task_id": "t", "status": "done"})
    assert result.ok is False
    assert "not wired" in (result.error or "")
    comment = registry.call("task_comment", {"task_id": "t", "body": "hola"})
    assert comment.ok is True
    assert sink.effects[0]["effect"] == "task_comment"
