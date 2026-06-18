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


@app.task(name="workers.run_execution")  # type: ignore[misc]
def run_execution(request: dict[str, Any]) -> dict[str, Any]:
    """Conduct one agent execution end to end (Plan 02 Fase G).

    The orchestrator (task_02_31) enqueues this with the execution
    request as a plain dict. The DB and Redis handles are built from
    `Settings`; the result is the JSON-safe `ExecutionOutcome` dict.

    On an unhandled failure (e.g. a tampered cross-tenant payload, or a
    DB/broker outage) the job is recorded to a dead-letter stream and the
    exception re-raised so Celery marks it failed. Agent runs are NOT
    auto-retried — re-running is expensive and side-effecting; an operator
    reprocesses from the dead-letter stream (task_06_14_04).
    """
    settings = get_settings()
    try:
        return asyncio.run(_run_execution(ExecutionRequest.from_dict(request), settings))
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


async def _run_execution(request: ExecutionRequest, settings: Settings) -> dict[str, Any]:
    """Async core of `run_execution` — owns the engine + Redis lifecycle."""
    engine = create_async_engine(settings.database_url)
    redis: Redis = Redis.from_url(settings.events_redis_url, decode_responses=True)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        outcome = await conduct_execution(
            request, settings=settings, sessionmaker=sessionmaker, redis=redis
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


async def _compose_review_runtime(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Async core. Persist row first, then attempt spawn."""
    # Lazy imports — workers without `review` queue routed shouldn't
    # pay these.
    from datetime import UTC, datetime, timedelta

    from api_server.db.review_session_repo import (
        create_review_session,
    )

    tenant_id = UUID(str(request["tenant_id"]))
    plan_id = UUID(str(request["plan_id"]))
    expires_in_seconds = int(request.get("expires_in_seconds", 48 * 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)
    spec_payload = {k: v for k, v in request.items() if k not in {"tenant_id"}}

    # 1. Persist the row first. Spawn failures are recoverable; a missing
    # DB row is not.
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
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

        # 2. Try the real spawn.
        container_ids = _spawn_review_runtime(request, str(session_id), settings)

        # 3. If we got container_ids, update the row.
        if container_ids:
            async with sessionmaker() as session, session.begin():
                refreshed = await session.get(type(row), session_id)  # type: ignore[arg-type]
                if refreshed is not None:
                    refreshed.container_ids = list(container_ids)
                    await session.flush()
    finally:
        await engine.dispose()

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
