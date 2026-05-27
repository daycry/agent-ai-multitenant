"""Integration tests: review-runtime idle suspension (Plan 06 task_06_32)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _spec(tenant_id: str = "t") -> object:
    from workers.review_runtime import ReviewRuntimeSpec

    return ReviewRuntimeSpec(
        plan_id="plan-1",
        project_id="proj-1",
        tenant_id=tenant_id,
        repo_name="backend",
        worktree_host_path="/data/wt/x",
        main_image="backend:latest",
    )


def test_suspend_idle_pauses_stale_sessions() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    paused: list[tuple[str, ...]] = []
    mgr = ReviewRuntimeManager(
        spawn=lambda _s: ("c1", "c2"),
        pause=paused.append,
        idle_suspend_s=10,
    )
    session = mgr.create(_spec())
    session.last_activity_at = session.last_activity_at - 3600

    suspended = mgr.suspend_idle()
    assert suspended == [session.id]
    assert session.status == "suspended"
    assert paused == [("c1", "c2")]


def test_touch_resumes_suspended_session() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), idle_suspend_s=10)
    session = mgr.create(_spec())
    session.last_activity_at = session.last_activity_at - 3600
    mgr.suspend_idle()
    assert session.status == "suspended"

    mgr.touch(session.id)
    assert session.status == "running"


def test_running_session_within_idle_budget_stays_running() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), idle_suspend_s=3600)
    session = mgr.create(_spec())
    suspended = mgr.suspend_idle()
    assert suspended == []
    assert session.status == "running"


def test_terminal_session_not_suspended() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), idle_suspend_s=10)
    session = mgr.create(_spec())
    mgr.approve(session.id)
    session.last_activity_at = session.last_activity_at - 3600
    assert mgr.suspend_idle() == []
