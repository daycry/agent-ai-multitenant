"""Unit tests for the single-source plan git identity (audit 2026-07-03, P1/P2).

`plan_git_identity` is the one derivation of a plan's bare repo + branch, shared
by execution, clone and the auto-PR. The bug it fixes: the auto-PR re-slugified
the (``"Plan: "``-prefixed) title while execution used the persisted
``plan.slug`` — so the PR branch could never match the branch holding the commits.
"""

from __future__ import annotations

from workers.plan_git import PlanGitIdentity, make_plan_branch_name, plan_git_identity
from workers.repo_clone import _repo_name_from_url, _slugify

PLAN_ID = "019f1397-afaf-7c31-9b2e-1a2b3c4d5e6f"
PROJECT_SLUG = "api-ci"  # projects.slug, persisted at creation
PLAN_SLUG = "api-codeigniter-4-endpoints-pblicos-v1-y-comando-de-diagnsti"  # plans.slug


def test_identity_uses_persisted_slugs() -> None:
    ident = plan_git_identity(PLAN_ID, PLAN_SLUG, PROJECT_SLUG)
    assert isinstance(ident, PlanGitIdentity)
    # The bare repo is one-per-project, named by project.slug (ADR 0085 dec.2).
    assert ident.project_slug == PROJECT_SLUG
    # The branch is derived from the PERSISTED plan.slug, never from the title.
    assert ident.plan_branch == make_plan_branch_name(PLAN_ID, PLAN_SLUG)


def test_identity_matches_execution_derivation() -> None:
    """Execution branches at make_plan_branch_name(plan_id, plan.slug); the
    auto-PR must resolve the IDENTICAL branch via plan_git_identity."""
    execution_branch = make_plan_branch_name(PLAN_ID, PLAN_SLUG)
    ident = plan_git_identity(PLAN_ID, PLAN_SLUG, PROJECT_SLUG)
    assert ident.plan_branch == execution_branch


def test_identity_diverges_from_old_buggy_autopr_derivation() -> None:
    """Regression: the old auto-PR derived the branch from _slugify("Plan: "+title)
    and the bare from basename(remote_url) — both diverged from the execution
    coordinates. The new identity must NOT reproduce either."""
    title = "Api CodeIgniter 4 endpoints públicos v1 y comando de diagnóstico"
    remote_url = "https://github.com/daycry/test-mailchimp-agent-ai.git"

    old_autopr_branch = make_plan_branch_name(PLAN_ID, _slugify(f"Plan: {title}"))
    old_autopr_bare = _repo_name_from_url(remote_url)  # "test-mailchimp-agent-ai"

    ident = plan_git_identity(PLAN_ID, PLAN_SLUG, PROJECT_SLUG)
    # The old branch carried an extra "plan-" prefix (from the "Plan: " title) and
    # different slugify semantics — the new one must not match it.
    assert ident.plan_branch != old_autopr_branch
    assert "plan-plan-" not in ident.plan_branch
    # The bare is project.slug, not the URL basename.
    assert ident.project_slug != old_autopr_bare
    assert ident.project_slug == PROJECT_SLUG


def test_identity_stable_for_non_ascii_and_long_titles() -> None:
    """Whatever the title's shape, the identity depends only on the persisted
    slugs, so it is deterministic and container-safe."""
    ident_a = plan_git_identity(PLAN_ID, PLAN_SLUG, PROJECT_SLUG)
    ident_b = plan_git_identity(PLAN_ID, PLAN_SLUG, PROJECT_SLUG)
    assert ident_a == ident_b
    assert ident_a.plan_branch.startswith("plan/019f1397-")
