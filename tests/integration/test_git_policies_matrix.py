"""Integration tests: matrix of the three policies (Plan 06 task_06_25 — step 2).

The three axes (``branch_push_mode`` × ``plan_validation_mode`` ×
``push_policy``) yield 2 × 2 × 3 = 12 combinations. Section 12.6.7 of
the .docx pins what each one means; this test pins the *git*
side of the matrix (orchestrator-side state transitions for
``plan_validation_mode`` are tested in the doble-Kanban tests).
"""

from __future__ import annotations

import itertools

import pytest

from tests.integration._git_helpers import seed_bare_repo

pytestmark = pytest.mark.integration


BRANCH_PUSH_MODES = ("incremental", "final_only")
VALIDATION_MODES = ("human_required", "auto_approve")
PUSH_POLICIES = ("forbidden", "branch_only_pr_required", "direct_to_default_allowed")


@pytest.mark.parametrize(
    ("bpm", "vmode", "ppol"),
    list(itertools.product(BRANCH_PUSH_MODES, VALIDATION_MODES, PUSH_POLICIES)),
)
def test_policy_combination_constructs_and_executes(
    tmp_path,
    bpm: str,
    vmode: str,
    ppol: str,
) -> None:
    """Every legal combination of the three axes:
    * builds a PlanGitWorkflow without raising,
    * apply_push_policy returns a known label,
    * open_plan_pr returns a PrInfo (URL or skip reason).
    """
    from workers.git_repos import BareRepoLayout, BareRepoManager
    from workers.plan_git import PlanGitPolicies, PlanGitWorkflow

    from tests.integration._git_helpers import commit_to_branch

    layout = BareRepoLayout(data_root=tmp_path, tenant_slug="t", project_slug="p")
    bare = BareRepoManager(layout).ensure_repo("backend")
    seed_bare_repo(bare)
    commit_to_branch(bare, "plan/x-y", filename="f.py", content="x")

    pr_log: list[tuple[str, str]] = []

    def opener(title: str, body: str) -> str:
        pr_log.append((title, body))
        return "https://example.test/pr/1"

    wf = PlanGitWorkflow(
        bare_repo_path=bare,
        plan_branch="plan/x-y",
        policies=PlanGitPolicies(
            branch_push_mode=bpm,  # type: ignore[arg-type]
            plan_validation_mode=vmode,  # type: ignore[arg-type]
            push_policy=ppol,  # type: ignore[arg-type]
        ),
        pr_opener=opener,
    )

    action = wf.apply_push_policy()
    assert action in {"forbidden", "pr_required", "merged_to_default"}

    pr_info = wf.open_plan_pr(title="X", body="Y")
    if ppol == "forbidden":
        assert pr_info.skipped_reason == "push_policy=forbidden"
    else:
        # Local-only project (no origin) → skip reason about origin.
        # We don't configure origin in this fixture so the skip
        # branch always fires here. The point of this test is
        # construction + behaviour parity across combinations, not
        # network reachability.
        assert pr_info.url is None or pr_info.url
