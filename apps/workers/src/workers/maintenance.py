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
import contextlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")

# Idle window after which a `running` review-runtime is suspended
# (containers paused). Mirrors the in-memory manager default.
_SUSPEND_IDLE_AFTER = timedelta(hours=24)

# Tope duro de lotes por ejecución del back-fill — defensa contra un bucle
# infinito si el embedder devolviese siempre vectores inválidos (las filas
# seguirían NULL y el SELECT las re-encontraría). Con embedder sano el back-fill
# converge mucho antes (cada lote rellena sus filas y el siguiente ya no las ve).
_BACKFILL_MAX_BATCHES_PER_RUN = 10_000

# Tipo del factory de embedder que los tests sobreescriben (inyectan un
# HashEmbedder determinista para no depender de Ollama). El embedder concreto
# vive en ``api_server.ingestion.embeddings`` (un paquete hermano que el hook de
# mypy de pre-commit NO ve), así que aquí el tipo es ``Any``: lo único que se le
# pide es ``await .embed([...])`` / ``await .aclose()``, lo importamos lazy en
# ``_default_embedder_factory``.
EmbedderFactory = Callable[[Settings], Any]


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


# ---------------------------------------------------------------------------
# backfill_memory_embeddings — beat (Plan 06.17 task_06_17_03)
# ---------------------------------------------------------------------------


def _default_embedder_factory(settings: Settings) -> Any:
    """Embedder por defecto del back-fill: el ``OllamaEmbedder`` de la ingesta
    de KBs (mismo modelo / dimensión), apuntado a la URL de Ollama del worker.

    Import perezoso: solo los workers que ejecutan el back-fill pagan el coste
    de importar ``api_server.ingestion.embeddings``."""
    from api_server.ingestion.embeddings import OllamaEmbedder

    return OllamaEmbedder(base_url=settings.memory_embedder_base_url)


@app.task(name="workers.backfill_memory_embeddings")  # type: ignore[misc]
def backfill_memory_embeddings() -> dict[str, Any]:
    """Rellena los ``memory_entries.embedding`` NULL — IDEMPOTENTE, por lotes y
    throttled (Plan 06.17 task_06_17_03).

    Worker DEDICADO: nunca forma parte del flujo de un run (sin auto-retry). Una
    pasada solo toca filas con ``embedding IS NULL`` y deja el resto intacto, así
    que re-ejecutarlo es seguro y convergente. Las palancas (enabled / batch /
    throttle) son PLATFORM settings que un System Admin posee y que se leen en
    vivo al inicio de cada pasada.
    """
    settings = get_settings()
    return asyncio.run(
        _backfill_memory_embeddings_async(
            settings=settings,
            embedder_factory=_default_embedder_factory,
        )
    )


async def _backfill_memory_embeddings_async(
    *,
    settings: Settings,
    embedder_factory: EmbedderFactory,
) -> dict[str, Any]:
    """Núcleo async del back-fill. Los tests inyectan ``embedder_factory`` para
    usar un :class:`HashEmbedder` determinista sin Ollama.

    Recorre TODOS los tenants (rol BYPASSRLS, como el Memorizer): por eso no fija
    ``app.tenant_id``. La columna ``tenant_id`` viaja igualmente en cada UPDATE
    como defensa en profundidad. Devuelve un dict con cuántas filas se
    rellenaron y en cuántos lotes."""
    from api_server.db.platform_settings import (
        get_memory_backfill_batch_size,
        get_memory_backfill_enabled,
        get_memory_backfill_throttle_ms,
    )

    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    updated = 0
    batches = 0
    try:
        async with sessionmaker() as session:
            enabled = await get_memory_backfill_enabled(session)
            batch_size = await get_memory_backfill_batch_size(session)
            throttle_ms = await get_memory_backfill_throttle_ms(session)
        if not enabled:
            _log.info("maintenance.backfill_memory_embeddings.disabled")
            return {"updated": 0, "batches": 0, "reason": "disabled"}

        embedder = embedder_factory(settings)
        try:
            while batches < _BACKFILL_MAX_BATCHES_PER_RUN:
                async with sessionmaker() as session, session.begin():
                    rows = (
                        await session.execute(
                            sa_text(
                                "SELECT id, tenant_id, content FROM memory_entries"
                                " WHERE embedding IS NULL AND deleted_at IS NULL"
                                " ORDER BY created_at"
                                " LIMIT :limit"
                                " FOR UPDATE SKIP LOCKED"
                            ),
                            {"limit": batch_size},
                        )
                    ).all()
                    if not rows:
                        break
                    batches += 1

                    contents = [r.content for r in rows]
                    try:
                        vectors = await embedder.embed(contents)
                    except Exception as exc:  # EmbeddingError u otro fallo de red
                        _log.warning(
                            "maintenance.backfill_memory_embeddings.embed_failed",
                            error=str(exc),
                            count=len(contents),
                        )
                        # No marcamos nada: el commit del bloque libera el lock y
                        # estas filas se reintentan en la PRÓXIMA pasada del beat
                        # (idempotente, sin auto-retry dentro del run).
                        break
                    if len(vectors) != len(rows):
                        _log.warning(
                            "maintenance.backfill_memory_embeddings.count_mismatch",
                            expected=len(rows),
                            got=len(vectors),
                        )
                        break

                    for row, vec in zip(rows, vectors, strict=True):
                        qvec = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
                        await session.execute(
                            sa_text(
                                "UPDATE memory_entries"
                                " SET embedding = CAST(:qvec AS vector)"
                                " WHERE id = :id AND tenant_id = :tenant_id"
                            ),
                            {"qvec": qvec, "id": row.id, "tenant_id": row.tenant_id},
                        )
                        updated += 1

                if throttle_ms > 0:
                    await asyncio.sleep(throttle_ms / 1000.0)
        finally:
            await embedder.aclose()
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.backfill_memory_embeddings.error", error=str(exc))
        return {"updated": updated, "batches": batches, "error": str(exc)}
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.backfill_memory_embeddings.done",
        updated=updated,
        batches=batches,
    )
    return {"updated": updated, "batches": batches}


# ---------------------------------------------------------------------------
# promote_ready_plans — every 30s (prod-06 task_prod06_dag_02, safety net)
# ---------------------------------------------------------------------------
@app.task(name="workers.promote_ready_plans")  # type: ignore[misc]
def promote_ready_plans() -> dict[str, Any]:
    """Safety-net DAG promotion: across every ``in_progress`` plan, promote
    eligible ``backlog`` tasks to ``ready`` and announce the undispatched ones.

    The instant path is ``start-execution`` (roots) + the DB trigger (dependents),
    but the trigger flips status WITHOUT publishing the event the dispatcher
    consumes, and an event can be lost — so a ready task could sit undispatched.
    This beat reconciles every 30s: it re-announces any ``ready`` task of an
    in-progress plan with no execution row. Idempotent (a dispatched task is
    skipped) and best-effort (never crashes beat)."""
    settings = get_settings()
    return asyncio.run(_promote_ready_plans_async(settings))


async def _promote_ready_plans_async(settings: Settings) -> dict[str, Any]:
    """Async core — owns the engine + redis lifecycle."""
    from api_server.dag_promotion import announce_ready_tasks, promote_ready_tasks
    from api_server.db.domain import Plan, PlanStatus
    from redis.asyncio import Redis
    from sqlalchemy import select

    engine = create_async_engine(settings.database_url)
    redis = Redis.from_url(settings.events_redis_url)
    plans_touched = 0
    promoted = 0
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            plan_ids = list(
                (
                    await db.execute(
                        select(Plan.id).where(Plan.status == PlanStatus.IN_PROGRESS.value)
                    )
                ).scalars()
            )
        for plan_id in plan_ids:
            async with sessionmaker() as db, db.begin():
                announced = await promote_ready_tasks(db, plan_id)
            if announced:
                plans_touched += 1
                promoted += len(announced)
                await announce_ready_tasks(redis, announced)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.promote_ready_plans.error", error=str(exc))
        return {"plans_touched": plans_touched, "promoted": promoted, "error": str(exc)}
    finally:
        await engine.dispose()
        with contextlib.suppress(Exception):
            await redis.aclose()

    _log.info(
        "maintenance.promote_ready_plans.done",
        plans_touched=plans_touched,
        promoted=promoted,
    )
    return {"plans_touched": plans_touched, "promoted": promoted}


# ---------------------------------------------------------------------------
# sweep_stale_executions — every 5 min (prod-06 task_prod06_zombi_01)
# ---------------------------------------------------------------------------
# A `running` execution older than this is presumed lost: the Celery child was
# SIGKILLed (OOM or the hard time limit) without finalising the row, leaving it
# `running` forever and possibly an orphan container. 7h = the 6h hard-limit cap
# (prod-06 decision 2 / zombi_03) + a 1h margin so a legitimately-long run is
# never reaped early.
_STALE_EXECUTION_AFTER = timedelta(hours=7)


@app.task(name="workers.sweep_stale_executions")  # type: ignore[misc]
def sweep_stale_executions() -> dict[str, Any]:
    """Close zombie executions + reap their orphan containers.

    No sweeper existed (workers-2): a hard-limit/OOM SIGKILL of the Celery child
    left ``executions.running`` rows and dangling agent-runtime containers. This
    beat finds ``running`` rows older than the stale threshold, marks them
    ``failed`` (``abort_code=stale_after_worker_loss``), transitions their task off
    ``in_progress`` (reusing the dag_01 policy → ``blocked``), and ``docker rm -f``
    their container by label. Best-effort (never crashes beat)."""
    settings = get_settings()
    return asyncio.run(_sweep_stale_executions_async(settings))


async def _sweep_stale_executions_async(
    settings: Settings,
    *,
    runner: Any = None,
    stale_after: timedelta = _STALE_EXECUTION_AFTER,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Async core. ``runner`` (a container runner with ``kill_by_label``) and
    ``now`` are injectable so the test drives it without Docker or wall-clock."""
    from api_server.db.domain import Execution, ExecutionStatus
    from sqlalchemy import select

    from workers.container import AgentContainerRunner
    from workers.execution import transition_task_after_run

    moment = now or datetime.now(UTC)
    cutoff = moment - stale_after
    engine = create_async_engine(settings.database_url)
    swept = 0
    reaped = 0
    try:
        if runner is None:
            runner = AgentContainerRunner(settings)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db, db.begin():
            stale = list(
                (
                    await db.execute(
                        select(Execution).where(
                            Execution.status == ExecutionStatus.RUNNING.value,
                            Execution.started_at < cutoff,
                        )
                    )
                ).scalars()
            )
            stale_ids = [str(e.id) for e in stale]
            for execution in stale:
                execution.status = ExecutionStatus.FAILED.value
                execution.abort_code = "stale_after_worker_loss"
                execution.completed_at = moment
                # Move the orphaned task off in_progress (dag_01 policy → blocked).
                await transition_task_after_run(db, execution.task_id, ExecutionStatus.FAILED.value)
                swept += 1
        # Reap lingering containers OUTSIDE the txn — Docker I/O must never hold
        # the DB transaction open. Best-effort per execution.
        for execution_id in stale_ids:
            with contextlib.suppress(Exception):
                reaped += runner.kill_by_label(execution_id)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.sweep_stale_executions.error", error=str(exc))
        return {"swept": swept, "reaped": reaped, "error": str(exc)}
    finally:
        await engine.dispose()

    _log.info("maintenance.sweep_stale_executions.done", swept=swept, reaped=reaped)
    return {"swept": swept, "reaped": reaped}


# ---------------------------------------------------------------------------
# refresh_budgets — every 5 min (prod-06 task_prod06_budget_01 / db-1)
# ---------------------------------------------------------------------------
@app.task(name="workers.refresh_budgets")  # type: ignore[misc]
def refresh_budgets() -> dict[str, Any]:
    """Periodic per-tenant budget sweep: re-derive the auto-pause flags and fire
    any threshold alerts.

    The dispatch START path reads ``paused_by_budget`` (``budget_pause_block``)
    but NOTHING wrote it in production (db-1): ``refresh_budget_pause_flags`` +
    ``maybe_alert_budgets`` had only tests as callers. The worker's
    post-execution hook keeps a single run's over-budget immediate; this beat is
    the safety net — it auto-clears a pause when a new period drops a scope back
    under 100%, and catches a missed hook or a manual spend correction. Cheap
    (one consumption query per tenant) and best-effort (per-tenant failures are
    isolated; never crashes beat)."""
    return asyncio.run(_refresh_budgets_async(get_settings()))


async def _refresh_budgets_async(
    settings: Settings,
    *,
    dispatcher: Any | None = None,
) -> dict[str, Any]:
    """Async core — owns the engine lifecycle. ``dispatcher`` is injectable so a
    test asserts the alert fan-out without a real broker; production builds the
    Celery dispatcher."""
    from api_server.budgets import sweep_tenant_budgets
    from api_server.budgets.consumption import CeleryBudgetAlertDispatcher
    from api_server.db.models import Organization
    from sqlalchemy import select

    engine = create_async_engine(settings.database_url)
    tenants = 0
    newly_paused = 0
    newly_cleared = 0
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            tenant_ids = list(
                (
                    await db.execute(
                        select(Organization.id).where(Organization.deleted_at.is_(None))
                    )
                ).scalars()
            )
        alert_dispatcher = dispatcher if dispatcher is not None else CeleryBudgetAlertDispatcher()
        for tenant_id in tenant_ids:
            try:
                async with sessionmaker() as db, db.begin():
                    result = await sweep_tenant_budgets(
                        db, tenant_id=tenant_id, dispatcher=alert_dispatcher
                    )
                tenants += 1
                newly_paused += len(result.refresh.newly_paused)
                newly_cleared += len(result.refresh.newly_cleared)
            except Exception as exc:  # isolate a single tenant's failure
                _log.warning(
                    "maintenance.refresh_budgets.tenant_error",
                    tenant_id=str(tenant_id),
                    error=str(exc),
                )
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.refresh_budgets.done",
        tenants=tenants,
        newly_paused=newly_paused,
        newly_cleared=newly_cleared,
    )
    return {"tenants": tenants, "newly_paused": newly_paused, "newly_cleared": newly_cleared}
