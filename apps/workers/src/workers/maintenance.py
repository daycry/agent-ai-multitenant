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
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.maintenance")

# Idle window after which a `running` review-runtime is suspended
# (containers paused). Mirrors the in-memory manager default.
_SUSPEND_IDLE_AFTER = timedelta(hours=24)

# Terminal review-session statuses — a session here no longer holds a runtime, so
# the expiry sweep reaps its containers (`docker rm -f`) + soft-deletes it (C8 F41).
_TERMINAL_REVIEW_STATUSES = ("approved", "rejected", "expired", "cancelled")

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
    """Expire overdue review-runtimes, suspend idle ones, reap terminal ones.

    Four DB sweeps (C8 F40/F41 — the in-memory ReviewRuntimeManager logic, now
    cabled to the repo-DB + beat as the single source of truth):
      1. ``status='running' AND expires_at < now`` → ``expired``, AND the owning
         Plan ``pending_human_validation`` → ``blocked`` (idempotent), AND an
         escalation notification to the owner.
      2. ``status='running' AND last_activity_at < now - 24h`` → ``suspended``
         (containers paused by the worker that owns them; out of scope here).
      3. Every TERMINAL session (approved/rejected/expired/cancelled) with leftover
         containers → ``docker rm -f`` them by id + soft-delete the row (closes the
         container leak the verdict path left — submit_verdict only marks terminal).
    """
    settings = get_settings()
    return asyncio.run(_expire_review_runtimes(settings))


def plan_status_after_expiry(current_status: str) -> str | None:
    """Pure decision: what status a plan moves to when its review session expires.

    A plan still awaiting human validation (``pending_human_validation``) is moved
    to ``blocked`` so the operator sees it needs attention; any other status is left
    untouched (``None``) — IDEMPOTENT, so re-running the sweep never re-transitions
    an already-blocked / completed / rejected plan (C8 F40)."""
    return "blocked" if current_status == "pending_human_validation" else None


async def _block_plan_for_expired_session(db: AsyncSession, row: Any) -> dict[str, Any] | None:
    """Idempotently move an expired session's plan off ``pending_human_validation``
    and return the owner-notification payload (or ``None`` when no transition was
    warranted). The Plan load is BYPASSRLS (worker engine); the session row already
    carries the tenant scope."""
    from api_server.db.domain import Plan

    plan = await db.get(Plan, row.plan_id)
    if plan is None:
        return None
    new_status = plan_status_after_expiry(plan.status)
    if new_status is None:
        return None
    plan.status = new_status
    await db.flush()
    spec = row.spec or {}
    return {
        "tenant_id": str(row.tenant_id),
        "plan_id": str(row.plan_id),
        "session_id": str(row.id),
        "plan_title": str(spec.get("plan_title") or spec.get("title") or ""),
        "owner_user_id": spec.get("owner_user_id"),
    }


async def _enqueue_review_expiry_notification(payload: dict[str, Any]) -> None:
    """Best-effort: escalate an expired review session to the owner (C8 F40).

    Reuses the registered ``review_escalated`` event (priority lane). A broker /
    import failure is swallowed — the plan is already ``blocked`` in the DB, so the
    escalation is a notification, not a transaction to roll back."""
    try:
        from api_server.celery_client import enqueue_event_dispatch
    except ImportError:  # pragma: no cover - api_server always present in workers
        return
    event = {
        "event_type": "review_escalated",
        "tenant_id": payload["tenant_id"],
        "context": {
            "task_title": payload.get("plan_title") or "",
            "plan_id": payload["plan_id"],
            "session_id": payload["session_id"],
            "owner_user_id": payload.get("owner_user_id"),
            "reason": "verdict_timeout",
        },
        "locale": None,
    }
    await enqueue_event_dispatch(event)


def _reap_review_containers(container_ids: list[str]) -> int:
    """``docker rm -f`` a terminal session's leftover containers (C8 F41).

    Best-effort + idempotent: a missing daemon, an unimportable SDK, or an
    already-gone container each no-op. Returns how many containers were removed.
    Runs OUTSIDE any DB transaction (Docker I/O must never hold a txn open)."""
    if not container_ids:
        return 0
    try:
        import docker
    except ImportError:
        return 0
    try:
        client = docker.from_env()
        client.ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
        return 0
    removed = 0
    for cid in container_ids:
        try:
            client.containers.get(str(cid)).remove(force=True)
            removed += 1
        except Exception:  # already gone / not found — idempotent
            continue
    return removed


async def _list_terminal_sessions_with_containers(db: AsyncSession) -> list[Any]:
    """Terminal (approved/rejected/expired/cancelled), not soft-deleted sessions
    that still carry container ids — the reap candidates (C8 F41)."""
    from api_server.db.models import ReviewSession

    rows = (
        (
            await db.execute(
                select(ReviewSession).where(
                    ReviewSession.status.in_(_TERMINAL_REVIEW_STATUSES),
                    ReviewSession.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return [r for r in rows if r.container_ids]


async def _expire_review_runtimes(settings: Settings) -> dict[str, Any]:
    """Async core — owns the engine lifecycle."""
    # Lazy import — avoids paying the api_server import cost on workers
    # that don't route the `review` queue.
    from api_server.db.review_session_repo import (
        list_running_idle,
        list_running_overdue,
        mark_terminal,
        soft_delete_session,
        suspend_session,
    )

    expired = 0
    suspended = 0
    reaped = 0
    notify_payloads: list[dict[str, Any]] = []
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        # 1. Overdue → expired + plan blocked + escalation notification.
        async with sessionmaker() as db, db.begin():
            overdue = await list_running_overdue(db)
            for row in overdue:
                await mark_terminal(db, row.id, status="expired")
                expired += 1
                payload = await _block_plan_for_expired_session(db, row)
                if payload is not None:
                    notify_payloads.append(payload)
        # 2. Idle → suspended.
        async with sessionmaker() as db, db.begin():
            idle = await list_running_idle(db, idle_for=_SUSPEND_IDLE_AFTER)
            for row in idle:
                await suspend_session(db, row.id)
                suspended += 1
        # 3. Reap terminal sessions' leftover containers, then soft-delete them.
        #    Docker I/O runs OUTSIDE the txn; the soft-delete makes the sweep
        #    idempotent (a reaped row is no longer re-listed).
        async with sessionmaker() as db:
            terminal = await _list_terminal_sessions_with_containers(db)
            to_reap = [(r.id, [str(c) for c in r.container_ids]) for r in terminal]
        for _session_id, container_ids in to_reap:
            _reap_review_containers(container_ids)
            reaped += 1
        if to_reap:
            async with sessionmaker() as db, db.begin():
                for session_id, _container_ids in to_reap:
                    deleted = await soft_delete_session(db, session_id)
                    if deleted is not None:
                        deleted.container_ids = []
                        await db.flush()
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.expire_review_runtimes.error", error=str(exc))
        return {
            "expired": expired,
            "suspended": suspended,
            "reaped": reaped,
            "error": str(exc),
        }
    finally:
        await engine.dispose()

    # Notifications OUTSIDE the engine lifecycle — best-effort, one per expired plan.
    for payload in notify_payloads:
        await _enqueue_review_expiry_notification(payload)

    _log.info(
        "maintenance.expire_review_runtimes.done",
        expired=expired,
        suspended=suspended,
        reaped=reaped,
    )
    return {"expired": expired, "suspended": suspended, "reaped": reaped}


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

# Sweep de huérfanos (2026-07-03, gotcha engine-restart): una fila `running`
# cuyo contenedor YA NO EXISTE no puede terminar jamás — no hace falta esperar
# las 7 h. La gracia cubre la ventana provisión→launch (la fila se crea antes
# de arrancar el contenedor: resolución de modelo + worktree, segundos).
_ORPHAN_CONTAINER_GRACE = timedelta(minutes=5)


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


async def _remove_exited_terminal_containers(engine: Any, runner: Any) -> int:
    """F0.6 (auditoría 2026-07-02): reap de contenedores ``exited`` cuya
    execution ya es terminal (o cuya fila no existe). ``run_streamed`` solo
    limpia su contenedor si el proceso worker sigue vivo; el path de supersede
    no limpia el contenedor del run viejo — en un host que duerme a diario se
    acumulan exited(255). Un run VIVO (fila running) nunca se toca: su
    contenedor exited puede ser forense en curso."""
    from api_server.db.domain import Execution, ExecutionStatus
    from sqlalchemy import select

    exited = list(runner.list_exited_managed())
    if not exited:
        return 0
    exec_uuids: list[UUID] = []
    for _cid, eid in exited:
        with contextlib.suppress(ValueError):
            exec_uuids.append(UUID(eid))
    statuses: dict[str, str] = {}
    if exec_uuids:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            rows = await db.execute(
                select(Execution.id, Execution.status).where(Execution.id.in_(exec_uuids))
            )
            statuses = {str(row[0]): str(row[1]) for row in rows}
    removed = 0
    for container_id, eid in exited:
        if statuses.get(eid) == ExecutionStatus.RUNNING.value:
            continue
        with contextlib.suppress(Exception):
            if runner.remove_container(container_id):
                removed += 1
    return removed


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
    orphan_cutoff = moment - _ORPHAN_CONTAINER_GRACE
    engine = create_async_engine(settings.database_url)
    swept = 0
    reaped = 0
    containers_removed = 0
    try:
        if runner is None:
            runner = AgentContainerRunner(settings)
        # Contenedores gestionados EXISTENTES (cualquier estado) — una llamada al
        # daemon, FUERA de la txn. None = daemon sin respuesta: el sweep de
        # huérfanos no barre nada (solo actúa el umbral por edad).
        alive_ids: set[str] | None = None
        if hasattr(runner, "list_managed_execution_ids"):
            alive_ids = runner.list_managed_execution_ids()
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db, db.begin():
            candidates = list(
                (
                    await db.execute(
                        select(Execution).where(
                            Execution.status == ExecutionStatus.RUNNING.value,
                            Execution.started_at < orphan_cutoff,
                        )
                    )
                ).scalars()
            )
            stale_ids = []
            for execution in candidates:
                stale_by_age = execution.started_at < cutoff
                # Huérfano (2026-07-03): pasada la gracia, sin contenedor en el
                # daemon → el run no puede terminar jamás; cerrarlo YA en vez de
                # dejarlo 7 h de zombi vetando el re-despacho de su task.
                orphaned = alive_ids is not None and str(execution.id) not in alive_ids
                if not (stale_by_age or orphaned):
                    continue
                stale_ids.append(str(execution.id))
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
        containers_removed = await _remove_exited_terminal_containers(engine, runner)
    except Exception as exc:  # pragma: no cover — defensive logging
        _log.warning("maintenance.sweep_stale_executions.error", error=str(exc))
        return {
            "swept": swept,
            "reaped": reaped,
            "containers_removed": containers_removed,
            "error": str(exc),
        }
    finally:
        await engine.dispose()

    _log.info(
        "maintenance.sweep_stale_executions.done",
        swept=swept,
        reaped=reaped,
        containers_removed=containers_removed,
    )
    return {"swept": swept, "reaped": reaped, "containers_removed": containers_removed}


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


# ---------------------------------------------------------------------------
# sample_queue_metrics — every 30s (prod-06 task_prod06_dag_03, parte B)
# ---------------------------------------------------------------------------
@app.task(name="workers.sample_queue_metrics")  # type: ignore[misc]
def sample_queue_metrics() -> dict[str, Any]:
    """Sample Celery queue depth + task counts per status and write the
    node-exporter textfile (prod-06 task_prod06_dag_03).

    Emits ``agentic_celery_queue_depth{queue}`` (Redis LLEN per Celery queue) and
    ``agentic_tasks_by_status{status}`` (non-deleted tasks per lifecycle status,
    all tenants). prod-08 owns the scrape job + CeleryQueueGrowing alert + the
    dashboard; this only EMITS. Cheap (one LLEN per queue + one GROUP BY) and
    best-effort (a sampling failure never crashes beat)."""
    return asyncio.run(_sample_queue_metrics_async(get_settings()))


async def _collect_queue_depths(redis: Any, queue_names: tuple[str, ...]) -> dict[str, int]:
    """Redis ``LLEN`` per Celery queue (a queue is a Redis list under its name)."""
    depths: dict[str, int] = {}
    for name in queue_names:
        with contextlib.suppress(Exception):  # a missing key LLENs to 0; other errors skip
            depths[name] = int(await redis.llen(name))
    return depths


async def _collect_status_counts(session: Any) -> dict[str, int]:
    """Count ``tasks`` rows grouped by lifecycle status (all tenants — the worker
    engine is BYPASSRLS). ``tasks`` is not soft-deletable (no ``deleted_at``)."""
    rows = await session.execute(sa_text("SELECT status, count(*) FROM tasks GROUP BY status"))
    return {str(status): int(count) for status, count in rows.all()}


async def _sample_queue_metrics_async(
    settings: Settings,
    *,
    redis: Any | None = None,
) -> dict[str, Any]:
    """Async core — owns the redis + engine lifecycle. ``redis`` is injectable for
    tests. Always writes the textfile (even if a collector fails → that dimension
    is simply absent), so the file reflects the freshest successful sample."""
    from redis.asyncio import Redis

    from workers.celery_app import QUEUE_NAMES
    from workers.queue_metrics import write_queue_metrics

    own_redis = redis is None
    redis_client = redis if redis is not None else Redis.from_url(settings.broker_url)
    engine = create_async_engine(settings.database_url)
    queue_depths: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    try:
        queue_depths = await _collect_queue_depths(redis_client, QUEUE_NAMES)
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            status_counts = await _collect_status_counts(db)
    except Exception as exc:  # pragma: no cover — best-effort: never crash beat
        _log.warning("maintenance.sample_queue_metrics.error", error=str(exc))
    finally:
        await engine.dispose()
        if own_redis:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    written = write_queue_metrics(
        settings.queue_metrics_textfile_path,
        queue_depths=queue_depths,
        status_counts=status_counts,
    )
    _log.info(
        "maintenance.sample_queue_metrics.done",
        queues=len(queue_depths),
        statuses=len(status_counts),
        written=written,
    )
    return {"queue_depths": queue_depths, "status_counts": status_counts, "written": written}


# ---------------------------------------------------------------------------
# reconcile_pipeline_state — every 90s (audit C3 / P0.6, convergence safety net)
# ---------------------------------------------------------------------------
# The live event path moves a task/plan off a transient state the instant a run
# finishes, but an event can be lost (Redis blip, a worker SIGKILLed between the
# finalize txn and the publish) — leaving DERIVED state stuck: a task `in_progress`
# whose run already finished, an `in_review` task whose review was never dispatched,
# or an `in_progress` plan whose tasks are all done. Nothing else reconciles these,
# so the DAG silently stalls. This beat is the net: three idempotent best-effort
# passes that re-derive the state from the DB and re-emit the events the live path
# would have. Age thresholds keep it from racing a worker still post-processing.

# A task must sit `in_progress` (and its terminal execution must be settled) this
# long before we act, so we never compete with a worker still in its post-run
# processing (worktree commit / tests / deferred event publish).
_RECONCILE_STUCK_TASK_MIN_AGE = timedelta(minutes=5)
# An `in_review` task with an AI reviewer must sit this long with no live/recent
# review run before we re-announce it — avoids double-dispatching a review whose
# `in_review` event the orchestrator is still processing.
_RECONCILE_REVIEW_MIN_AGE = timedelta(minutes=5)

# Execution statuses that mean the run is OVER — the owning task must no longer be
# `in_progress`. Literal mirror of the terminal ``ExecutionStatus`` members, kept as
# strings so importing this module costs no api_server import. ``running`` and
# ``awaiting_human_approval`` are deliberately absent (a live run / an approval the
# approval branch owns — not the reconciler's concern).
_TERMINAL_EXECUTION_STATUSES = frozenset(
    {"done", "failed", "aborted", "cancelled", "needs_human_review"}
)


def _stuck_task_needs_reconcile(
    latest_exec_status: str | None,
    latest_exec_completed_at: datetime | None,
    *,
    now: datetime,
    min_age: timedelta,
) -> bool:
    """True when an `in_progress` task's LATEST execution is terminal and settled
    long enough that the task should be transitioned off `in_progress` (case a).

    Pure decision — no DB — so the candidate filter is unit-testable in isolation.
    A non-terminal (still `running`/`awaiting_human_approval`) or not-yet-settled
    latest execution is left alone (a worker may still be finishing it)."""
    if latest_exec_status is None or latest_exec_status not in _TERMINAL_EXECUTION_STATUSES:
        return False
    if latest_exec_completed_at is None:
        return False
    return latest_exec_completed_at <= now - min_age


def _orphan_review_needs_reannounce(
    *,
    reviewer_is_ai: bool,
    has_running_execution: bool,
    latest_completed_at: datetime | None,
    now: datetime,
    min_age: timedelta,
) -> bool:
    """True when an `in_review` task with an AI reviewer has NO live review run and
    nothing ran recently, so its `in_review` event should be re-announced (case b).

    Pure decision — no DB. A human reviewer is the peer-review path's concern; a
    running execution means the review is already in flight; a recently-completed
    execution means a run just finished (the implementer that moved it to review, or
    a review whose verdict is being applied) — in both we wait rather than duplicate."""
    if not reviewer_is_ai or has_running_execution:
        return False
    return latest_completed_at is None or latest_completed_at <= now - min_age


async def _reconcile_stuck_tasks(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    now: datetime,
    min_age: timedelta,
) -> int:
    """Case (a): transition tasks stuck `in_progress` whose last run is terminal.

    Reuses ``workers.execution.transition_task_after_run`` (the SAME dag_01 policy
    the worker applies: done→in_review/done, cancelled→cancelled, else→blocked) and
    re-emits the resulting ``task.status_changed`` so the board + the orchestrator
    converge. Per-task transaction + the `in_progress` guard inside
    ``transition_task_after_run`` make it idempotent and safe against a worker that
    wins the race. Returns how many tasks were transitioned."""
    from api_server.db.domain import Execution, Task, TaskStatus
    from api_server.events import publish_task_status_changed
    from sqlalchemy import select

    from workers.execution import transition_task_after_run

    cutoff = now - min_age
    async with sessionmaker() as db:
        candidate_ids = list(
            (
                await db.execute(
                    select(Task.id).where(
                        Task.status == TaskStatus.IN_PROGRESS.value,
                        Task.started_at < cutoff,
                    )
                )
            ).scalars()
        )
    reconciled = 0
    for task_id in candidate_ids:
        event: tuple[Any, str, str] | None = None
        async with sessionmaker() as db, db.begin():
            latest = (
                (
                    await db.execute(
                        select(Execution)
                        .where(Execution.task_id == task_id)
                        .order_by(Execution.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if latest is None or not _stuck_task_needs_reconcile(
                latest.status, latest.completed_at, now=now, min_age=min_age
            ):
                continue
            event = await transition_task_after_run(db, task_id, latest.status)
        if event is not None:
            task_obj, old, new = event
            await publish_task_status_changed(redis, task_obj, old_status=old, new_status=new)
            _log.info(
                "maintenance.reconcile_pipeline_state.stuck_task_reconciled",
                task_id=str(task_id),
                old_status=old,
                new_status=new,
            )
            reconciled += 1
    return reconciled


async def _reconcile_orphan_reviews(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    now: datetime,
    min_age: timedelta,
) -> int:
    """Case (b): re-announce `in_review` for AI-reviewed tasks whose review is lost.

    An `in_review` task with an AI ``reviewer_agent_id``, no `running` execution and
    no recently-finished run had its review dispatch lost (the `in_review` event
    never reached the orchestrator). Re-publishing ``task.status_changed`` with
    ``new_status=in_review`` makes ``orchestrator._on_task_in_review`` re-dispatch the
    review. Best-effort and idempotent — the orchestrator re-checks live state and
    no-ops on a stale re-announce. Returns how many tasks were re-announced."""
    from api_server.db.domain import (
        Agent,
        AgentType,
        Execution,
        ExecutionStatus,
        Task,
        TaskStatus,
    )
    from api_server.events import publish_task_status_changed
    from sqlalchemy import func, select

    cutoff = now - min_age
    async with sessionmaker() as db:
        candidates = list(
            (
                await db.execute(
                    select(
                        Task.id,
                        Task.tenant_id,
                        Task.project_id,
                        Task.reviewer_agent_id,
                    ).where(
                        Task.status == TaskStatus.IN_REVIEW.value,
                        Task.reviewer_agent_id.isnot(None),
                        Task.updated_at < cutoff,
                    )
                )
            ).all()
        )
    reannounced = 0
    for row in candidates:
        async with sessionmaker() as db:
            reviewer = await db.get(Agent, row.reviewer_agent_id)
            reviewer_is_ai = reviewer is not None and reviewer.agent_type != AgentType.HUMAN.value
            running = (
                (
                    await db.execute(
                        select(Execution.id)
                        .where(
                            Execution.task_id == row.id,
                            Execution.status == ExecutionStatus.RUNNING.value,
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            latest_completed = (
                await db.execute(
                    select(func.max(Execution.completed_at)).where(Execution.task_id == row.id)
                )
            ).scalar_one_or_none()
        if not _orphan_review_needs_reannounce(
            reviewer_is_ai=reviewer_is_ai,
            has_running_execution=running is not None,
            latest_completed_at=latest_completed,
            now=now,
            min_age=min_age,
        ):
            continue
        # A transient Task is just the value carrier the publisher reads
        # (id/tenant/project) — same pattern the dispatcher uses.
        task_ref = Task(id=row.id, tenant_id=row.tenant_id, project_id=row.project_id)
        await publish_task_status_changed(
            redis,
            task_ref,
            old_status=TaskStatus.IN_REVIEW.value,
            new_status=TaskStatus.IN_REVIEW.value,
        )
        _log.info(
            "maintenance.reconcile_pipeline_state.review_reannounced",
            task_id=str(row.id),
        )
        reannounced += 1
    return reannounced


async def _reconcile_complete_plans(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Case (c): flip `in_progress` plans whose tasks are ALL terminal to
    `pending_human_validation` AND auto-start their review-runtime.

    Mirrors ``orchestrator._on_task_done`` exactly — the SAME plan state machine
    (``transition_to_pending_human_validation``) + the SAME atomic ``WHERE
    status=in_progress`` guard — so the reconciler never diverges and can never
    double-transition a plan the live path already moved. Returns how many plans
    transitioned.

    Convergence GAP fix: the live ``done`` path auto-starts the review-runtime
    (``_on_task_done`` → ``compose_review_runtime``); when that event is LOST only
    the reconciler moves the plan, and until now it stopped at the transition —
    leaving the plan stalled in ``pending_human_validation`` with NO review_session
    (the reviewer URLs 404, human validation never arms). On a winning transition we
    now fire the SAME shared autostart (``_autostart_review_runtime``), idempotent
    and best-effort, so the two paths converge."""
    from api_server.db.domain import Plan, PlanStatus, Task
    from api_server.plan_progress import (
        TaskSnapshot,
        transition_to_pending_human_validation,
    )
    from sqlalchemy import select, update

    async with sessionmaker() as db:
        plan_rows = list(
            (
                await db.execute(
                    select(Plan.id, Plan.tenant_id).where(
                        Plan.status == PlanStatus.IN_PROGRESS.value
                    )
                )
            ).all()
        )
    transitioned = 0
    for prow in plan_rows:
        won = False
        async with sessionmaker() as db, db.begin():
            task_rows = list(
                (
                    await db.execute(
                        select(Task.id, Task.status).where(
                            Task.plan_id == prow.id,
                            Task.tenant_id == prow.tenant_id,
                        )
                    )
                ).all()
            )
            if not task_rows:
                continue
            plan = await db.get(Plan, prow.id)
            if plan is None:
                continue
            snapshots = [TaskSnapshot(id=str(r.id), status=r.status) for r in task_rows]
            result = transition_to_pending_human_validation(plan.status, snapshots)
            if not result.transitioned:
                continue
            won_id = (
                await db.execute(
                    update(Plan)
                    .where(
                        Plan.id == prow.id,
                        Plan.tenant_id == prow.tenant_id,
                        Plan.status == PlanStatus.IN_PROGRESS.value,
                    )
                    .values(status=result.new_status)
                    .returning(Plan.id)
                )
            ).scalar_one_or_none()
            if won_id is not None:
                _log.info(
                    "maintenance.reconcile_pipeline_state.plan_ready_for_review",
                    plan_id=str(prow.id),
                )
                transitioned += 1
                won = True
        # GAP fix: build + enqueue the review-runtime autostart in a SEPARATE read
        # session AFTER the transition txn commits (broker I/O must never hold a DB
        # txn open; a build/enqueue failure must never touch the committed move).
        if won:
            await _autostart_review_runtime(sessionmaker, plan_id=prow.id, tenant_id=prow.tenant_id)
    return transitioned


async def _autostart_review_runtime(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    plan_id: Any,
    tenant_id: Any,
) -> None:
    """Best-effort: build + enqueue the review-runtime autostart for a plan the
    reconciler just moved to ``pending_human_validation`` (convergence GAP fix).

    Delegates to ``api_server.review_autostart.build_review_autostart_request`` — the
    SINGLE source of truth shared with ``orchestrator._on_task_done`` — so the live
    path and the reconciler can never diverge. IDEMPOTENT: the builder returns
    ``None`` when an active (``running``/``suspended``) review session already exists
    for the plan, so a double pass (live + reconciler, or two reconciler passes) never
    spawns a second runtime. Wrapped so a bad row / a broker blip NEVER breaks the
    reconciler pass or the already-committed transition; the autostart simply retries
    on a later pass / the operator."""
    from api_server.db.domain import Plan
    from api_server.review_autostart import build_review_autostart_request

    try:
        async with sessionmaker() as db:
            plan = await db.get(Plan, plan_id)
            if plan is None:
                return
            request = await build_review_autostart_request(db, plan=plan, tenant_id=tenant_id)
        if request is None:
            return
        await asyncio.to_thread(_send_compose_review_runtime, request)
        _log.info(
            "maintenance.reconcile_pipeline_state.review_runtime_autostarted",
            plan_id=str(plan_id),
        )
    except Exception as exc:  # never break the reconciler pass / the committed move
        _log.warning(
            "maintenance.reconcile_pipeline_state.review_autostart_failed",
            plan_id=str(plan_id),
            error=str(exc),
        )


def _send_compose_review_runtime(request: dict[str, Any]) -> None:
    """Blocking broker enqueue of ``workers.compose_review_runtime`` (runs in a
    thread). Uses the worker's own Celery ``app`` to PRODUCE the task by name onto
    the ``review`` lane — the same task/queue the orchestrator autostart uses."""
    from api_server.review_autostart import COMPOSE_REVIEW_RUNTIME_TASK, REVIEW_QUEUE

    app.send_task(
        COMPOSE_REVIEW_RUNTIME_TASK,
        kwargs={"request": request},
        queue=REVIEW_QUEUE,
    )


@app.task(name="workers.reconcile_pipeline_state")  # type: ignore[misc]
def reconcile_pipeline_state() -> dict[str, Any]:
    """Convergence safety net (audit C3 / P0.6): reconcile DERIVED pipeline state
    the live event path can miss.

    Three idempotent best-effort passes (a/b/c — see the module comment). A pass
    failure is isolated and logged; it never tumbles the beat. Every 90s."""
    return asyncio.run(_reconcile_pipeline_state_async(get_settings()))


async def _reconcile_pipeline_state_async(
    settings: Settings,
    *,
    redis: Any | None = None,
    now: datetime | None = None,
    stuck_task_min_age: timedelta = _RECONCILE_STUCK_TASK_MIN_AGE,
    review_min_age: timedelta = _RECONCILE_REVIEW_MIN_AGE,
) -> dict[str, int]:
    """Async core — owns the engine + redis lifecycle. ``redis`` / ``now`` /
    thresholds are injectable so the integration test drives it deterministically.

    Each of the three passes is wrapped so an exception in one (a bad row, a broker
    blip) is logged and the others still run — best-effort, never crash beat."""
    from redis.asyncio import Redis

    moment = now or datetime.now(UTC)
    engine = create_async_engine(settings.database_url)
    own_redis = redis is None
    redis_client = redis if redis is not None else Redis.from_url(settings.events_redis_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    result: dict[str, int] = {"stuck_tasks": 0, "orphan_reviews": 0, "completed_plans": 0}
    try:
        try:
            result["stuck_tasks"] = await _reconcile_stuck_tasks(
                sessionmaker, redis_client, now=moment, min_age=stuck_task_min_age
            )
        except Exception as exc:
            _log.warning("maintenance.reconcile_pipeline_state.stuck_tasks_error", error=str(exc))
        try:
            result["orphan_reviews"] = await _reconcile_orphan_reviews(
                sessionmaker, redis_client, now=moment, min_age=review_min_age
            )
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.orphan_reviews_error", error=str(exc)
            )
        try:
            result["completed_plans"] = await _reconcile_complete_plans(sessionmaker)
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.completed_plans_error", error=str(exc)
            )
    finally:
        await engine.dispose()
        if own_redis:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    _log.info("maintenance.reconcile_pipeline_state.done", **result)
    return result
