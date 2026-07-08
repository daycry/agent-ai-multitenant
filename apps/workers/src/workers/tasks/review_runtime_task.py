"""Review-runtime Celery task (Plan 06.5 Fase C/F — task_06_5_17, C8 F39/F41).

Spawnea el contenedor `main_image` de una sesión de validación humana,
persiste su fila `review_sessions`, respeta el cap por-tenant y notifica al
owner con las URLs firmadas. Sin daemon Docker la fila persiste igual (la
sesión queda «pending spawn» y las URLs funcionan; solo el app-preview es
inerte).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.docker_client import get_docker_client

_log = structlog.get_logger("workers.tasks")


@app.task(name="workers.compose_review_runtime")  # type: ignore[untyped-decorator]
def compose_review_runtime(request: dict[str, Any]) -> dict[str, Any]:
    """Spawn the review-runtime + persist its session row.

    Plan 06.5 Fase F task_06_5_17.

    Expected ``request`` shape::

        {
          "tenant_id": "<uuid>",
          "plan_id": "<uuid>",
          "repo_name": "backend",
          "worktree_host_path": "/data/wt/plan-...",
          "main_image": "backend:latest",
          "main_port": 8080,
          "expires_in_seconds": 172800,
          "human_checklist": [
            {"id": "human_01", "description": "...", "checklist": [...]},
            ...
          ]
        }

    Steps:
      1. Always persist a `review_sessions` row in status='running'
         with the request as the spec JSONB.
      2. If Docker is reachable, spawn the main_image container with
         the worktree bind-mounted at `/workspace`. Update the row
         with the container_id.
      3. If Docker is unreachable, leave container_ids empty — the
         row is still useful (the orchestrator + admin-panel can show
         "session pending spawn" + the URL the human will visit).

    Returns ``{session_id, plan_id, status, expires_at_unix,
    container_ids}``. The container log stream goes to Redis pub/sub
    channel ``review:logs:{session_id}`` (see Plan 06.5 Fase B
    WS endpoint).
    """
    settings = get_settings()
    return asyncio.run(_compose_review_runtime(request, settings))


def tenant_cap_exceeded(active_count: int, tenant_cap: int) -> bool:
    """Pure decision: would spawning ONE more review-runtime breach the tenant cap?

    The N+1-th concurrent runtime for a tenant is refused (C8 F41). With
    ``active_count`` running/suspended sessions and a cap of ``tenant_cap``, a new
    spawn is rejected once ``active_count >= tenant_cap``. Kept pure so the
    boundary is unit-testable without a DB."""
    return active_count >= tenant_cap


async def _count_active_review_sessions(session: Any, tenant_id: UUID) -> int:
    """Count a tenant's live (running/suspended, not soft-deleted) review sessions.

    The cap is per-tenant concurrency, so terminal sessions (approved/rejected/
    expired/cancelled) never count. BYPASSRLS-safe: carries an explicit tenant
    predicate."""
    from api_server.db.models import ReviewSession as ReviewSessionRow

    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(ReviewSessionRow)
                .where(
                    ReviewSessionRow.tenant_id == tenant_id,
                    ReviewSessionRow.status.in_(("running", "suspended")),
                    ReviewSessionRow.deleted_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def _compose_review_runtime(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Async core. Enforce the tenant cap, persist the row, then attempt spawn."""
    # Lazy imports — workers without `review` queue routed shouldn't
    # pay these.
    from datetime import UTC, datetime, timedelta

    from api_server.db.review_session_repo import (
        create_review_session,
    )

    from workers.review_runtime import DEFAULT_TENANT_CAP

    tenant_id = UUID(str(request["tenant_id"]))
    plan_id = UUID(str(request["plan_id"]))
    expires_in_seconds = int(request.get("expires_in_seconds", 48 * 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)

    # C8 F41: enforce the per-tenant cap on the PRODUCTION path — the in-memory
    # ReviewRuntimeManager.create cap never ran here. We refuse the N+1-th BEFORE
    # creating a row or a container (so a runaway never piles up sessions).
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            active = await _count_active_review_sessions(session, tenant_id)
        if tenant_cap_exceeded(active, DEFAULT_TENANT_CAP):
            _log.warning(
                "review_runtime.tenant_cap_exceeded",
                tenant_id=str(tenant_id),
                plan_id=str(plan_id),
                active=active,
                cap=DEFAULT_TENANT_CAP,
            )
            return {
                "plan_id": str(plan_id),
                "status": "tenant_cap_exceeded",
                "active": active,
                "cap": DEFAULT_TENANT_CAP,
            }

        # C8 F39: the orchestrator passes worktree IDENTIFIERS, not the host path
        # (only the worker owns data_root + the git libs). Resolve/provision the
        # plan-level worktree here; absent or unresolvable falls back to "" — the
        # row + signed URLs still work, only the live app-preview is inert.
        worktree_host_path = _resolve_review_worktree_host_path(request, settings)
        request = {**request, "worktree_host_path": worktree_host_path}
        # hallazgo #4 (QA 2026-07-07): sin imagen pineada por el proyecto NO se
        # lanza contenedor (el placeholder alpine:3.20 está retirado). La fila +
        # URLs firmadas siguen vivas; el proxy/SPA leen `app_configured` y
        # explican honestamente que el proyecto no tiene app-preview.
        app_configured = bool(str(request.get("main_image") or "").strip())
        spec_payload = {k: v for k, v in request.items() if k not in {"tenant_id"}}
        spec_payload["app_configured"] = app_configured

        # Persist the row first. Spawn failures are recoverable; a missing DB row
        # is not.
        async with sessionmaker() as session, session.begin():
            row = await create_review_session(
                session,
                tenant_id=tenant_id,
                plan_id=plan_id,
                spec=spec_payload,
                expires_at=expires_at,
            )
            session_id = row.id
            if app_configured:
                # Record where the api-server will reverse-proxy the app (ADR
                # 0062): the container's deterministic name on agentic-agents.
                # The proxy reads `spec.main_host`; default falls back to the
                # same name.
                row.spec = {**spec_payload, "main_host": f"agentic-review-{session_id}"}
                await session.flush()

        # Try the real spawn (no-op without a configured image).
        container_ids = (
            _spawn_review_runtime(request, str(session_id), settings) if app_configured else ()
        )

        # If we got container_ids, update the row.
        if container_ids:
            async with sessionmaker() as session, session.begin():
                refreshed = await session.get(type(row), session_id)
                if refreshed is not None:
                    refreshed.container_ids = list(container_ids)
                    await session.flush()
    finally:
        await engine.dispose()

    # C8 F39: notify the owner with the signed reviewer URLs (the worker owns the
    # session id, so URL minting lives here, not in the orchestrator). Best-effort.
    await _notify_review_ready(request, session_id, expires_at)

    result: dict[str, Any] = {
        "session_id": str(session_id),
        "plan_id": str(plan_id),
        "status": "running",
        "expires_at_unix": expires_at.timestamp(),
        "container_ids": list(container_ids) if container_ids else [],
    }
    if not app_configured:
        result["note"] = "no review app image configured — session persisted, app-preview disabled"
    elif not container_ids:
        result["note"] = "docker unavailable — session row persisted, container spawn pending"
    return result


def _resolve_review_worktree_host_path(request: dict[str, Any], settings: Settings) -> str:
    """Resolve (provisioning if needed) the plan-level worktree host path (C8 F39).

    Honours an explicit ``worktree_host_path`` when the caller already has one.
    Otherwise materialises a detached worktree on the plan branch (``plan/{id8}-
    {slug}``) from the project's bare repo — the same Plan 06 git libraries the
    per-task provisioning uses. Best-effort: any failure (missing slugs, no git,
    bare-repo error) returns ``""`` so the session still persists with an inert
    app-preview rather than failing the whole spawn."""
    explicit = request.get("worktree_host_path")
    if isinstance(explicit, str) and explicit:
        return explicit
    tenant_slug = request.get("tenant_slug")
    project_slug = request.get("project_slug")
    if not tenant_slug or not project_slug:
        return ""
    try:
        from pathlib import Path

        from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
        from workers.plan_git import make_plan_branch_name
    except ImportError:
        return ""
    try:
        layout = BareRepoLayout(
            data_root=Path(settings.data_root),
            tenant_slug=str(tenant_slug),
            project_slug=str(project_slug),
        )
        repo_name = str(request.get("repo_name") or project_slug)
        branch = make_plan_branch_name(str(request["plan_id"]), str(request.get("plan_slug") or ""))
        mgr = BareRepoManager(layout)
        mgr.ensure_repo(repo_name)
        mgr.seed_initial_commit_if_empty(repo_name)
        wt = WorktreeManager(layout, repo_name)
        key = f"review-{str(request['plan_id'])[:8]}"
        path = wt.add(key, branch=branch)
        wt.sync_to_head(key, branch=branch)
        return str(path)
    except Exception as exc:  # pragma: no cover - requires a live git tree
        _log.warning(
            "review_runtime.worktree_provision_failed",
            plan_id=str(request.get("plan_id", "")),
            error=str(exc),
        )
        return ""


async def _notify_review_ready(request: dict[str, Any], session_id: Any, expires_at: Any) -> None:
    """Fan out the ``human_validation_needed`` notification for a fresh session.

    Mints the signed reviewer URLs (SPA / app-preview / verdict) and dispatches the
    domain event so the owner + the tenant's subscribed channels learn validation is
    pending (C8 F39). Best-effort: any import / broker failure is swallowed — the
    session row + URLs are already persisted, so a notification outage never strands
    the plan (the operator can still open the review from the board)."""
    try:
        from api_server.celery_client import enqueue_event_dispatch
        from api_server.routers.review import build_review_urls
    except ImportError:  # pragma: no cover - api_server always present in workers
        return
    try:
        urls = build_review_urls(session_id, expires_at.timestamp())
    except Exception as exc:
        _log.warning(
            "review_runtime.review_urls_failed",
            plan_id=str(request.get("plan_id", "")),
            error=str(exc),
        )
        return
    event = {
        "event_type": str(request.get("notify_event") or "human_validation_needed"),
        "tenant_id": str(request["tenant_id"]),
        "context": {
            "task_title": request.get("plan_title") or "",
            "project_name": request.get("project_name") or "",
            "plan_id": str(request.get("plan_id", "")),
            "session_id": str(session_id),
            "owner_user_id": request.get("owner_user_id"),
            **urls,
        },
        "locale": None,
    }
    await enqueue_event_dispatch(event)


def _spawn_review_runtime(
    request: dict[str, Any], session_id: str, settings: Settings
) -> tuple[str, ...]:
    """Spawn the `main_image` container for one review session.

    Plan 06.5 Fase F task_06_5_17. Returns a tuple of container IDs.
    Empty tuple = Docker unreachable; the DB row still persists, and
    the admin-panel will show the session as "pending spawn".

    Spawns ONLY ``main_image`` for now; ``aux_services`` (postgres-test,
    redis-test sidecars) are intentionally deferred — most first-pass
    reviews don't need them, and each aux requires its own bridge
    network. Adding them is a body change to this helper, not a
    contract change to the celery task.

    Hardening mirrors the agent-runtime envelope: cap-drop ALL,
    no-new-privileges, read-only root, non-root uid, mem/pids capped,
    Docker socket tripwire. The worktree is bind-mounted at
    ``/workspace``.
    """
    # hallazgo #4: sin imagen configurada no hay nada que lanzar — el caller ya
    # persistió la sesión con `app_configured=false`. Guard defensivo aquí
    # también, antes de tocar el daemon.
    main_image = str(request.get("main_image") or "").strip()
    if not main_image:
        return ()

    try:
        from workers.isolation import (
            assert_no_docker_socket,
            build_hardened_run_kwargs,
        )
    except ImportError:
        return ()

    client = get_docker_client()
    if client is None:
        return ()

    worktree_host_path = str(request["worktree_host_path"])

    kwargs = build_hardened_run_kwargs(settings, workspace_host_path=worktree_host_path)
    kwargs["detach"] = True
    # Deterministic name + shared internal network so the api-server can reverse
    # -proxy the running app by name (ADR 0062). agentic-agents is internal (no
    # host egress); the app is reachable ONLY via the api-server's signed proxy.
    kwargs["name"] = f"agentic-review-{session_id}"
    kwargs["network"] = "agentic-agents"
    kwargs["labels"] = {
        "com.agentic-platform.component": "review-runtime",
        "com.agentic-platform.managed": "true",
        "com.agentic-platform.review-session-id": session_id,
        "com.agentic-platform.plan-id": str(request["plan_id"]),
        "com.agentic-platform.tenant-id": str(request["tenant_id"]),
    }

    assert_no_docker_socket(kwargs)

    try:
        container = client.containers.run(main_image, **kwargs)
    except Exception:
        # Daemon reachable but launch failed (image missing, OOM at
        # create, etc.). Surface as empty tuple — the orchestrator
        # treats this the same as "daemon unavailable" for now.
        return ()

    return (container.id,)
