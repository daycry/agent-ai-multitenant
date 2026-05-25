"""Unit tests for the Memorizer gating policy (Plan 04 task_04_03)."""

from __future__ import annotations

import pytest
from api_server.memorizer.policy import MemorizeDecision, should_memorize

pytestmark = pytest.mark.unit


def test_done_with_canonical_scope_returns_memorise_true() -> None:
    for scope in ("private", "team_shared", "project_shared", "global"):
        decision = should_memorize(status="done", memory_scope=scope)
        assert decision.memorise is True, f"scope={scope}"
        assert decision.reason == "ok"


@pytest.mark.parametrize("status", ["aborted", "failed", "running", "awaiting_human_approval"])
def test_non_done_status_returns_memorise_false(status: str) -> None:
    decision = should_memorize(status=status, memory_scope="private")
    assert decision.memorise is False
    assert status in decision.reason


def test_null_memory_scope_returns_memorise_false() -> None:
    decision = should_memorize(status="done", memory_scope=None)
    assert decision.memorise is False
    assert "NULL" in decision.reason


@pytest.mark.parametrize("scope", ["none", "off", "random_string", ""])
def test_non_canonical_scope_returns_memorise_false(scope: str) -> None:
    decision = should_memorize(status="done", memory_scope=scope)
    assert decision.memorise is False
    assert "canonical" in decision.reason


def test_decision_is_frozen_dataclass() -> None:
    """Catches accidental mutation downstream."""
    decision = should_memorize(status="done", memory_scope="private")
    with pytest.raises((AttributeError, Exception)):
        decision.memorise = False  # type: ignore[misc]


def test_decision_equality_is_value_based() -> None:
    a = MemorizeDecision(memorise=True, reason="ok")
    b = MemorizeDecision(memorise=True, reason="ok")
    assert a == b
