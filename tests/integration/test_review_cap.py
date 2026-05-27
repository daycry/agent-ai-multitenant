"""Integration tests: tenant cap on review-runtimes (Plan 06 task_06_34)."""

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


def test_tenant_cap_blocks_new_session() -> None:
    from workers.review_runtime import ReviewRuntimeManager, TenantCapExceeded

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), tenant_cap=2)
    mgr.create(_spec(tenant_id="t1"))
    mgr.create(_spec(tenant_id="t1"))
    with pytest.raises(TenantCapExceeded, match="cap 2"):
        mgr.create(_spec(tenant_id="t1"))


def test_tenant_cap_is_per_tenant() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), tenant_cap=1)
    mgr.create(_spec(tenant_id="t1"))
    # t2 has its own budget.
    mgr.create(_spec(tenant_id="t2"))
    assert len(mgr.list_for_tenant("t1")) == 1
    assert len(mgr.list_for_tenant("t2")) == 1


def test_terminal_session_doesnt_count_toward_cap() -> None:
    """approved / rejected sessions free their cap slot."""
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), tenant_cap=1)
    s = mgr.create(_spec(tenant_id="t1"))
    mgr.approve(s.id)
    # New session for the same tenant succeeds.
    mgr.create(_spec(tenant_id="t1"))


def test_expired_session_also_doesnt_count() -> None:
    from workers.review_runtime import ReviewRuntimeManager

    mgr = ReviewRuntimeManager(spawn=lambda _s: ("c",), tenant_cap=1)
    s = mgr.create(_spec(tenant_id="t1"))
    s.expires_at = 0.0
    mgr.expire_overdue()
    # The just-expired session freed the cap slot.
    mgr.create(_spec(tenant_id="t1"))


def test_default_cap_is_5() -> None:
    from workers.review_runtime import DEFAULT_TENANT_CAP

    assert DEFAULT_TENANT_CAP == 5
