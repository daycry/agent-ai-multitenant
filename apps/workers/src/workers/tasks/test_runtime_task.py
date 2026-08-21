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
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine
from workers.docker_client import get_docker_client

_log = structlog.get_logger("workers.tasks")

# --- despacho de la FASE DE TESTS a la cola `test` (task_wf_22, C-04) --------
#
# Presupuesto de espera. Los checks corren EN SERIE dentro del mismo contenedor
# (el `exec_run` amortiza el pre_install), así que el techo es la suma de sus
# timeouts; el margen cubre lo que no es un check: pull/arranque de la imagen,
# servicios auxiliares del proyecto (ADR 0129) y teardown.
_TEST_PHASE_DEFAULT_CHECK_TIMEOUT_S = 600
_TEST_PHASE_SPINUP_MARGIN_S = 180
# Techo duro: cien checks de 600 s no pueden dejar un slot bloqueado 16 horas.
_TEST_PHASE_MAX_WAIT_S = 3600


def test_phase_wait_budget_s(acceptance_criteria: list[Any]) -> int:
    """Cuánto esperar como mucho por la fase de tests de una tarea.

    Un ``timeout_s`` ausente o basura cae al default en vez de restar: si un
    valor corrupto produjera una espera de 0 s, la fase se cortaría antes de
    empezar y el reviewer volvería a quedarse sin ``<test-report>`` — el
    hallazgo C1/F51 otra vez, por la puerta de atrás.
    """
    if not acceptance_criteria:
        return 0
    total = 0
    for check in acceptance_criteria:
        raw = check.get("timeout_s") if isinstance(check, dict) else None
        try:
            per_check = int(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            per_check = _TEST_PHASE_DEFAULT_CHECK_TIMEOUT_S
        if per_check <= 0:
            per_check = _TEST_PHASE_DEFAULT_CHECK_TIMEOUT_S
        total += per_check
    return min(total + _TEST_PHASE_SPINUP_MARGIN_S, _TEST_PHASE_MAX_WAIT_S)


async def dispatch_test_runtime_and_wait(request: dict[str, Any]) -> dict[str, Any]:
    """Encolar la fase de tests en la cola ``test`` y esperar su resultado.

    Corría **en proceso** (``await _run_test_runtime(...)``) dentro del worker de
    la cola ``default``: el slot que un run acababa de liberar se quedaba
    orquestando Docker —levantar el runtime, los servicios auxiliares, N checks
    de hasta 600 s, teardown— con los recursos del worker equivocado, y además
    arrastraba al worker ``default`` el import del SDK de Docker y de
    ``shared_test_runtimes`` que este módulo aplaza justamente para evitarlo.
    ``stack_exec`` ya enruta a ``test`` por este motivo (ADR 0093); esta fase se
    había quedado atrás (C-04, task_wf_22).

    **Se sigue esperando, y a propósito**: el reviewer se despacha después y
    necesita encontrar un ``<test-report>`` real — sin la espera volvería la
    carrera que dejaba al reviewer a ciegas (C1/F51). Lo que cambia es DÓNDE se
    hace el trabajo, no si se espera.

    Best-effort en sentido estricto: el run YA terminó bien y la tarea ya se
    movió a review, así que un broker caído, la ausencia de worker en ``test`` o
    el vencimiento del presupuesto se registran y devuelven ``{}``. Nunca lanzan.
    """
    criteria = request.get("acceptance_criteria") or []
    if not criteria:
        return {}
    budget = test_phase_wait_budget_s(list(criteria))

    def _send_and_wait() -> dict[str, Any]:
        async_result = app.send_task("workers.run_test_runtime", args=[request], queue="test")
        result = async_result.get(timeout=budget)
        return dict(result) if isinstance(result, dict) else {}

    try:
        return await asyncio.to_thread(_send_and_wait)
    except Exception as exc:
        _log.warning(
            "workers.test_phase_dispatch_failed",
            task_id=str(request.get("task_id", "")),
            budget_s=budget,
            error_type=exc.__class__.__name__,
        )
        return {}


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

    engine = worker_engine(settings)
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
