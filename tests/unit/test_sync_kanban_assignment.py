"""Unit: plan-role → task implementer/reviewer assignment (Track 2).

The plan spec carries a ``role`` per task (planning_llm). Historically
``_build_task`` dropped it, so tasks materialised with
``assigned_agent_id=NULL`` and the dispatcher then picked by raw load
(landing implementation on the PM). These tests pin that ``_build_task``
resolves the spec ``role`` to the team's agent of that role and stamps
``assigned_agent_id`` (and ``reviewer_agent_id`` from the ``reviewer``
role), reusing the ``team_role_agents`` map the cost layer already builds.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.chat.planning_graph import PlanningRole
from api_server.chat.sync_to_kanban import _build_task
from api_server.db.domain import Plan

pytestmark = pytest.mark.unit


def _plan() -> Plan:
    return Plan(id=uuid4(), tenant_id=uuid4(), project_id=uuid4())


def test_build_task_resolves_role_to_assigned_agent() -> None:
    backend = uuid4()
    task = _build_task(
        _plan(),
        "t1",
        {"title": "Implement JWT", "role": "backend_dev"},
        role_agents={PlanningRole.BACKEND_DEV: backend},
    )
    assert task.assigned_agent_id == backend


def test_build_task_no_role_leaves_agent_unassigned() -> None:
    task = _build_task(
        _plan(),
        "t1",
        {"title": "Some task"},
        role_agents={PlanningRole.BACKEND_DEV: uuid4()},
    )
    assert task.assigned_agent_id is None


def test_build_task_unknown_role_leaves_agent_unassigned() -> None:
    task = _build_task(
        _plan(),
        "t1",
        {"title": "Some task", "role": "wizard"},
        role_agents={PlanningRole.BACKEND_DEV: uuid4()},
    )
    assert task.assigned_agent_id is None


def test_build_task_role_without_team_agent_unassigned() -> None:
    # The role is valid but the team has no agent of that role → fall back to
    # the dispatcher (NULL preset), never an arbitrary agent.
    task = _build_task(
        _plan(),
        "t1",
        {"title": "Implement JWT", "role": "backend_dev"},
        role_agents={},
    )
    assert task.assigned_agent_id is None


def test_build_task_sets_reviewer_from_reviewer_role() -> None:
    backend, reviewer = uuid4(), uuid4()
    task = _build_task(
        _plan(),
        "t1",
        {"title": "Implement JWT", "role": "backend_dev"},
        role_agents={PlanningRole.BACKEND_DEV: backend, PlanningRole.REVIEWER: reviewer},
    )
    assert task.assigned_agent_id == backend
    assert task.reviewer_agent_id == reviewer


def test_build_task_reviewer_never_equals_implementer() -> None:
    # A reviewer-role task implemented by the reviewer agent must NOT also be its
    # own reviewer (reviewer != implementer invariant).
    reviewer = uuid4()
    task = _build_task(
        _plan(),
        "t1",
        {"title": "Review pass", "role": "reviewer"},
        role_agents={PlanningRole.REVIEWER: reviewer},
    )
    assert task.assigned_agent_id == reviewer
    assert task.reviewer_agent_id is None


def test_build_task_without_role_agents_map_is_backward_compatible() -> None:
    # Legacy callers (no team) pass no map → no assignment, no crash.
    task = _build_task(_plan(), "t1", {"title": "Legacy", "role": "backend_dev"})
    assert task.assigned_agent_id is None
    assert task.reviewer_agent_id is None
