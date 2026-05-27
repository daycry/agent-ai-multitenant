"""Synchronous plan runner — demos / human tests glue (Plan 06 Fase H).

This module orchestrates the Plan 06 modules end-to-end in a *single
process*, without Celery and without long-running queues. It exists
to make the twelve human tests of Plan 06 runnable as scripts the
operator can execute by hand and observe live:

    setup_demo_06.py           seed project + repos + tasks
    demo_human_06_a_*.py       end-to-end pipeline
    demo_human_06_b_*.py       cache + aux services + multi-repo
    demo_human_06_c_*.py       pool + policies matrix
    demo_human_06_d_*.py       review-runtime + escalation + audit

The production orchestrator (apps/orchestrator) wraps these same
modules behind Celery beat schedules + DB-backed task events; that's
the scope of the follow-up Plan 06.5 (docs/roadmap/06.5-orchestrator-
wiring.md). Here we keep it deliberately simple — the runner is
purely synchronous, the stores are in-memory, and every step prints
its progress to stdout so operators see exactly what happened.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog
from api_server.plan_progress import (
    PlanProgress,
    TaskSnapshot,
    compute_plan_progress,
    transition_to_completed,
    transition_to_pending_human_validation,
)
from api_server.task_lifecycle import (
    InMemoryTaskStore,
    ReviewComment,
    TaskLifecycle,
    TaskRecord,
)
from workers.git_repos import (
    BareRepoLayout,
    BareRepoManager,
    WorktreeManager,
)
from workers.plan_git import (
    CommitTrailers,
    PlanGitPolicies,
    PlanGitWorkflow,
    commit_task,
    make_plan_branch_name,
)
from workers.runtime_pool import (
    PoolConfig,
    RuntimePool,
)

_log = structlog.get_logger("orchestrator.plan_runner")


@dataclass(frozen=True)
class StepResult:
    """One step's outcome — what plan_runner logs after each call.

    Demos render these as a sequence of `[OK]` / `[FAIL]` lines so
    the operator sees the state transitions in real time.
    """

    name: str
    ok: bool
    detail: str = ""


@dataclass
class PlanRunner:
    """In-process orchestrator for one plan run.

    Wires together the Fase A..H modules. The runner is **not** a
    drop-in replacement for the production orchestrator — it skips
    persistence, queueing, and the actual LLM call (the agent step
    is a deterministic stub that writes a file). What it gives us is
    a *real* execution path for the twelve human tests that exercises
    every module the plan added, end-to-end.
    """

    data_root: Path
    tenant_slug: str
    project_slug: str
    plan_id: str
    plan_slug: str
    policies: PlanGitPolicies = field(default_factory=PlanGitPolicies)
    pool_config: PoolConfig = field(default_factory=PoolConfig)

    # Injected at __post_init__.
    bare_mgr: BareRepoManager = field(init=False)
    task_store: InMemoryTaskStore = field(init=False)
    task_lc: TaskLifecycle = field(init=False)
    plan_branch: str = field(init=False)
    pool: RuntimePool = field(init=False)
    steps: list[StepResult] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        layout = BareRepoLayout(
            data_root=self.data_root,
            tenant_slug=self.tenant_slug,
            project_slug=self.project_slug,
        )
        self.bare_mgr = BareRepoManager(layout)
        self.task_store = InMemoryTaskStore()
        self.task_lc = TaskLifecycle(store=self.task_store)
        self.plan_branch = make_plan_branch_name(self.plan_id, self.plan_slug)

        spawn_counter = [0]

        def fake_spawn() -> str:
            spawn_counter[0] += 1
            return f"runtime-container-{spawn_counter[0]}"

        self.pool = RuntimePool(
            plan_id=self.plan_id,
            project_id=self.project_slug,
            config=self.pool_config,
            container_factory=fake_spawn,
        )

    # ----- helpers ----------------------------------------------------

    def _step(self, name: str, ok: bool, detail: str = "") -> StepResult:
        result = StepResult(name=name, ok=ok, detail=detail)
        self.steps.append(result)
        mark = "[ OK ]" if ok else "[FAIL]"
        print(f"{mark} {name}" + (f" — {detail}" if detail else ""), flush=True)
        return result

    # ----- repo setup -------------------------------------------------

    def ensure_repo(self, repo_name: str, *, remote_url: str | None = None) -> Path:
        path = self.bare_mgr.ensure_repo(repo_name, remote_url=remote_url)
        # Mirror what a real orchestrator does at plan start: fetch the
        # remote so the bare has a real HEAD/main, otherwise the first
        # `git branch plan/x HEAD` fails on a fresh `git init --bare`.
        if remote_url is not None:
            from workers.git_repos import _run_git

            _run_git("fetch", "origin", cwd=path)
            try:
                _run_git("update-ref", "refs/heads/main", "refs/remotes/origin/main", cwd=path)
                _run_git("symbolic-ref", "HEAD", "refs/heads/main", cwd=path)
            except Exception:  # - tolerated for non-main default
                pass
        try:
            display = path.relative_to(self.data_root)
        except ValueError:
            display = path
        self._step(f"bare_repo:{repo_name}", True, f"path={display}")
        return Path(path)

    # ----- task seeding ----------------------------------------------

    def seed_task(self, *, title: str, description: str = "") -> TaskRecord:
        task = TaskRecord(
            id=uuid.uuid4().hex[:12],
            plan_id=self.plan_id,
            title=title,
            description=description,
            status="backlog",
        )
        self.task_store.save(task)
        self.task_store.append_event(self._creation_event(task))
        self._step(f"task:{task.id}", True, f"title={title!r}")
        return task

    def _creation_event(self, task: TaskRecord) -> Any:
        from api_server.task_lifecycle import AuditEvent

        return AuditEvent(
            task_id=task.id,
            at=time.time(),
            kind="creation",
            actor="orchestrator:plan_runner",
            payload={"plan_id": task.plan_id, "title": task.title},
        )

    # ----- per-task execution ----------------------------------------

    def execute_task(
        self,
        task_id: str,
        repo_name: str,
        *,
        file_writer: Any,
        role: str = "implementador",
    ) -> StepResult:
        """Run one task end-to-end: pool slot → worktree → agent stub
        → commit + push.

        ``file_writer`` is a callable ``(worktree_path: Path) -> None``
        that simulates what the LangGraph agent would do (write a
        file). Demos pass closures that drop a per-task marker file.
        """
        task = self.task_store.get(task_id)
        if task is None:
            return self._step(f"execute:{task_id}", False, "task not found")

        task.status = "in_progress"
        self.task_store.save(task)

        with self.pool.acquire(role) as slot:
            wt_mgr = WorktreeManager(self.bare_mgr.layout, repo_name)
            wt_path = wt_mgr.add(task_id, branch=self.plan_branch)
            wt_mgr.sync_to_head(task_id, branch=self.plan_branch)
            file_writer(wt_path)

            try:
                sha = commit_task(
                    wt_path,
                    message=f"task: {task.title}",
                    trailers=CommitTrailers(
                        plan_id=self.plan_id,
                        task_id=task.id,
                        execution_id=f"exec-{slot.slot_id}",
                    ),
                )
            except Exception as exc:  # — surface clean failure
                task.status = "backlog"
                self.task_store.save(task)
                return self._step(f"commit:{task_id}", False, f"{type(exc).__name__}: {exc}")

        # Auto-review stub: always accept. Push to bare.
        wf = PlanGitWorkflow(
            bare_repo_path=self.bare_mgr.layout.bare_repo_path(repo_name),
            plan_branch=self.plan_branch,
            policies=self.policies,
        )
        wf.push_review_to_bare(wt_mgr._layout.worktree_path(task_id))
        # Remote push if policy says so.
        wf.push_branch_to_remote()

        task.status = "done"
        self.task_store.save(task)
        return self._step(f"execute:{task_id}", True, f"sha={sha[:8]} role={role}")

    # ----- reject path (for escalation demo) -------------------------

    def reject_task(self, task_id: str, comment: ReviewComment) -> TaskRecord:
        task = self.task_store.get(task_id)
        assert task is not None
        task.status = "in_review"
        self.task_store.save(task)
        self.task_lc.reject_review(task_id, comment=comment)
        new = self.task_store.get(task_id)
        assert new is not None
        self._step(
            f"reject:{task_id}",
            True,
            f"new_status={new.status} retries={new.retry_count}",
        )
        return new

    # ----- plan-level transitions -----------------------------------

    def progress(self) -> PlanProgress:
        snapshots = [
            TaskSnapshot(id=t.id, status=t.status)
            for t in self.task_store._tasks.values()
            if t.plan_id == self.plan_id
        ]
        return compute_plan_progress(self.plan_id, snapshots)

    def try_transition_to_review(self) -> bool:
        snapshots = [
            TaskSnapshot(id=t.id, status=t.status)
            for t in self.task_store._tasks.values()
            if t.plan_id == self.plan_id
        ]
        result = transition_to_pending_human_validation("in_progress", snapshots)
        self._step(
            "plan:transition_to_review",
            result.transitioned,
            result.reason or "ok",
        )
        return bool(result.transitioned)

    def try_complete(
        self,
        *,
        human_verdict: str | None,
        pr_merged: bool,
    ) -> bool:
        result = transition_to_completed(
            "pending_human_validation",
            human_verdict=human_verdict,  # type: ignore[arg-type]
            pr_merged=pr_merged,
        )
        self._step(
            "plan:complete",
            result.transitioned,
            result.reason or "ok",
        )
        return bool(result.transitioned)

    # ----- cleanup --------------------------------------------------

    def shutdown(self) -> None:
        self.pool.shutdown()
        self._step("plan:shutdown", True, "pool destroyed")


__all__ = ["PlanRunner", "StepResult"]
