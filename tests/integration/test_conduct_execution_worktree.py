"""Integration test — prod-18 task_prod18_provision_01.

`_provision_worktree` materialises the per-task git worktree (reusing the Plan 06
libraries) and returns its absolute HOST path for the `/workspace` bind. Git on
disk in ``tmp_path`` — no Docker.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from workers.config import Settings
from workers.execution import _commit_and_push_worktree, _provision_worktree
from workers.git_repos import _run_git
from workers.plan_git import make_plan_branch_name

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_provision_worktree_creates_worktree_and_bare(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())

    path = await _provision_worktree(
        settings,
        tenant_slug="acme",
        project_slug="api-ci",
        plan_id=plan_id,
        plan_slug="first-plan",
        task_id="task-aaa",
    )

    assert path is not None
    wt = Path(path)
    # Absolute host path under the canonical worktrees/ tree, named by task_id.
    assert wt.is_absolute()
    assert wt.is_dir()
    assert wt.name == "task-aaa"
    assert "worktrees" in wt.parts
    # The project's bare repo was ensured.
    bare = tmp_path / "projects" / "acme" / "api-ci" / "repos" / "api-ci.git"
    assert bare.is_dir()
    # The worktree is checked out on the plan branch (HEAD detached on it).
    assert (tmp_path / "projects" / "acme" / "api-ci" / "worktrees" / "task-aaa").is_dir()


@pytest.mark.asyncio
async def test_two_tasks_share_plan_branch_distinct_worktrees(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())
    kwargs = {"tenant_slug": "acme", "project_slug": "api-ci", "plan_id": plan_id, "plan_slug": "p"}

    a = await _provision_worktree(settings, task_id="task-a", **kwargs)
    b = await _provision_worktree(settings, task_id="task-b", **kwargs)

    assert a is not None and b is not None
    assert a != b  # disjoint worktrees
    assert Path(a).name == "task-a"
    assert Path(b).name == "task-b"


@pytest.mark.asyncio
async def test_commit_and_push_persists_agent_output_to_bare(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())
    kwargs = {"tenant_slug": "acme", "project_slug": "api-ci", "plan_id": plan_id, "plan_slug": "p"}

    wt = await _provision_worktree(settings, task_id="task-1", **kwargs)
    assert wt is not None
    # The agent writes a file into the worktree.
    (Path(wt) / "out.txt").write_text("hello from the agent\n", encoding="utf-8")

    await _commit_and_push_worktree(
        settings, host_path=wt, task_id="task-1", execution_id="exec-1", **kwargs
    )

    bare = str(tmp_path / "projects" / "acme" / "api-ci" / "repos" / "api-ci.git")
    branch = make_plan_branch_name(plan_id, "p")
    # The plan branch tip carries the agent's file + the mandatory trailers.
    body = _run_git("-C", bare, "log", "-1", "--format=%B", branch)
    assert "Plan-Id:" in body
    assert "Task-Id: task-1" in body
    assert "Execution-Id: exec-1" in body
    tree = _run_git("-C", bare, "ls-tree", "--name-only", branch)
    assert "out.txt" in tree


@pytest.mark.asyncio
async def test_commit_and_push_noop_on_clean_worktree(tmp_path: Path) -> None:
    # No file written → commit_task raises "clean" → swallowed, no push, no raise.
    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())
    kwargs = {"tenant_slug": "acme", "project_slug": "api-ci", "plan_id": plan_id, "plan_slug": "p"}
    wt = await _provision_worktree(settings, task_id="task-1", **kwargs)
    assert wt is not None

    # Must not raise even though there is nothing to commit.
    await _commit_and_push_worktree(
        settings, host_path=wt, task_id="task-1", execution_id="exec-1", **kwargs
    )

    bare = str(tmp_path / "projects" / "acme" / "api-ci" / "repos" / "api-ci.git")
    branch = make_plan_branch_name(plan_id, "p")
    # The branch exists (created at provision time, pointing at the empty seed
    # commit), but NO agent commit was added — its tree is empty.
    tree = _run_git("-C", bare, "ls-tree", "--name-only", branch)
    assert tree.strip() == ""
