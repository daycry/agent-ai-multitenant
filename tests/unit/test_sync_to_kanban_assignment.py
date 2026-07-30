"""c7: `_resolve_assignment` must leave a trace when a role loses its preset.

An unknown role (or a valid role with no agent on the team) still resolves to a
NULL slot — that is the accepted ADR 0091 D1 fallback (the dispatcher decides).
The audit (2026-07-03, c7) found it happened SILENTLY, so a typo'd role would
lose its intended assignment with no signal. These tests pin the warning (via a
monkeypatched logger — caplog is order-fragile here, so we don't use it).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.chat import sync_to_kanban as sk
from api_server.chat.planning_graph import PlanningRole


class _FakeLog:
    def __init__(self) -> None:
        self.events: list[str] = []

    def warning(self, event: str, **_: object) -> None:
        self.events.append(event)


def test_unknown_role_warns_and_nulls_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLog()
    monkeypatch.setattr(sk, "_log", fake)
    role_agents = {PlanningRole.REVIEWER: uuid4()}

    assigned, _reviewer = sk._resolve_assignment({"id": "t1", "role": "wizard"}, role_agents)

    assert assigned is None  # ADR 0091 D1 fallback preserved
    assert "sync_to_kanban.role_unknown" in fake.events


def test_role_without_team_agent_warns_and_nulls_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLog()
    monkeypatch.setattr(sk, "_log", fake)
    # A valid role, but no agent for it in the team → still NULL, now traced.
    role_agents = {PlanningRole.REVIEWER: uuid4()}

    assigned, _reviewer = sk._resolve_assignment(
        {"id": "t1", "role": PlanningRole.BACKEND_DEV.value}, role_agents
    )

    assert assigned is None
    assert "sync_to_kanban.role_without_team_agent" in fake.events


def test_resolved_role_does_not_warn(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeLog()
    monkeypatch.setattr(sk, "_log", fake)
    agent_id = uuid4()
    role_agents = {PlanningRole.BACKEND_DEV: agent_id, PlanningRole.REVIEWER: uuid4()}

    assigned, _reviewer = sk._resolve_assignment(
        {"id": "t1", "role": PlanningRole.BACKEND_DEV.value}, role_agents
    )

    assert assigned == agent_id
    assert fake.events == []
