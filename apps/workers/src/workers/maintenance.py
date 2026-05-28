"""Periodic maintenance tasks (Plan 06.5 Fase D — task_06_5_13).

Four Celery tasks driven by Celery beat (see `beat_schedule.py`):

  * `workers.idle_sweep_pools`        every 30s
  * `workers.expire_review_runtimes`  every 5 min
  * `workers.purge_dep_cache`         daily at 03:00 UTC
  * `workers.prune_worktrees`         daily at 03:30 UTC

These are best-effort cleanup jobs — a single failure must not crash
beat itself. Each task catches its own exceptions and logs them; the
beat scheduler keeps firing on its cadence regardless.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")

# Idle window after which a `running` review-runtime is suspended
# (containers paused). Mirrors the in-memory manager default.
_SUSPEND_IDLE_AFTER = timedelta(hours=24)


# ---------------------------------------------------------------------------
# idle_sweep_pools — every 30s
# ---------------------------------------------------------------------------


@app.task(name="workers.idle_sweep_pools")  # type: ignore[misc]
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
# expire_review_runtimes — every 5 min
# ---------------------------------------------------------------------------


@app.task(name="workers.expire_review_runtimes")  # type: ignore[misc]
def expire_review_runtimes() -> dict[str, Any]:
    """Mark overdue review-runtimes as `expired` + suspend idle ones.

    Two DB sweeps:
      1. ``status='running' AND expires_at < now`` → ``expired``.
      2. ``status='running' AND last_activity_at < now - 24h`` →
         ``suspended`` (containers should be paused by the worker
         that owns them; out of scope here).
    """
    settings = get_settings()
    return asyncio.run(_expire_review_runtimes(settings))


async def _expire_review_runtimes(settings: Settings) -> dict[str, Any]:
    """Async core — owns the engine lifecycle."""
    # Lazy import — avoids paying the api_server import cost on workers
    # that don't route the `review` queue.
    from api_server.db.review_session_repo import (
        list_running_idle,
        list_running_overdue,
        mark_terminal,
        suspend_session,
    )

    expired = 0
    suspended = 0
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db, db.begin():
            overdue = await list_running_overdue(db)
            for row in overdue:
                await mark_terminal(db, row.id, status="expired")
                expired += 1
        async with sessionmaker() as db, db.begin():
            idle = await list_running_idle(db, idle_for=_SUSPEND_IDLE_AFTER)
            for row in idle:
                await suspend_session(db, row.id)
                suspended += 1
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.expire_review_runtimes.error", error=str(exc))
        return {"expired": expired, "suspended": suspended, "error": str(exc)}
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.expire_review_runtimes.done",
        expired=expired,
        suspended=suspended,
    )
    return {"expired": expired, "suspended": suspended}


# ---------------------------------------------------------------------------
# purge_dep_cache — daily 03:00
# ---------------------------------------------------------------------------


@app.task(name="workers.purge_dep_cache")  # type: ignore[misc]
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


@app.task(name="workers.prune_worktrees")  # type: ignore[misc]
def prune_worktrees() -> dict[str, Any]:
    """Remove worktrees idle past the TTL (default 30d).

    Walks `<data_root>/projects/*/repos/*/worktrees/` and prunes per
    repo. Each removed worktree is also unregistered from its bare via
    `git worktree remove --force` so `git worktree list` stays clean.

    The walk picks up bare repos dynamically — we don't keep a registry.
    A repo that's still active will have its worktrees touched recently
    and survive the prune.
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
                    removed = mgr.prune_idle()
                    total += len(removed)
                except Exception as exc:  # pragma: no cover
                    _log.warning(
                        "maintenance.prune_worktrees.repo_error",
                        repo=str(repo_entry),
                        error=str(exc),
                    )

    _log.info("maintenance.prune_worktrees.done", pruned=total)
    return {"pruned": total}
