"""Filesystem/pool cleanup beat tasks (Plan 06.5 Fase D — task_06_5_13).

  * `workers.idle_sweep_pools`  every 30s
  * `workers.purge_dep_cache`   daily at 03:00 UTC
  * `workers.prune_worktrees`   daily at 03:30 UTC

Best-effort cleanup jobs — a single failure must not crash beat itself.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import structlog

from workers.celery_app import app
from workers.config import get_settings

_log = structlog.get_logger("workers.maintenance")


# ---------------------------------------------------------------------------
# idle_sweep_pools — every 30s
# ---------------------------------------------------------------------------


@app.task(name="workers.idle_sweep_pools")  # type: ignore[untyped-decorator]
def idle_sweep_pools() -> dict[str, Any]:
    """Trim runtime pools (Plan 06 Fase E2) that have idle slots above
    `min`.

    The `RuntimePool` instances are per-worker-process (in-memory) — a
    beat task running in a separate process can't sweep them. The real
    sweeping is done in-process by a `RuntimePool`'s own ticker (set
    up by `apps/workers/__main__.py` at boot, Plan 06.5 Fase F). This
    task exists so beat has a registered name to call; the body is a
    no-op heartbeat for now.
    """
    _log.debug("maintenance.idle_sweep_pools.tick")
    return {"swept": 0, "note": "per-process pool sweep — see Fase F"}


# ---------------------------------------------------------------------------
# purge_dep_cache — daily 03:00
# ---------------------------------------------------------------------------


@app.task(name="workers.purge_dep_cache")  # type: ignore[untyped-decorator]
def purge_dep_cache() -> dict[str, Any]:
    """Drop dep-cache entries older than the configured TTL (default 30d).

    The cache lives at `<data_root>/dep-cache/`. The `DepCacheManager`
    walks subdirectories and removes those whose mtime is past the
    threshold; the next test run re-installs deps.
    """
    settings = get_settings()
    try:
        from shared_test_runtimes import DepCacheManager
    except ImportError as exc:
        _log.warning("maintenance.purge_dep_cache.import_error", error=str(exc))
        return {"purged": 0, "error": "shared-test-runtimes not installed"}

    cache_root = Path(settings.data_root) / "dep-cache"
    if not cache_root.exists():
        return {"purged": 0, "note": f"{cache_root} does not exist yet"}

    mgr = DepCacheManager(cache_root)
    try:
        removed = mgr.purge_expired()
    except Exception as exc:  # pragma: no cover
        _log.warning("maintenance.purge_dep_cache.error", error=str(exc))
        return {"purged": 0, "error": str(exc)}

    _log.info("maintenance.purge_dep_cache.done", purged=len(removed))
    return {"purged": len(removed), "paths": [str(p) for p in removed]}


# ---------------------------------------------------------------------------
# prune_worktrees — daily 03:30
# ---------------------------------------------------------------------------


async def _prunable_plan_branches(settings: Any) -> set[str]:
    """G-08: ramas ``plan/*`` de planes CERRADOS (completed/archived) cuyo PR ya
    se abrió (``pr_url``) — su copia vive en el remoto; la local es podable."""
    from api_server.db.domain import Plan
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from workers.plan_git import make_plan_branch_name

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            rows = (
                await db.execute(
                    select(Plan.id, Plan.slug).where(
                        Plan.status.in_(("completed", "archived")),
                        Plan.pr_url.is_not(None),
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    return {make_plan_branch_name(str(plan_id), slug or "") for plan_id, slug in rows}


_STALE_LOCK_AGE_S = 24 * 3600


def _housekeep_bare(repo_path: Path, prunable_branches: set[str]) -> tuple[int, int]:
    """Un bare: worktree prune + gc ligero + locks huérfanos + poda de ramas
    de planes cerrados (con ref de rescate). Devuelve (locks, ramas)."""
    import contextlib

    from workers.git_repos import GitCommandError, _run_git

    with contextlib.suppress(GitCommandError):
        _run_git("worktree", "prune", cwd=repo_path)

    # Locks huérfanos: los .lock legítimos de git son efímeros (ms); uno con
    # >24h es el resto de una operación abortada y bloquea para siempre.
    locks_removed = 0
    threshold = time.time() - _STALE_LOCK_AGE_S
    for lock in repo_path.rglob("*.lock"):
        try:
            if lock.is_file() and lock.stat().st_mtime < threshold:
                lock.unlink()
                locks_removed += 1
                _log.warning("maintenance.git_housekeeping.stale_lock_removed", lock=str(lock))
        except OSError:
            continue

    with contextlib.suppress(GitCommandError):
        _run_git("gc", "--prune=30.days.ago", "--quiet", cwd=repo_path)

    branches_pruned = 0
    if prunable_branches:
        try:
            local = set(
                _run_git(
                    "for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=repo_path
                ).split()
            )
        except GitCommandError:
            local = set()
        for branch in sorted(prunable_branches & local):
            try:
                tip = _run_git("rev-parse", branch, cwd=repo_path).strip()
                # Ref de rescate: el tip sobrevive aunque el PR remoto se
                # descartara; gc lo respeta.
                _run_git("update-ref", f"refs/rescue/{branch}", tip, cwd=repo_path)
                _run_git("branch", "-D", branch, cwd=repo_path)
                branches_pruned += 1
            except GitCommandError as exc:
                _log.warning(
                    "maintenance.git_housekeeping.branch_prune_failed",
                    branch=branch,
                    error=str(exc),
                )
    return locks_removed, branches_pruned


@app.task(name="workers.git_housekeeping")  # type: ignore[untyped-decorator]
def git_housekeeping() -> dict[str, Any]:
    """Higiene mensual de los bare repos (G-08): ``git worktree prune`` + gc
    ligero (`--prune=30.days.ago`), borra locks huérfanos (>24h, restos de
    operaciones abortadas — el `initializing` de dev llevaba desde el 03-07) y
    poda ramas ``plan/*`` de planes completed/archived con PR abierto (su copia
    vive en el remoto), dejando ``refs/rescue/{branch}`` como red."""
    settings = get_settings()
    projects_root = Path(settings.data_root) / "projects"
    if not projects_root.exists():
        return {"repos": 0, "locks_removed": 0, "branches_pruned": 0}

    try:
        prunable = asyncio.run(_prunable_plan_branches(settings))
    except Exception as exc:
        _log.warning("maintenance.git_housekeeping.branches_query_error", error=str(exc))
        prunable = set()

    repos = 0
    locks_removed = 0
    branches_pruned = 0
    for repo_path in projects_root.glob("*/*/repos/*.git"):
        if not repo_path.is_dir():
            continue
        repos += 1
        try:
            locks, branches = _housekeep_bare(repo_path, prunable)
            locks_removed += locks
            branches_pruned += branches
        except Exception as exc:  # pragma: no cover — un repo malo no frena el resto
            _log.warning(
                "maintenance.git_housekeeping.repo_error", repo=str(repo_path), error=str(exc)
            )

    result = {"repos": repos, "locks_removed": locks_removed, "branches_pruned": branches_pruned}
    _log.info("maintenance.git_housekeeping.done", **result)
    return result


async def _build_worktree_policy(settings: Any) -> dict[str, str]:
    """G-07: política de poda por worktree (nombre = task id) leída de la DB.

    - plan CERRADO (completed/cancelled/archived o soft-borrado) → ``closed``
      (TTL 48h);
    - task ``blocked`` → ``keep`` (la escena del crimen se conserva);
    - resto → sin entrada (``default``, TTL 30d).
    Best-effort: si la DB no responde, policy vacía = poda clásica por TTL.
    """
    from api_server.db.domain import Plan, Task
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    closed = ("completed", "cancelled", "archived")
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            rows = (
                await db.execute(
                    select(Task.id, Task.status, Plan.status, Plan.deleted_at).join(
                        Plan, Task.plan_id == Plan.id, isouter=True
                    )
                )
            ).all()
    finally:
        await engine.dispose()

    policy: dict[str, str] = {}
    for task_id, task_status, plan_status, plan_deleted in rows:
        if plan_status in closed or plan_deleted is not None:
            policy[str(task_id)] = "closed"
        elif task_status == "blocked":
            policy[str(task_id)] = "keep"
    return policy


@app.task(name="workers.prune_worktrees")  # type: ignore[untyped-decorator]
def prune_worktrees() -> dict[str, Any]:
    """Remove worktrees past their TTL, aware of plan/task state (G-07).

    Walks `<data_root>/projects/*/repos/*/worktrees/` and prunes per
    repo via ``prune_by_policy``: plan cerrado → 48h, task blocked →
    conservar, resto → 30d; con ref de rescate ``refs/rescue/{task}`` si
    el HEAD no está en ninguna rama. Each removed worktree is also
    unregistered from its bare so `git worktree list` stays clean.

    The walk picks up bare repos dynamically — we don't keep a registry.
    """
    settings = get_settings()
    try:
        # BareRepoLayout + WorktreeManager need a (tenant_slug, project_slug,
        # repo_name) triple to find their files. Since we want to prune
        # across all of them, walk the filesystem and instantiate one
        # manager per (tenant, project, repo) found.
        from workers.git_repos import BareRepoLayout, WorktreeManager
    except ImportError as exc:
        _log.warning("maintenance.prune_worktrees.import_error", error=str(exc))
        return {"pruned": 0, "error": "workers.git_repos not importable"}

    projects_root = Path(settings.data_root) / "projects"
    if not projects_root.exists():
        return {"pruned": 0, "note": f"{projects_root} does not exist yet"}

    try:
        policy = asyncio.run(_build_worktree_policy(settings))
    except Exception as exc:
        _log.warning("maintenance.prune_worktrees.policy_error", error=str(exc))
        policy = {}

    total = 0
    for tenant_dir in projects_root.iterdir():
        if not tenant_dir.is_dir():
            continue
        for project_dir in tenant_dir.iterdir():
            if not project_dir.is_dir():
                continue
            repos_dir = project_dir / "repos"
            if not repos_dir.exists():
                continue
            for repo_entry in repos_dir.iterdir():
                # Bare repos end in `.git`.
                if not repo_entry.name.endswith(".git"):
                    continue
                repo_name = repo_entry.name[: -len(".git")]
                layout = BareRepoLayout(
                    data_root=Path(settings.data_root),
                    tenant_slug=tenant_dir.name,
                    project_slug=project_dir.name,
                )
                mgr = WorktreeManager(layout, repo_name)
                try:
                    removed = mgr.prune_by_policy(policy)
                    total += len(removed)
                except Exception as exc:  # pragma: no cover
                    _log.warning(
                        "maintenance.prune_worktrees.repo_error",
                        repo=str(repo_entry),
                        error=str(exc),
                    )

    _log.info("maintenance.prune_worktrees.done", pruned=total)
    return {"pruned": total}
