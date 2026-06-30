"""Celery tasks the workers execute (task_02_06, task_02_31).

Two entry points:

  * `run_agent_container` — Plan 02 Fase B: launch one agent-runtime
    container from a raw `ContainerSpec` and return its result.
  * `run_execution` — Plan 02 Fase G: conduct a full agent execution
    for a task. This is what the orchestrator's dispatcher enqueues
    (task_02_31); the heavy lifting lives in `workers.execution`.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.execution import ExecutionRequest, conduct_execution

_log = structlog.get_logger("workers.tasks")

# Failed `run_execution` jobs land here for operator visibility / manual
# reprocessing — we deliberately do NOT auto-retry agent runs (each retry
# is a full, costly LLM run with side effects). Plan 06.14 task_06_14_04.
_DEAD_LETTER_STREAM = "dlq:executions"


@app.task(name="workers.run_agent_container")  # type: ignore[misc]
def run_agent_container(
    image: str | None = None,
    command: list[str] | None = None,
    env: dict[str, str] | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """Launch one agent-runtime container and return its result.

    `image` defaults to the configured agent-runtime image. The result
    is the JSON-safe dict from `ContainerResult.as_dict()`.
    """
    settings = get_settings()
    runner = AgentContainerRunner(settings)
    spec = ContainerSpec(
        image=image or settings.agent_runtime_image,
        command=command,
        env=env or {},
        workspace_host_path=workspace,
    )
    return runner.run(spec).as_dict()


@app.task(bind=True, name="workers.run_execution")  # type: ignore[misc]
def run_execution(self: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Conduct one agent execution end to end (Plan 02 Fase G).

    The orchestrator (task_02_31) enqueues this with the execution
    request as a plain dict. The DB and Redis handles are built from
    `Settings`; the result is the JSON-safe `ExecutionOutcome` dict.

    Bound (``bind=True``) so we can persist ``self.request.id`` — the Celery job
    id — onto the `executions` row (prod-06 cancel_01). Without it the operator
    cancel endpoint's `revoke` branch was dead code (the column stayed NULL).

    On an unhandled failure (e.g. a tampered cross-tenant payload, or a
    DB/broker outage) the job is recorded to a dead-letter stream and the
    exception re-raised so Celery marks it failed. Agent runs are NOT
    auto-retried — re-running is expensive and side-effecting; an operator
    reprocesses from the dead-letter stream (task_06_14_04).
    """
    settings = get_settings()
    celery_task_id = getattr(self.request, "id", None)
    try:
        return asyncio.run(
            _run_execution(
                ExecutionRequest.from_dict(request),
                settings,
                celery_task_id=celery_task_id,
            )
        )
    except Exception as exc:
        _record_execution_dead_letter(settings, request, exc)
        raise


def _record_execution_dead_letter(
    settings: Settings, request: dict[str, Any], exc: Exception
) -> None:
    """Best-effort: push a failed run_execution onto the dead-letter stream.
    Never masks the original error (a DLQ outage just logs a warning)."""
    try:
        asyncio.run(_push_execution_dead_letter(settings, request, exc))
    except Exception as dlq_exc:  # pragma: no cover - DLQ is best-effort
        _log.warning(
            "workers.dead_letter_record_failed",
            task_id=str(request.get("task_id", "")),
            error=str(dlq_exc),
        )


async def _push_execution_dead_letter(
    settings: Settings, request: dict[str, Any], exc: Exception
) -> None:
    redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
    try:
        await redis.xadd(
            _DEAD_LETTER_STREAM,
            {
                "task": "workers.run_execution",
                "tenant_id": str(request.get("tenant_id", "")),
                "task_id": str(request.get("task_id", "")),
                "error": f"{type(exc).__name__}: {exc}",
                "failed_at_unix": str(time.time()),
            },
            maxlen=10_000,
            approximate=True,
        )
    finally:
        await redis.aclose()


async def _run_execution(
    request: ExecutionRequest, settings: Settings, *, celery_task_id: str | None = None
) -> dict[str, Any]:
    """Async core of `run_execution` — owns the engine + Redis lifecycle."""
    engine = create_async_engine(settings.database_url)
    redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        outcome = await conduct_execution(
            request,
            settings=settings,
            sessionmaker=sessionmaker,
            redis=redis,
            celery_task_id=celery_task_id,
        )
        return outcome.as_dict()
    finally:
        await redis.aclose()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Plan 06.5 Fase C — test-runtime + review-runtime celery tasks
# ---------------------------------------------------------------------------
#
# Two thin wrappers that move Plan 06's in-memory orchestration into
# Celery jobs. They DO NOT spin up real containers yet — that comes in
# Plan 06.5 Fase F (task_06_5_16 / 06_5_17). Today they:
#
#   1. Accept a JSON-safe dict describing the work.
#   2. Persist an audit event / review_session row via the api-server
#      repos so the admin-panel and the orchestrator can observe state.
#   3. Return a JSON-safe outcome dict.
#
# Fase F will replace the inner stub with the real `launch()` /
# `create()` invocations against `docker.from_env()`. The task names
# (`workers.run_test_runtime`, `workers.compose_review_runtime`) and
# their JSON contracts stay the same — switching from stub to real is
# a body change, not a wire-protocol change.


@app.task(name="workers.run_test_runtime")  # type: ignore[misc]
def run_test_runtime(request: dict[str, Any]) -> dict[str, Any]:
    """Run the test-runtime for one task (Plan 06.5 Fase F task_06_5_16).

    Expected ``request`` shape::

        {
          "tenant_id": "<uuid>",
          "task_id": "<uuid>",
          "acceptance_criteria": [{
              "id": "auto_01_a",
              "runtime": "python-pytest",
              "command": "pytest -q",
              "expected_signal": "exit_code == 0",
              "timeout_s": 600
          }, ...],
          "worktree_host_path": "/data/wt/<task>",
          "dep_cache_host_path": "/data/dep-cache",  // optional
          "aux_services": [...],                     // optional
          "cpu": 2.0, "memory_mb": 4096,             // optional overrides
        }

    Audit events emitted:
      1. ``test_run_started`` at queue time — captures runtime + paths.
      2. ``test_run_completed`` after each `RuntimePlan` finishes —
         carries exit_codes, container_id, network_name, timed_out.

    Returns a dict with the per-runtime outcomes.

    Docker-aware: if `docker.from_env()` fails (e.g. CI without a
    daemon, or running under a sandbox), the task falls back to a
    stub that emits only the ``test_run_started`` event and returns
    `status="docker_unavailable"`. That keeps the orchestrator path
    testable without infrastructure.
    """
    settings = get_settings()
    return asyncio.run(_run_test_runtime(request, settings))


async def _run_test_runtime(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Async core. Audit event always; real launch when Docker is up."""
    # Lazy imports — workers without the `test` queue routed shouldn't
    # pay the cost of importing docker SDK / shared_test_runtimes.
    from api_server.db.task_audit_repo import append_audit_event

    tenant_id = UUID(str(request["tenant_id"]))
    task_id = UUID(str(request["task_id"]))

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

        # 1. Always emit the "started" event so audit shows the queue moment.
        async with sessionmaker() as session, session.begin():
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                task_id=task_id,
                kind="test_run_started",
                actor="system:celery",
                payload={
                    "runtime": request.get("runtime"),
                    "worktree_host_path": request.get("worktree_host_path"),
                    "queued_at_unix": time.time(),
                },
            )

        # 2. Try the real launch. Failure to reach Docker → stub fallback.
        outcomes = await _launch_test_runtime_plans(request, settings)
        if outcomes is None:
            return {
                "task_id": str(task_id),
                "status": "docker_unavailable",
                "note": "docker SDK could not connect — fell back to stub",
            }

        # 3. Persist one "completed" event per runtime plan run.
        async with sessionmaker() as session, session.begin():
            for outcome in outcomes:
                await append_audit_event(
                    session,
                    tenant_id=tenant_id,
                    task_id=task_id,
                    kind="test_run_completed",
                    actor="system:celery",
                    payload=outcome,
                )
    finally:
        await engine.dispose()

    all_passed = all(o.get("all_passed", False) for o in outcomes)
    return {
        "task_id": str(task_id),
        "status": "completed",
        "all_passed": all_passed,
        "runtimes": outcomes,
    }


async def _launch_test_runtime_plans(
    request: dict[str, Any], settings: Settings
) -> list[dict[str, Any]] | None:
    """Build RuntimePlans from `acceptance_criteria` and launch each.

    Returns a JSON-safe list of per-plan outcomes, or None when Docker
    is unreachable (the caller emits a stub fallback in that case).
    """
    try:
        import docker
        from workers.test_runtime import (
            TestRuntimeRunner,
            TestRuntimeSpec,
            group_tasks_by_runtime,
        )
    except ImportError:
        return None

    try:
        docker.from_env().ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
        return None

    acceptance = request.get("acceptance_criteria") or []
    if not acceptance:
        return []

    try:
        plans = group_tasks_by_runtime(acceptance)
    except KeyError:
        # Unknown runtime id — surface as zero outcomes; the orchestrator
        # is responsible for surfacing the bad config to the user.
        return []

    runner = TestRuntimeRunner(settings)
    outcomes: list[dict[str, Any]] = []
    for plan in plans:
        spec = TestRuntimeSpec(
            plan=plan,
            worktree_host_path=str(request["worktree_host_path"]),
            dep_cache_host_path=request.get("dep_cache_host_path"),
            # ADR 0094: cold-cache default_pre_install needs to resolve its
            # registries; the runner drops the proxy before the check phase so
            # the tests themselves still run offline.
            dep_egress=True,
        )
        result = runner.launch(spec)
        outcomes.append(
            {
                "runtime": result.runtime,
                "exit_codes": list(result.exit_codes),
                "all_passed": result.all_passed(),
                "container_id": result.container_id,
                "network_name": result.network_name,
                "timed_out": result.timed_out,
                # Truncate logs to keep the JSONB payload reasonable —
                # full logs are available via `docker logs <container_id>`
                # if needed (until cleanup).
                "logs_tail": result.logs[-4000:] if result.logs else "",
            }
        )
    return outcomes


# ---------------------------------------------------------------------------
# ADR 0093 — stack_exec: the agent asks the worker (which has Docker) to run a
# stack command (composer install / vendor/bin/phpunit / php spark) in the
# project's runtime template, over the task's worktree. The agent-runtime cannot
# launch containers (no socket, principle 2) — it POSTs to /internal/agent/run-stack
# which enqueues THIS task.
# ---------------------------------------------------------------------------
_STACK_EXEC_DEFAULT_TIMEOUT_S = 600


def _stack_command_allowed(command: str, allowed: list[str]) -> str | None:
    """Deny-by-default gate (ADR 0045), identical to ``shell_exec``: the first
    token's basename must be in ``allowed``. Returns an error string, or ``None``
    when the command is allowed. An empty allowlist denies everything."""
    import shlex
    from pathlib import Path

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return f"could not parse command: {exc}"
    if not argv:
        return "empty command"
    allowed_set = set(allowed)
    program = Path(argv[0]).name
    # Accept either the basename (`php`, `composer`) or the full relative token
    # (`vendor/bin/phpunit`) — the project commands UI offers both shapes.
    if program not in allowed_set and argv[0] not in allowed_set:
        return f"command not allowed: {program}"
    return None


def _resolve_stack_dep_cache(template: Any, worktree_host_path: str, data_root: str) -> str | None:
    """Resolve the warm dep-cache host path for a stack command, or None.

    Best-effort (ADR 0045/0093): a missing/cold lock file or cache layout must
    never block the command — the install just runs cold (and resolves its
    registries via the proxy, ADR 0094)."""
    from pathlib import Path

    from shared_test_runtimes.dep_cache import DepCacheManager, compute_lock_hash

    try:
        lock = compute_lock_hash(Path(worktree_host_path), template.id)
        if not lock.hash:
            return None
        entry = DepCacheManager(Path(data_root) / "dep-cache").mount_for(template, lock.hash)
        return str(entry.host_path) if entry is not None else None
    except Exception:  # pragma: no cover - dep-cache is a best-effort optimisation
        return None


@app.task(name="workers.run_stack_command")  # type: ignore[misc]
def run_stack_command(request: dict[str, Any]) -> dict[str, Any]:
    """Run one stack command for a task in its runtime template (ADR 0093).

    ``request``: ``{tenant_id, task_id, command, timeout_s?}``. Returns
    ``{exit_code, logs, timed_out}``. The command is gated by the project's
    ``allowed_commands`` (deny-by-default) BEFORE it runs.
    """
    settings = get_settings()
    return asyncio.run(_run_stack_command(request, settings))


async def _run_stack_command(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Async core: resolve task→project (slug/runtime/allowlist) + the existing
    worktree path, gate the command against the allowlist, run it in the stack
    runtime over the worktree (RW), return rc+logs."""
    from pathlib import Path

    from api_server.db.domain import Project, Task
    from api_server.db.models import Organization
    from sqlalchemy import select

    tenant_id = UUID(str(request["tenant_id"]))
    task_id = UUID(str(request["task_id"]))
    command = str(request.get("command") or "")
    timeout_s = int(request.get("timeout_s") or _STACK_EXEC_DEFAULT_TIMEOUT_S)

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            task = (
                await session.execute(
                    select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if task is None:
                return {"exit_code": -1, "logs": "task not found", "timed_out": False}
            project = (
                await session.execute(select(Project).where(Project.id == task.project_id))
            ).scalar_one_or_none()
            org = await session.get(Organization, tenant_id)
            if project is None or org is None or not project.slug or not org.slug:
                return {"exit_code": -1, "logs": "project/org not resolvable", "timed_out": False}
            allowed = [str(c) for c in (project.allowed_commands or [])]
            runtime_id = project.default_runtime_template
            org_slug, project_slug = org.slug, project.slug
    finally:
        await engine.dispose()

    deny = _stack_command_allowed(command, allowed)
    if deny is not None:
        return {"exit_code": -1, "logs": deny, "timed_out": False, "allowed": sorted(allowed)}

    try:
        import docker
        from workers.git_repos import BareRepoLayout
        from workers.test_runtime import (
            RuntimePlan,
            TestRuntimeRunner,
            TestRuntimeSpec,
            resolve_run_runtime,
        )
    except ImportError:
        return {"exit_code": -1, "logs": "docker/runtime libs unavailable", "timed_out": False}
    try:
        docker.from_env().ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
        return {"exit_code": -1, "logs": "docker daemon unavailable", "timed_out": False}

    template = resolve_run_runtime(project_default_runtime=runtime_id, tool_default_runtime=None)
    layout = BareRepoLayout(
        data_root=Path(settings.data_root), tenant_slug=org_slug, project_slug=project_slug
    )
    worktree_host_path = str(layout.worktree_path(str(task_id)))
    dep_cache_host_path = _resolve_stack_dep_cache(template, worktree_host_path, settings.data_root)

    spec = TestRuntimeSpec(
        plan=RuntimePlan(template=template, checks=()),
        worktree_host_path=worktree_host_path,
        dep_cache_host_path=dep_cache_host_path,
        # ADR 0094: stack_exec IS the install (composer install / npm ci / …) —
        # it needs proxied egress to the registries for the whole command.
        dep_egress=True,
    )
    # Audit: a stack_exec launch with registry egress (prod-12 requirement).
    _log.info(
        "stack_exec_egress",
        tenant_id=str(tenant_id),
        task_id=str(task_id),
        runtime=template.id,
        command=command[:120],
    )
    runner = TestRuntimeRunner(settings)
    rc, logs = await asyncio.to_thread(runner.run_command, spec, command, timeout_s=timeout_s)
    return {"exit_code": rc, "logs": logs[-8000:], "timed_out": rc == 124}


@app.task(name="workers.compose_review_runtime")  # type: ignore[misc]
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
        spec_payload = {k: v for k, v in request.items() if k not in {"tenant_id"}}

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
            # Record where the api-server will reverse-proxy the app (ADR 0062):
            # the container's deterministic name on agentic-agents. The proxy
            # reads `spec.main_host`; default falls back to the same name.
            spec_with_host = {**spec_payload, "main_host": f"agentic-review-{session_id}"}
            row.spec = spec_with_host
            await session.flush()

        # Try the real spawn.
        container_ids = _spawn_review_runtime(request, str(session_id), settings)

        # If we got container_ids, update the row.
        if container_ids:
            async with sessionmaker() as session, session.begin():
                refreshed = await session.get(type(row), session_id)  # type: ignore[arg-type]
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
    if not container_ids:
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
    try:
        import docker
        from workers.isolation import (
            assert_no_docker_socket,
            build_hardened_run_kwargs,
        )
    except ImportError:
        return ()

    try:
        client = docker.from_env()
        client.ping()
    except Exception:  # docker.errors.DockerException — daemon unavailable
        return ()

    main_image = str(request["main_image"])
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
