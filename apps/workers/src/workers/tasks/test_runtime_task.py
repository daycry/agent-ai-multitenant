"""Test-runtime Celery task (Plan 06.5 Fase C/F — task_06_5_16).

Acepta un dict JSON-safe con los ``acceptance_criteria`` de una tarea, emite
los audit events (`test_run_started`/`test_run_completed`) y lanza cada
``RuntimePlan`` sobre el worktree. Docker-aware: sin daemon degrada a stub
(`status="docker_unavailable"`) para que el camino del orchestrator sea
testeable sin infraestructura.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.docker_client import get_docker_client

_log = structlog.get_logger("workers.tasks")


@app.task(name="workers.run_test_runtime")  # type: ignore[untyped-decorator]
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
        from workers.runtime_services import (
            RuntimeServicesConfigError,
            build_project_runtime_services,
        )
        from workers.test_runtime import (
            TestRuntimeRunner,
            TestRuntimeSpec,
            group_tasks_by_runtime,
        )
    except ImportError:
        return None

    if get_docker_client() is None:
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

    # ADR 0129: the project's declared services (+ connection env). The request
    # carries `repository_config` when the orchestrator threads it; absent →
    # empty (backward-compatible, no services).
    try:
        services = build_project_runtime_services(request.get("repository_config"))
    except RuntimeServicesConfigError:
        # A bad services config must not sink the whole test run — run without
        # them (the checks that need a DB will fail visibly, which is truthful).
        services = build_project_runtime_services(None)

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
            # ADR 0129: services on the bridge + connection env for the checks.
            aux_services=services.aux_services,
            main_env=services.main_env,
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
