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
from typing import Any

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
