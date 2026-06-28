"""Unit test: the expired-review → plan-status decision (C8 F40).

The expiry sweep now transitions the owning plan + escalates to the owner. The DB
sweep + notification are integration-tested; here we pin the pure, IDEMPOTENT
decision: only a plan still awaiting human validation moves to ``blocked``.
"""

from __future__ import annotations

import pytest
from workers.maintenance import plan_status_after_expiry

pytestmark = pytest.mark.unit


def test_pending_human_validation_blocks() -> None:
    assert plan_status_after_expiry("pending_human_validation") == "blocked"


def test_other_statuses_untouched_idempotent() -> None:
    # Re-running the sweep must never re-transition an already-settled plan.
    for status in (
        "blocked",
        "completed",
        "rejected",
        "in_progress",
        "cancelled",
        "archived",
        "approved",
    ):
        assert plan_status_after_expiry(status) is None
