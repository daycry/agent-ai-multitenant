"""Integration tests: review-runtime verdict timeout (Plan 06 task_06_33).

48 h without verdict → session expires, containers destroyed, plan
gets flipped to `blocked` by the orchestrator (orchestrator side
tested elsewhere).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _spec() -> object:
    from workers.review_runtime import ReviewRuntimeSpec

    return ReviewRuntimeSpec(
        plan_id="plan-1",
        project_id="proj-1",
        tenant_id="t",
        repo_name="backend",
        worktree_host_path="/data/wt/x",
        main_image="backend:latest",
    )


def test_expire_overdue_destroys_containers() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    destroyed: list[tuple[str, ...]] = []
    mgr = ReviewRuntimeManager(
        spawn=lambda _s: ("c1",),
        destroy=destroyed.append,
        verdict_timeout_s=1,
    )
    session = mgr.create(_spec())
    session.expires_at = 0.0

    expired = mgr.expire_overdue()
    assert expired == [session.id]
    assert session.status == "expired"
    assert destroyed == [("c1",)]


def test_expire_overdue_ignores_running_session_within_budget() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",))
    session = mgr.create(_spec())
    assert mgr.expire_overdue() == []
    assert session.status == "running"


def test_expire_overdue_now_override() -> None:
    """The orchestrator can pass a fake `now` so the sweep is
    deterministic in tests."""
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(
        spawn=lambda _s: ("c",),
        verdict_timeout_s=60 * 60,
    )
    session = mgr.create(_spec())
    expired = mgr.expire_overdue(now=session.expires_at + 1)
    assert session.id in expired
    assert session.status == "expired"


def test_already_terminal_sessions_skipped() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",))
    s = mgr.create(_spec())
    mgr.approve(s.id)
    s.expires_at = 0.0
    assert mgr.expire_overdue() == []
    # Status stays approved (NOT flipped to expired).
    assert s.status == "approved"
