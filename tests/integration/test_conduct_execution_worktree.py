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
async def test_provision_survives_concurrent_branch_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auditoría 2026-07-02: dos tasks hermanas promovidas A LA VEZ provisionan
    el MISMO plan branch; la perdedora del `git branch` moría con rc=128
    «a branch named ... already exists» (TOCTOU tras `branch_exists`) y, con el
    fail-fast F0.2, su run abortaba `workspace_unavailable`. La creación de la
    branch es ahora idempotente: "already exists" es éxito, no error."""
    from workers.git_repos import WorktreeManager

    settings = Settings(data_root=str(tmp_path))
    plan_id = str(uuid4())
    kwargs = {"tenant_slug": "acme", "project_slug": "api-ci", "plan_id": plan_id, "plan_slug": "p"}

    a = await _provision_worktree(settings, task_id="task-a", **kwargs)  # crea el branch
    assert a is not None
    # Simula al PERDEDOR de la carrera: su check dijo "no existe" justo antes
    # de que el ganador la creara.
    monkeypatch.setattr(WorktreeManager, "branch_exists", lambda self, branch: False)

    b = await _provision_worktree(settings, task_id="task-b", **kwargs)

    assert b is not None
    assert Path(b).is_dir()


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
        settings,
        host_path=wt,
        task_id="task-1",
        execution_id="exec-1",
        project_id=str(uuid4()),
        **kwargs,
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
        settings,
        host_path=wt,
        task_id="task-1",
        execution_id="exec-1",
        project_id=str(uuid4()),
        **kwargs,
    )

    bare = str(tmp_path / "projects" / "acme" / "api-ci" / "repos" / "api-ci.git")
    branch = make_plan_branch_name(plan_id, "p")
    # The branch exists (created at provision time, pointing at the empty seed
    # commit), but NO agent commit was added — its tree is empty.
    tree = _run_git("-C", bare, "ls-tree", "--name-only", branch)
    assert tree.strip() == ""


@pytest.mark.asyncio
async def test_run_task_tests_threads_worktree_and_filters_criteria(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prod-18 test_01: the test-runtime is invoked with the worktree path and ONLY
    the automated acceptance criteria (manual/human checks dropped).

    task_wf_22: el seam observado cambió de `_run_test_runtime` (en proceso) a
    `dispatch_test_runtime_and_wait` (cola `test`). Lo que este test fija —qué
    request se construye— es exactamente igual de válido; lo que cambió es DÓNDE
    se ejecuta ese request."""
    from workers.execution import _run_task_tests

    captured: dict = {}

    async def _fake_dispatch(request: dict) -> dict:
        captured["request"] = request
        return {"status": "completed"}

    monkeypatch.setattr(
        "workers.tasks.test_runtime_task.dispatch_test_runtime_and_wait", _fake_dispatch
    )
    tenant, task = uuid4(), uuid4()
    criteria = [
        {"id": "a", "runtime": "python-pytest", "command": "pytest -q"},
        {"id": "manual", "kind": "human"},  # no runtime/command → dropped
    ]
    await _run_task_tests(
        Settings(data_root=str(tmp_path)),
        tenant_id=tenant,
        task_id=task,
        worktree_host_path="/data/agent-platform/wt/task-1",
        acceptance_criteria=criteria,
    )
    req = captured["request"]
    assert req["worktree_host_path"] == "/data/agent-platform/wt/task-1"
    assert req["task_id"] == str(task)
    assert [c["id"] for c in req["acceptance_criteria"]] == ["a"]


@pytest.mark.asyncio
async def test_run_task_tests_noop_without_automated_criteria(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from workers.execution import _run_task_tests

    called = False

    async def _fake_dispatch(request: dict) -> dict:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(
        "workers.tasks.test_runtime_task.dispatch_test_runtime_and_wait", _fake_dispatch
    )
    await _run_task_tests(
        Settings(data_root=str(tmp_path)),
        tenant_id=uuid4(),
        task_id=uuid4(),
        worktree_host_path="/x",
        acceptance_criteria=[{"id": "m", "kind": "human"}],  # no automated checks
    )
    assert called is False
