"""Integration tests: plan branch naming + create (Plan 06 task_06_21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


def test_branch_name_short_id_plus_slug() -> None:
    from workers.plan_git import make_plan_branch_name

    name = make_plan_branch_name("11111111-2222-3333-4444-555555555555", "fix authentication")
    assert name == "plan/11111111-fix-authentication"


def test_branch_name_normalises_slug() -> None:
    """Spaces, mixed case, accents → kebab-case alnum-only. PROY2-14: los
    acentos se TRANSLITERAN (búsqueda→busqueda), no se pierden letras."""
    from workers.plan_git import make_plan_branch_name

    name = make_plan_branch_name("abcdef0123", "Mejorar Búsqueda v2")
    assert name == "plan/abcdef01-mejorar-busqueda-v2"


def test_branch_name_handles_empty_slug() -> None:
    from workers.plan_git import make_plan_branch_name

    assert make_plan_branch_name("deadbeef1234", "") == "plan/deadbeef"


def test_branch_name_short_id_lowercased() -> None:
    from workers.plan_git import make_plan_branch_name

    assert make_plan_branch_name("AABBCCDD", "x") == "plan/aabbccdd-x"


def test_create_branch_in_bare(tmp_path: Path) -> None:
    """The orchestrator calls WorktreeManager.add when sync'ing the
    plan to the Kanban; the plan branch must exist on the bare after
    that. We exercise the integration via the worktree manager."""
    from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
    from workers.plan_git import make_plan_branch_name

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)

    branch = make_plan_branch_name("ffeeddcc-aabb-1122", "first plan")
    wt_mgr = WorktreeManager(layout, "backend")
    wt_mgr.add("task-1", branch=branch)

    # The branch now exists in the bare repo.
    import subprocess

    proc = subprocess.run(
        ["git", "rev-parse", f"refs/heads/{branch}"],
        cwd=str(bare),
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip()
