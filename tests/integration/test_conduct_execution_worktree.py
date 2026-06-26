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
from workers.execution import _provision_worktree

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
