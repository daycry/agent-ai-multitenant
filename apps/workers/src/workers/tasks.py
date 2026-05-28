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

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.execution import ExecutionRequest, conduct_execution


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
    """
    settings = get_settings()
    return asyncio.run(_run_execution(ExecutionRequest.from_dict(request), settings))


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
    """Enqueue + persist a test-runtime job for one task.

    Expected ``request`` shape::

        {
          "tenant_id": "<uuid>",
          "task_id": "<uuid>",
          "runtime": "python-pytest",
          "worktree_host_path": "/data/wt/<task>",
          "command": ["pytest", "-q"],
          "timeout_s": 600
        }

    Persists a `task_audit_events` row with kind=``test_run_started`` so
    the audit log shows the queue moment. The actual container spin-up
    + result event lands in a follow-up audit row from Fase F.

    Returns ``{"task_id": ..., "status": "scheduled"}`` for now. Fase F
    extends the body to invoke ``TestRuntimeRunner.launch`` and returns
    the full ``TestRuntimeResult`` shape.
    """
    settings = get_settings()
    return asyncio.run(_run_test_runtime_stub(request, settings))


async def _run_test_runtime_stub(request: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """DB-only stub. Real container spin-up is Plan 06.5 Fase F."""
    # Lazy import keeps the worker boot cheap when this task isn't routed.
    from api_server.db.task_audit_repo import append_audit_event

    tenant_id_str = str(request["tenant_id"])
    task_id_str = str(request["task_id"])

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            await append_audit_event(
                session,
                tenant_id=UUID(tenant_id_str),
                task_id=UUID(task_id_str),
                kind="test_run_started",
                actor="system:celery",
                payload={
                    "runtime": request.get("runtime"),
                    "worktree_host_path": request.get("worktree_host_path"),
                    "queued_at_unix": time.time(),
                },
            )
    finally:
        await engine.dispose()

    return {
        "task_id": task_id_str,
        "status": "scheduled",
        "note": "stub: container spawn pending Plan 06.5 Fase F",
    }


@app.task(name="workers.compose_review_runtime")  # type: ignore[misc]
def compose_review_runtime(request: dict[str, Any]) -> dict[str, Any]:
    """Enqueue + persist a review-runtime composition for one plan.

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

    Persists a `review_sessions` row in status ``running`` (the spec is
    stored as JSONB for re-hydration). Returns the row id + status.

    Fase F will replace the body with a real
    ``ReviewRuntimeManager.create(spec)`` call that spawns the
    container compose. The DB row already exists by then — Fase F just
    fills in the spawned container ids.
    """
    settings = get_settings()
    return asyncio.run(_compose_review_runtime_stub(request, settings))


async def _compose_review_runtime_stub(
    request: dict[str, Any], settings: Settings
) -> dict[str, Any]:
    """DB-only stub. Real spawn lands in Plan 06.5 Fase F."""
    # Lazy import — same reasoning as the test-runtime task.
    from datetime import UTC, datetime, timedelta

    from api_server.db.review_session_repo import (
        create_review_session,
    )

    tenant_id_str = str(request["tenant_id"])
    plan_id_str = str(request["plan_id"])
    expires_in_seconds = int(request.get("expires_in_seconds", 48 * 3600))
    expires_at = datetime.now(UTC) + timedelta(seconds=expires_in_seconds)

    # Persist the full request as the `spec` JSONB — Fase F deserialises
    # it back into a ReviewRuntimeSpec when it calls the real manager.
    spec_payload = {k: v for k, v in request.items() if k not in {"tenant_id"}}

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session, session.begin():
            row = await create_review_session(
                session,
                tenant_id=UUID(tenant_id_str),
                plan_id=UUID(plan_id_str),
                spec=spec_payload,
                expires_at=expires_at,
            )
            session_id = str(row.id)
    finally:
        await engine.dispose()

    return {
        "session_id": session_id,
        "plan_id": plan_id_str,
        "status": "running",
        "expires_at_unix": expires_at.timestamp(),
        "note": "stub: container spawn pending Plan 06.5 Fase F",
    }
