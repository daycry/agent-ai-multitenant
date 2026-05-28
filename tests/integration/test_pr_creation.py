"""Integration tests: PR auto-creation on plan close (Plan 06 task_06_24).

Multiple repos per plan → multiple PRs. We pin: one workflow per repo,
each opens its own PR via the injected opener.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _bare_with_origin(tmp_path: Path, repo_name: str) -> Path:
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    remote_bare = tmp_path / "remote" / f"{repo_name}.git"
    seed_bare_repo(remote_bare)
    layout = BareRepoLayout(data_root=tmp_path / "local", tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo(repo_name, remote_url=str(remote_bare))
    _run_git("fetch", "origin", cwd=bare)
    _run_git(
        "update-ref",
        "refs/heads/plan/m1-feat",
        "refs/remotes/origin/main",
        cwd=bare,
    )
    return bare


def test_multiple_repos_open_their_own_pr(tmp_path: Path) -> None:
    """Plan touches two repos → two PRs, one each."""
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    backend = _bare_with_origin(tmp_path, "backend")
    frontend = _bare_with_origin(tmp_path, "frontend")

    pr_log: dict[str, str] = {}

    def opener_for(repo_name: str) -> object:
        def _open(title: str, body: str) -> str:  # - body unused
            url = f"https://github.example/owner/{repo_name}/pull/1"
            pr_log[repo_name] = url
            return url

        return _open

    backend_wf = PlanGitWorkflow(
        bare_repo_path=backend,
        plan_branch="plan/m1-feat",
        policies=PlanGitPolicies(),
        pr_opener=opener_for("backend"),
    )
    frontend_wf = PlanGitWorkflow(
        bare_repo_path=frontend,
        plan_branch="plan/m1-feat",
        policies=PlanGitPolicies(),
        pr_opener=opener_for("frontend"),
    )

    backend_info = backend_wf.open_plan_pr(title="Plan M1", body="…")
    frontend_info = frontend_wf.open_plan_pr(title="Plan M1", body="…")

    assert backend_info.url == "https://github.example/owner/backend/pull/1"
    assert frontend_info.url == "https://github.example/owner/frontend/pull/1"
    assert pr_log == {
        "backend": "https://github.example/owner/backend/pull/1",
        "frontend": "https://github.example/owner/frontend/pull/1",
    }


def test_pr_skipped_when_push_forbidden(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    bare = _bare_with_origin(tmp_path, "backend")
    called = False

    def fake_opener(_title: str, _body: str) -> str:
        nonlocal called
        called = True
        return "should-not-happen"

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/x",
        policies=PlanGitPolicies(push_policy="forbidden"),
        pr_opener=fake_opener,
    )
    info = wf.open_plan_pr(title="X", body="Y")
    assert info.skipped_reason == "push_policy=forbidden"
    assert info.url is None
    assert called is False
