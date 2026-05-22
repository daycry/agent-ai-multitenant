"""Celery tasks the workers execute (task_02_06).

`run_agent_container` is the worker's entry point in Plan 02 Fase B:
take a container spec off a queue, launch it through the hardened
`AgentContainerRunner`, and return a JSON-safe summary that the result
backend stores. The orchestrator (Fase A) decides *which* agent runs a
task; this task is the muscle that runs the sandbox.
"""

from __future__ import annotations

from typing import Any

from workers.celery_app import app
from workers.config import get_settings
from workers.container import AgentContainerRunner, ContainerSpec


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
