"""Integration tests: plan → completed (Plan 06 task_06_37)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_approved_and_pr_merged_completes_plan() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed(
        "pending_human_validation",
        human_verdict="approved",
        pr_merged=True,
    )
    assert result.transitioned is True
    assert result.new_status == "completed"


def test_not_yet_approved_blocks_completion() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed(
        "pending_human_validation",
        human_verdict=None,
        pr_merged=True,
    )
    assert result.transitioned is False
    assert "verdict" in (result.reason or "")


def test_rejected_blocks_completion() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed(
        "pending_human_validation",
        human_verdict="rejected",
        pr_merged=True,
    )
    assert result.transitioned is False


def test_pr_not_merged_blocks_completion() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed(
        "pending_human_validation",
        human_verdict="approved",
        pr_merged=False,
    )
    assert result.transitioned is False
    assert "PR not merged" in (result.reason or "")


def test_wrong_starting_state_blocks() -> None:
    from api_server.plan_progress import transition_to_completed

    result = transition_to_completed(
        "in_progress",
        human_verdict="approved",
        pr_merged=True,
    )
    assert result.transitioned is False
