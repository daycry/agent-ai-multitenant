"""Integration tests: plan_validation_mode='auto_approve' (Plan 06 task_06_23 — step 4).

In auto_approve mode the plan closes (and the PR opens, when policy
allows) without a human reviewer marking the validation checkboxes.
We don't simulate the orchestrator end-to-end here — we pin the
contract on the PolicyMode + open_plan_pr branches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _bare_with_origin(tmp_path: Path) -> Path:
    """Local bare with an origin remote so the PR step can run."""
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    remote_bare = tmp_path / "remote" / "backend.git"
    seed_bare_repo(remote_bare)
    layout = BareRepoLayout(data_root=tmp_path / "local", tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend", remote_url=str(remote_bare))
    _run_git("fetch", "origin", cwd=bare)
    _run_git("update-ref", "refs/heads/plan/x-y", "refs/remotes/origin/main", cwd=bare)
    return bare


def test_auto_approve_opens_pr_without_human_step(tmp_path: Path) -> None:
    """The orchestrator calls open_plan_pr unconditionally; the
    plan_validation_mode policy is consulted upstream in the
    orchestrator (whether to skip the human-validation state). At
    the git layer, auto_approve means "open the PR now"."""
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_origin(tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_pr_opener(title: str, body: str) -> str:
        calls.append((title, body))
        return "https://github.example/owner/repo/pull/42"

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/x-y",
        policies=PlanGitPolicies(
            branch_push_mode="incremental",
            plan_validation_mode="auto_approve",
            push_policy="branch_only_pr_required",
        ),
        pr_opener=fake_pr_opener,
    )
    info = wf.open_plan_pr(title="Plan X", body="Auto-approved")
    assert info.url == "https://github.example/owner/repo/pull/42"
    assert info.skipped_reason is None
    assert calls == [("Plan X", "Auto-approved")]


def test_human_required_still_opens_pr_at_close(tmp_path: Path) -> None:
    """``human_required`` doesn't *skip* the PR — it just means the
    orchestrator waited for the human's pass mark before getting
    here. open_plan_pr behaves identically once invoked."""
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_origin(tmp_path)
    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/x-y",
        policies=PlanGitPolicies(
            plan_validation_mode="human_required",
            push_policy="branch_only_pr_required",
        ),
        pr_opener=lambda _t, _b: "https://prs.example/1",
    )
    info = wf.open_plan_pr(title="X", body="Y")
    assert info.url == "https://prs.example/1"


def test_open_pr_skipped_when_no_opener(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_origin(tmp_path)
    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/x-y",
        policies=PlanGitPolicies(),
        pr_opener=None,
    )
    info = wf.open_plan_pr(title="X", body="Y")
    assert info.url is None
    assert info.skipped_reason == "no pr_opener wired"


def test_open_pr_skipped_when_no_origin(tmp_path: Path) -> None:
    """Local-only project: no remote → no PR."""
    from workers.git_repos import BareRepoLayout, BareRepoManager
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/x",
        policies=PlanGitPolicies(),
        pr_opener=lambda _t, _b: "https://nope",
    )
    info = wf.open_plan_pr(title="X", body="Y")
    assert info.url is None
    assert "no remote" in (info.skipped_reason or "")
