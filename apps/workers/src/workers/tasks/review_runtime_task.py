"""Review-runtime Celery task (Plan 06.5 Fase C/F — task_06_5_17, C8 F39/F41).

Spawnea el contenedor `main_image` de una sesión de validación humana,
persiste su fila `review_sessions`, respeta el cap por-tenant y notifica al
owner con las URLs firmadas. Sin daemon Docker la fila persiste igual (la
sesión queda «pending spawn» y las URLs funcionan; solo el app-preview es
inerte).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine
from workers.docker_client import get_docker_client
from workers.host_paths import HostPathError, ensure_under_data_root

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
    # ADR 0130: a PROJECT preview has no plan (plan_id absent/None); a plan
    # review or a plan preview carries one. ``kind`` discriminates them.
    plan_id = UUID(str(request["plan_id"])) if request.get("plan_id") else None
    kind = str(request.get("kind") or "plan")
    expires_in_seconds = int(request.get("expires_in_seconds", 48 * 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)

    # C8 F41: enforce the per-tenant cap on the PRODUCTION path — the in-memory
    # ReviewRuntimeManager.create cap never ran here. We refuse the N+1-th BEFORE
    # creating a row or a container (so a runaway never piles up sessions).
    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            active = await _count_active_review_sessions(session, tenant_id)
        if tenant_cap_exceeded(active, DEFAULT_TENANT_CAP):
            _log.warning(
                "review_runtime.tenant_cap_exceeded",
                tenant_id=str(tenant_id),
                plan_id=str(plan_id) if plan_id else None,
                kind=kind,
                active=active,
                cap=DEFAULT_TENANT_CAP,
            )
            return {
                "plan_id": str(plan_id) if plan_id else None,
                "status": "tenant_cap_exceeded",
                "active": active,
                "cap": DEFAULT_TENANT_CAP,
            }

        # C8 F39: the orchestrator passes worktree IDENTIFIERS, not the host path
        # (only the worker owns data_root + the git libs). Resolve/provision the
        # worktree here; absent or unresolvable falls back to "" — the row +
        # signed URLs still work, only the live app-preview is inert. ADR 0130:
        # a PROJECT preview (no plan) provisions the project's DEFAULT branch;
        # everything else (plan review + plan preview) the plan branch.
        if plan_id is None:
            worktree_host_path = _resolve_preview_worktree_host_path(request, settings)
        else:
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
                kind=kind,
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
    # ADR 0130: on-demand previews DON'T notify — there's no pending validation to
    # escalate; the operator who launched it polls for the app URL instead.
    if kind == "plan":
        await _notify_review_ready(request, session_id, expires_at)

    result: dict[str, Any] = {
        "session_id": str(session_id),
        "plan_id": str(plan_id) if plan_id else None,
        "kind": kind,
        "status": "running",
        "expires_at_unix": expires_at.timestamp(),
        "container_ids": list(container_ids) if container_ids else [],
    }
    if not app_configured:
        result["note"] = "no review app image configured — session persisted, app-preview disabled"
    elif not container_ids:
        result["note"] = "docker unavailable — session row persisted, container spawn pending"
    return result


def _sync_explicit_review_worktree(path: str, request: dict[str, Any], settings: Settings) -> None:
    """Best-effort: trae un worktree de review EXPLÍCITO a la punta de la rama
    del plan (fetch + reset --hard, **sin** ``clean``).

    Visto en vivo (plan CI4, ADR 0107): un spawn que reusa el
    ``worktree_host_path`` de un spec anterior (p.ej. el re-run del sweep, o un
    relanzamiento manual) servía el commit de la PRIMERA ronda — el validador
    veía el bug ya corregido como si las correcciones no existieran. Sin
    ``clean`` a propósito: los artefactos no trackeados (``vendor/`` de
    composer, ``node_modules/``…) deben sobrevivir — la app de preview los
    necesita y su contenedor no tiene red para reinstalarlos."""
    try:
        from pathlib import Path

        from workers.git_repos import _run_git
        from workers.plan_git import worktree_coordinates

        tenant_slug = request.get("tenant_slug")
        project_slug = request.get("project_slug")
        if not tenant_slug or not project_slug:
            return
        layout, branch = worktree_coordinates(  # coordenadas únicas (hallazgo #10a)
            data_root=settings.data_root,
            tenant_slug=str(tenant_slug),
            project_slug=str(project_slug),
            plan_id=str(request["plan_id"]),
            plan_slug=str(request.get("plan_slug") or ""),
        )
        bare = layout.bare_repo_path(str(request.get("repo_name") or project_slug))
        wt = Path(path)
        if not wt.exists() or not bare.exists():
            return
        _run_git("fetch", str(bare), branch, cwd=wt)
        _run_git("reset", "--hard", "FETCH_HEAD", cwd=wt)
        _log.info(
            "review_runtime.worktree_synced",
            plan_id=str(request.get("plan_id", "")),
            branch=branch,
        )
    except Exception as exc:  # best-effort: mejor preview desfasada que ninguna
        _log.warning(
            "review_runtime.worktree_sync_failed",
            plan_id=str(request.get("plan_id", "")),
            error=str(exc),
        )


def _resolve_review_worktree_host_path(request: dict[str, Any], settings: Settings) -> str:
    """Resolve (provisioning if needed) the plan-level worktree host path (C8 F39).

    Honours an explicit ``worktree_host_path`` when the caller already has one —
    sincronizándolo antes a la punta de la rama del plan (ver
    :func:`_sync_explicit_review_worktree`). Otherwise materialises a detached
    worktree on the plan branch (``plan/{id8}-{slug}``) from the project's bare
    repo — the same Plan 06 git libraries the per-task provisioning uses.
    Best-effort: any failure (missing slugs, no git, bare-repo error) returns
    ``""`` so the session still persists with an inert app-preview rather than
    failing the whole spawn."""
    explicit = request.get("worktree_host_path")
    if isinstance(explicit, str) and explicit:
        # `task_cv_45` (B-10): una ruta de host fuera de `data_root` no se monta.
        try:
            explicit = ensure_under_data_root(explicit, data_root=settings.data_root)
        except HostPathError as exc:
            _log.error("review_runtime.worktree_host_path_rejected", error=str(exc))
            return ""
        _sync_explicit_review_worktree(explicit, request, settings)
        return explicit
    tenant_slug = request.get("tenant_slug")
    project_slug = request.get("project_slug")
    if not tenant_slug or not project_slug:
        return ""
    try:
        from workers.git_repos import BareRepoManager, WorktreeManager
        from workers.plan_git import worktree_coordinates
    except ImportError:
        return ""
    try:
        # Mismas coordenadas que la provisión per-task (hallazgo #10a); repo_name
        # conserva el override legacy por-request (ADR 0085 = project_slug).
        layout, branch = worktree_coordinates(
            data_root=settings.data_root,
            tenant_slug=str(tenant_slug),
            project_slug=str(project_slug),
            plan_id=str(request["plan_id"]),
            plan_slug=str(request.get("plan_slug") or ""),
        )
        repo_name = str(request.get("repo_name") or project_slug)
        mgr = BareRepoManager(layout)
        mgr.ensure_repo(repo_name)
        mgr.seed_initial_commit_if_empty(repo_name)
        wt = WorktreeManager(layout, repo_name)
        # ADR 0130: a plan PREVIEW gets a DISTINCT worktree key from the plan's
        # human-validation review so the two never RW-bind-mount the same dir.
        prefix = "preview" if request.get("kind") == "preview" else "review"
        key = f"{prefix}-{str(request['plan_id'])[:8]}"
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


def _resolve_preview_worktree_host_path(request: dict[str, Any], settings: Settings) -> str:
    """Provision a worktree on the PROJECT's default branch for an on-demand
    preview (ADR 0130) — the project-level counterpart of
    :func:`_resolve_review_worktree_host_path`, which needs a plan.

    Uses ``preview_ref`` (the git_config default branch, ``main`` fallback). Best
    -effort: aligns the local default branch to ``origin`` first (so the preview
    shows the merged code, not a stale/synthetic root), seeds an empty repo, and
    materialises a detached worktree keyed by the project slug (reused +
    resynced on a later preview). Any failure returns ``""`` → the session +
    signed URLs still work, only the live app is inert."""
    tenant_slug = request.get("tenant_slug")
    project_slug = request.get("project_slug")
    if not tenant_slug or not project_slug:
        return ""
    branch = str(request.get("preview_ref") or "main")
    try:
        from pathlib import Path

        from workers.git_repos import BareRepoLayout, BareRepoManager, WorktreeManager
    except ImportError:
        return ""
    try:
        layout = BareRepoLayout(
            data_root=Path(settings.data_root),
            tenant_slug=str(tenant_slug),
            project_slug=str(project_slug),
        )
        repo_name = str(request.get("repo_name") or project_slug)
        mgr = BareRepoManager(layout)
        mgr.ensure_repo(repo_name)
        mgr.seed_initial_commit_if_empty(repo_name, default_branch=branch)
        # Bring the local default branch to origin's tip when a remote exists —
        # a diverged/empty remote is a no-op (the caller-seeded branch stands).
        with contextlib.suppress(Exception):
            mgr.align_default_branch(repo_name, branch)
        wt = WorktreeManager(layout, repo_name)
        key = f"preview-{project_slug}"[:60]
        path = wt.add(key, branch=branch)
        wt.sync_to_head(key, branch=branch)
        return str(path)
    except Exception as exc:  # pragma: no cover - requires a live git tree
        _log.warning(
            "review_runtime.preview_worktree_provision_failed",
            project_slug=str(project_slug),
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


# ADR 0129 fase 2: the review/preview bridge + aux sidecars carry these labels so
# both reapers own them — orphan_reaper reaps aux containers by
# ``review-session-id`` and the empty bridge by ``component=review-runtime``, and
# expire_review_runtimes reaps every container id recorded on the session row.
_REVIEW_COMPONENT = "review-runtime"


def _review_labels(session_id: str, request: dict[str, Any]) -> dict[str, str]:
    """Association labels shared by the review main container, its aux sidecars
    and the per-session bridge (so the reapers clean the whole set)."""
    return {
        "com.agentic-platform.component": _REVIEW_COMPONENT,
        "com.agentic-platform.managed": "true",
        "com.agentic-platform.review-session-id": session_id,
        "com.agentic-platform.plan-id": str(request.get("plan_id", "")),
        "com.agentic-platform.tenant-id": str(request.get("tenant_id", "")),
    }


def _wait_aux_healthy(container: Any, aux: Any) -> None:
    """Poll an aux sidecar's ``healthcheck_cmd`` until green or timeout.

    Mirrors ``TestRuntimeRunner._wait_healthy`` — kept local so the review
    spawn does not depend on instantiating the whole runner."""
    import time

    if aux.healthcheck_cmd is None:
        return
    cmd = list(aux.healthcheck_cmd)
    deadline = time.monotonic() + aux.healthcheck_timeout_s
    last_rc: int | None = None
    while time.monotonic() < deadline:
        result = container.exec_run(cmd)
        last_rc = getattr(result, "exit_code", None)
        if last_rc == 0:
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"review aux {aux.name!r} not healthy within {aux.healthcheck_timeout_s}s (rc={last_rc})"
    )


def _start_review_aux_services(
    client: Any,
    settings: Settings,
    session_id: str,
    request: dict[str, Any],
    services: Any,
) -> tuple[Any, list[str]]:
    """Bring up the project's declared services on a per-session internal bridge.

    A DEDICATED bridge per session (never the shared ``agentic-agents``) keeps a
    tenant's aux services unreachable from another tenant's review container —
    aliases like ``mysql``/``redis`` would otherwise collide on the shared net.
    Each sidecar uses the same hardened envelope the test-runtime uses
    (:func:`build_aux_run_kwargs`) but relabeled to the review session so the
    reapers own it. Returns ``(bridge, aux_container_ids)``; on any failure it
    tears down whatever it created and returns ``(None, [])`` so the review still
    spawns main-only (a preview without its DB beats no preview)."""
    from workers.test_runtime import build_aux_run_kwargs, ensure_runtime_image

    aux_specs = services.aux_services
    if not aux_specs:
        return None, []

    labels = _review_labels(session_id, request)
    bridge = client.networks.create(
        f"review-aux-{session_id}",
        driver="bridge",
        internal=True,
        labels=labels,
    )
    started: list[Any] = []
    try:
        for aux in aux_specs:
            run_kwargs = build_aux_run_kwargs(settings, aux, bridge.name)
            # Relabel from the test-runtime component to this review session so
            # the reapers associate + clean the sidecar with the session.
            run_kwargs["labels"] = {**labels, "com.agentic-platform.role": "aux-service"}
            # prod-11 task_digest_pin_11: los `default_image` del catálogo del ADR
            # 0129 van fijados por digest, así que se lanza la referencia canónica
            # `repo@sha256:…` y no el tag — si no, el daemon vuelve a elegir y el
            # pin queda en decoración. Un digest irresoluble levanta la excepción
            # que ya captura el `except` de abajo: aquí la degradación correcta es
            # «preview sin su base de datos», no abortar (a diferencia del
            # test-runtime, donde un sidecar impostor contaminaría un veredicto).
            container = client.containers.run(ensure_runtime_image(client, aux.image), **run_kwargs)
            started.append(container)
            _wait_aux_healthy(container, aux)
        return bridge, [c.id for c in started]
    except Exception as exc:  # best-effort: never strand the review on aux trouble
        _log.warning(
            "review_runtime.aux_start_failed",
            session_id=session_id,
            error=str(exc)[:300],
        )
        for c in started:
            with contextlib.suppress(Exception):
                c.remove(force=True)
        with contextlib.suppress(Exception):
            bridge.remove()
        return None, []


def _preview_run_kwargs(settings: Any, *, worktree_host_path: str, services: Any) -> dict[str, Any]:
    """Los kwargs del contenedor principal del preview (`task_cv_26`, B-06).

    El worktree del plan va bind-mounteado en `/workspace` en SÓLO LECTURA por
    defecto: el preview corre la app del tenant hasta 48 h y cualquier cosa que
    la app escriba —o cualquier fallo de la propia app— modificaría el código
    que el humano va a validar. Las rutas que la app necesite escribir se
    declaran en `repository_config.preview.writable_paths` y se montan como
    tmpfs encima; `preview.workspace_rw: true` es el opt-in al comportamiento
    anterior."""
    from workers.isolation import build_hardened_run_kwargs

    read_only = not services.preview_workspace_rw
    kwargs = build_hardened_run_kwargs(
        settings, workspace_host_path=worktree_host_path, workspace_read_only=read_only
    )
    if read_only and services.preview_writable_paths:
        tmpfs = dict(kwargs.get("tmpfs") or {})
        for rel in services.preview_writable_paths:
            tmpfs[f"/workspace/{rel}"] = (
                f"rw,nosuid,size={settings.container_workspace_size},uid=1000,gid=1000"
            )
        kwargs["tmpfs"] = tmpfs
    return kwargs


def _spawn_review_runtime(
    request: dict[str, Any], session_id: str, settings: Settings
) -> tuple[str, ...]:
    """Spawn the `main_image` container (+ project services) for one review session.

    Plan 06.5 Fase F task_06_5_17 / ADR 0129 fase 2. Returns a tuple of container
    IDs (main first, then any aux sidecars). Empty tuple = Docker unreachable; the
    DB row still persists, and the admin-panel shows the session as "pending spawn".

    When the project declares services in ``repository_config`` (ADR 0129) the
    worker brings them up on a per-session internal bridge, connects the main
    container to that bridge (so it resolves the services by hostname) and injects
    the derived connection env (``DATABASE_URL``/``REDIS_URL``/…) — otherwise a DB
    app can't be previewed. Invalid service config never strands the review: it
    falls back to spawning main only.

    Hardening mirrors the agent-runtime envelope: cap-drop ALL,
    no-new-privileges, read-only root, non-root uid, mem/pids capped,
    Docker socket tripwire. The worktree is bind-mounted at ``/workspace``.
    """
    # hallazgo #4: sin imagen configurada no hay nada que lanzar — el caller ya
    # persistió la sesión con `app_configured=false`. Guard defensivo aquí
    # también, antes de tocar el daemon.
    main_image = str(request.get("main_image") or "").strip()
    if not main_image:
        return ()

    try:
        from workers.isolation import assert_no_docker_socket
        from workers.runtime_services import (
            RuntimeServicesConfigError,
            build_project_runtime_services,
        )
    except ImportError:
        return ()

    client = get_docker_client()
    if client is None:
        return ()

    # ADR 0129: resolve the project's declared services (sidecars + connection
    # env). Invalid config must NOT strand the review — fall back to main only.
    try:
        services = build_project_runtime_services(
            request.get("repository_config"),
            image_registry_allowlist=tuple(get_settings().tenant_image_registry_allowlist),
        )
    except RuntimeServicesConfigError as exc:
        _log.warning(
            "review_runtime.services_config_invalid",
            session_id=session_id,
            error=str(exc)[:300],
        )
        services = build_project_runtime_services(None)

    bridge, aux_ids = _start_review_aux_services(client, settings, session_id, request, services)

    worktree_host_path = str(request["worktree_host_path"])

    kwargs = _preview_run_kwargs(settings, worktree_host_path=worktree_host_path, services=services)
    kwargs["detach"] = True
    # Deterministic name + shared internal network so the api-server can reverse
    # -proxy the running app by name (ADR 0062). agentic-agents is internal (no
    # host egress); the app is reachable ONLY via the api-server's signed proxy.
    kwargs["name"] = f"agentic-review-{session_id}"
    kwargs["network"] = "agentic-agents"
    kwargs["labels"] = _review_labels(session_id, request)
    # ADR 0129: inject the derived connection env + the project's own env so the
    # previewed app finds its DB/cache/queue. Never clobber HOME (the envelope
    # owns it and the toolchain caches hang off it).
    if services.main_env:
        env = dict(kwargs.get("environment") or {})
        for k, v in services.main_env.items():
            if k != "HOME":
                env[str(k)] = str(v)
        kwargs["environment"] = env

    assert_no_docker_socket(kwargs)

    try:
        container = client.containers.run(main_image, **kwargs)
    except Exception:
        # Daemon reachable but launch failed (image missing, OOM at
        # create, etc.). Tear down any aux we brought up so they don't leak,
        # then surface as empty tuple (treated as "daemon unavailable").
        _teardown_review_aux(client, bridge, aux_ids)
        return ()

    # Connect the main container to the per-session bridge so it reaches the aux
    # services by their hostname/alias (it stays on agentic-agents for the proxy).
    if bridge is not None:
        try:
            bridge.connect(container)
        except Exception as exc:  # pragma: no cover - defensive
            _log.warning(
                "review_runtime.bridge_connect_failed",
                session_id=session_id,
                error=str(exc)[:300],
            )

    return (container.id, *aux_ids)


def _teardown_review_aux(client: Any, bridge: Any, aux_ids: list[str]) -> None:
    """Best-effort teardown of the aux sidecars + bridge (used when the main
    spawn fails after the aux came up). The reapers are the steady-state path;
    this just avoids an obvious immediate leak."""
    for cid in aux_ids:
        with contextlib.suppress(Exception):
            client.containers.get(cid).remove(force=True)
    if bridge is not None:
        with contextlib.suppress(Exception):
            bridge.remove()
