"""cadena-pr T3 (P3): the plan branch is pushed bare→remote per branch_push_mode.

`incremental` (the default) mirrors every accepted task's commit to the remote so
the auto-PR at close targets a branch the remote already has; `final_only` defers.
Exercises `_push_branch_to_remote_gated` against a real `file://` remote (the push
mechanics of `push_plan_branch_to_remote`, split out so no project row is needed).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def _setup_local_bare_with_plan_branch(tmp_path: Path, plan_branch: str) -> tuple[object, Path]:
    """A single-source local bare (origin → a seeded remote) carrying `plan_branch`."""
    from workers.config import Settings
    from workers.git_repos import BareRepoLayout, BareRepoManager, _run_git

    remote_bare = tmp_path / "remote" / "p.git"
    seed_bare_repo(remote_bare)

    settings = Settings(data_root=str(tmp_path / "local"))
    layout = BareRepoLayout(data_root=Path(settings.data_root), tenant_slug="t", project_slug="p")
    local_bare = BareRepoManager(layout).ensure_repo("p", remote_url=str(remote_bare))
    _run_git("fetch", "origin", cwd=local_bare)
    _run_git("update-ref", f"refs/heads/{plan_branch}", "refs/remotes/origin/main", cwd=local_bare)
    return settings, remote_bare


def _remote_has_branch(remote_bare: Path, branch: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", f"refs/heads/{branch}"],
        cwd=str(remote_bare),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0 and bool(proc.stdout.strip())


def test_incremental_push_reaches_remote(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, plan_git_identity
    from workers.plan_pr import _push_branch_to_remote_gated

    plan_id = str(uuid4())
    identity = plan_git_identity(plan_id, "feat", "p")
    settings, remote_bare = _setup_local_bare_with_plan_branch(tmp_path, identity.plan_branch)

    status = asyncio.run(
        _push_branch_to_remote_gated(
            settings,
            tenant_slug="t",
            project_slug="p",
            plan_id=plan_id,
            plan_slug="feat",
            remote_url=str(remote_bare),
            provider="generic",
            auth_mode="none",
            project_id=uuid4(),
            policies=PlanGitPolicies(branch_push_mode="incremental"),
        )
    )
    assert status == "pushed"
    assert _remote_has_branch(remote_bare, identity.plan_branch)


def test_push_forbidden_never_reaches_the_remote(tmp_path: Path) -> None:
    """`push_policy='forbidden'` = «este proyecto nunca empuja»: el push por-tarea
    de T3 lo ignoraba y espejaba la rama al remoto en cada tarea aceptada."""
    from workers.plan_git import PlanGitPolicies, plan_git_identity
    from workers.plan_pr import _push_branch_to_remote_gated

    plan_id = str(uuid4())
    identity = plan_git_identity(plan_id, "feat", "p")
    settings, remote_bare = _setup_local_bare_with_plan_branch(tmp_path, identity.plan_branch)

    status = asyncio.run(
        _push_branch_to_remote_gated(
            settings,
            tenant_slug="t",
            project_slug="p",
            plan_id=plan_id,
            plan_slug="feat",
            remote_url=str(remote_bare),
            provider="generic",
            auth_mode="none",
            project_id=uuid4(),
            policies=PlanGitPolicies(branch_push_mode="incremental", push_policy="forbidden"),
        )
    )
    assert status == "skipped:push_forbidden"
    assert not _remote_has_branch(remote_bare, identity.plan_branch)


def test_final_only_defers_the_push(tmp_path: Path) -> None:
    from workers.plan_git import PlanGitPolicies, plan_git_identity
    from workers.plan_pr import _push_branch_to_remote_gated

    plan_id = str(uuid4())
    identity = plan_git_identity(plan_id, "feat", "p")
    settings, remote_bare = _setup_local_bare_with_plan_branch(tmp_path, identity.plan_branch)

    status = asyncio.run(
        _push_branch_to_remote_gated(
            settings,
            tenant_slug="t",
            project_slug="p",
            plan_id=plan_id,
            plan_slug="feat",
            remote_url=str(remote_bare),
            provider="generic",
            auth_mode="none",
            project_id=uuid4(),
            policies=PlanGitPolicies(branch_push_mode="final_only"),
        )
    )
    assert status == "skipped:final_only"
    assert not _remote_has_branch(remote_bare, identity.plan_branch)
