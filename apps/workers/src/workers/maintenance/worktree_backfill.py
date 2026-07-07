"""Worktree back-fill — pass (d) of the convergence reconciler (M4).

Non-atomicity DB↔git: ``finalize_execution`` + ``transition_task_after_run``
commit (task → in_review/done) BEFORE ``_commit_and_push_worktree`` runs; a crash
in that window advances the task but the diff never reaches the plan branch → the
final PR is incomplete. This pass finds terminal executions of OPEN plans whose
diff is missing from the bare and pushes the surviving worktree.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.config import Settings

_log = structlog.get_logger("workers.maintenance")


def _backfill_worktree_to_bare(
    *,
    data_root: str,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    task_id: str,
    execution_id: str,
) -> str:
    """Push a crashed run's surviving worktree diff to the plan branch (M4, sync).

    Idempotency is keyed by the ``Execution-Id`` trailer: if the plan branch in the
    bare already carries this execution's commit, no-op. Otherwise commit any
    uncommitted work (a clean tree is fine — a committed-but-unpushed sub-window) and,
    only if the worktree HEAD actually carries this execution's commit, push it. A run
    that produced NO change (clean tree, no such commit) is left alone — no empty push.
    Returns one of ``pushed`` / ``already_present`` / ``no_change`` / ``no_worktree`` /
    ``no_bare``. Raises ``GitCommandError`` on a real git failure (rebase conflict…)."""
    from pathlib import Path

    from workers.git_repos import BareRepoLayout, GitCommandError, _run_git
    from workers.plan_git import (
        CommitTrailers,
        PlanGitPolicies,
        PlanGitWorkflow,
        commit_task,
        make_plan_branch_name,
    )

    layout = BareRepoLayout(
        data_root=Path(data_root), tenant_slug=tenant_slug, project_slug=project_slug
    )
    bare = layout.bare_repo_path(project_slug)
    worktree = layout.worktree_path(str(task_id))
    branch = make_plan_branch_name(str(plan_id), plan_slug)
    if not bare.is_dir():
        return "no_bare"
    if not worktree.is_dir():
        return "no_worktree"

    def _has_execution_commit(repo_args: tuple[str, ...], ref: str) -> bool:
        try:
            out = _run_git(
                *repo_args,
                "log",
                ref,
                "-F",
                f"--grep=Execution-Id: {execution_id}",
                "--format=%H",
            )
        except GitCommandError:
            return False
        return bool(out.strip())

    if _has_execution_commit(("-C", str(bare)), branch):
        return "already_present"

    try:
        commit_task(
            worktree,
            message=f"task {task_id}",
            trailers=CommitTrailers(
                plan_id=str(plan_id), task_id=str(task_id), execution_id=str(execution_id)
            ),
        )
    except GitCommandError as exc:
        if "clean" not in str(exc).lower():
            raise  # a real git error — let the caller mark it

    if not _has_execution_commit(("-C", str(worktree)), "HEAD"):
        return "no_change"  # agent produced no diff — nothing legit to backfill

    PlanGitWorkflow(
        bare_repo_path=bare, plan_branch=branch, policies=PlanGitPolicies()
    ).push_review_to_bare(worktree)
    return "pushed"


async def _reconcile_unpushed_worktrees(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    now: datetime,
    min_age: timedelta,
) -> int:
    """Case (d): backfill worktree diffs a crashed run left out of the bare (M4).

    ``_reconcile_stuck_tasks`` is blind to this window (the task already left
    ``in_progress``). This pass finds terminal executions of OPEN plans whose diff
    is missing from the bare and pushes the surviving worktree (TTL 30d keeps it on
    disk). Age-gated (``min_age`` settle — the reconciler passes its 5-min window) so
    it never competes with a worker still in post-processing, and it takes the SAME
    per-task run lock as the live path (A6) so backfill and a live commit can't
    corrupt the worktree in parallel. Returns how many were pushed. Best-effort per
    candidate; a real git error marks the execution
    (``rebase_conflict``/``commit_failed``) like the live path."""
    from api_server.db.domain import (
        Execution,
        ExecutionStatus,
        Plan,
        PlanStatus,
        Project,
        Task,
    )
    from api_server.db.models import Organization
    from sqlalchemy import select

    from workers.git_repos import GitCommandError
    from workers.run_lock import acquire_run_lock, release_run_lock

    cutoff = now - min_age
    terminal_with_diff = (
        ExecutionStatus.DONE.value,
        ExecutionStatus.NEEDS_HUMAN_REVIEW.value,
    )
    open_plan = (
        PlanStatus.IN_PROGRESS.value,
        PlanStatus.PENDING_HUMAN_VALIDATION.value,
    )
    async with sessionmaker() as db:
        rows = (
            await db.execute(
                select(
                    Execution.id,
                    Execution.task_id,
                    Organization.slug,
                    Project.slug,
                    Plan.id,
                    Plan.slug,
                )
                .join(Task, Task.id == Execution.task_id)
                .join(Project, Project.id == Task.project_id)
                .join(Plan, Plan.id == Task.plan_id)
                .join(Organization, Organization.id == Execution.tenant_id)
                .where(
                    Execution.status.in_(terminal_with_diff),
                    Execution.completed_at.isnot(None),
                    Execution.completed_at <= cutoff,
                    Plan.status.in_(open_plan),
                    Organization.slug.isnot(None),
                    Project.slug.isnot(None),
                    Plan.slug.isnot(None),
                )
            )
        ).all()

    pushed = 0
    for exec_id, task_id, org_slug, project_slug, plan_id, plan_slug in rows:
        if not (org_slug and project_slug and plan_slug):
            continue
        token = f"reconcile-backfill:{exec_id}"
        # Same per-task lock as the live run (A6): if a live run holds it, skip this
        # pass rather than race its worktree; a later pass retries.
        if not await acquire_run_lock(redis, str(task_id), ttl_s=180, token=token):
            continue
        try:
            outcome = await asyncio.to_thread(
                _backfill_worktree_to_bare,
                data_root=settings.data_root,
                tenant_slug=org_slug,
                project_slug=project_slug,
                plan_id=str(plan_id),
                plan_slug=plan_slug,
                task_id=str(task_id),
                execution_id=str(exec_id),
            )
            if outcome == "pushed":
                pushed += 1
                _log.info(
                    "maintenance.reconcile_pipeline_state.worktree_backfilled",
                    task_id=str(task_id),
                    execution_id=str(exec_id),
                )
        except GitCommandError as exc:
            from workers.execution import _commit_abort_code, _mark_commit_failed

            abort_code = _commit_abort_code(exc)
            await _mark_commit_failed(sessionmaker, exec_id, abort_code)
            _log.warning(
                "maintenance.reconcile_pipeline_state.worktree_backfill_failed",
                task_id=str(task_id),
                abort_code=abort_code,
                error=str(exc),
            )
        finally:
            await release_run_lock(redis, str(task_id), token=token)
    return pushed
