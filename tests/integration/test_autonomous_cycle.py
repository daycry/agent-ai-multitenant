"""E2E del CICLO AUTÓNOMO completo — hallazgo #8 (A4), sobre contenedores reales.

Extiende ``test_e2e_smoke.py`` (que llega hasta el run del implementador) con la
segunda mitad del ciclo: el reviewer IA. Una tarea de un plan viaja de punta a punta

    ready → dispatch → run implementador (agent-runtime) → in_review →
    dispatch review → run reviewer (agent-runtime) → verdict → done | backlog

con TODAS las piezas cableadas (orchestrator + Celery seam + contenedor +
``_apply_review_verdict``), no mockeadas. El único seam que el test juega es el
proceso worker de Celery: lee el mensaje que el dispatcher encoló e invoca
``run_execution`` con ese payload exacto — la misma llamada que haría un daemon.

Modelos SCRIPTED (sin LLM real): el implementador escribe+finish; el reviewer cierra
con el tag ``<verdict>approve|reject</verdict>`` (el canal de veredicto del run
reviewer, ADR 0084). Necesita el stack local: Docker + ``agent-runtime:v1`` + Postgres
+ Redis (``@requires_docker`` + ``_agent_runtime_image`` hacen skip honesto si faltan).

NOTA de ubicación: vive en ``tests/integration/`` (no ``tests/e2e/``) porque reutiliza
el harness de integración probado del smoke (PG efímero + imagen agent-runtime +
``run_execution`` directo); ``tests/e2e/`` es para el e2e de instalación (installed_stack).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from api_server.db.domain import Agent, ExecutionStatus, Plan, Project, Task
from api_server.db.execution_repo import list_executions_for_task
from api_server.db.models import Organization
from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.consumer import StreamConsumer
from orchestrator.dispatch import TaskDispatcher
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings
from workers.config import reset_settings_cache

import docker

from ._docker_helpers import docker_client, requires_docker, skip_or_fail

pytestmark = pytest.mark.integration

_IMAGE = "agent-runtime:v1"
TEST_REDIS_URL = "redis://localhost:6379/15"

# Implementador: una acción + finish (un loop completo con tool call).
_IMPLEMENTER_MODEL = {
    "kind": "scripted",
    "decisions": [
        {"kind": "act", "tool": "echo", "tool_args": {"text": "draft"}},
        {"kind": "finish", "output": "implementé el endpoint"},
    ],
}


def _reviewer_model(verdict_tag: str) -> dict[str, Any]:
    """Reviewer scripted que cierra con el tag de veredicto del canal run-reviewer."""
    return {
        "kind": "scripted",
        "decisions": [{"kind": "finish", "output": f"Revisado. {verdict_tag}"}],
    }


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def _agent_runtime_image() -> None:
    """Skip local si ``agent-runtime:v1`` no está construido; FAIL bajo CI (el job de
    integración la construye, así que su ausencia allí es una regresión real)."""
    client = docker_client()
    try:
        client.images.get(_IMAGE)
    except docker.errors.ImageNotFound:  # pragma: no cover - env-dependent
        skip_or_fail(f"{_IMAGE} not built — run: docker build -t {_IMAGE} ...")
    finally:
        client.close()


async def _seed(db_url: str, *, verdict_tag: str) -> dict[str, UUID]:
    """Tenant + project + plan + implementador + reviewer + tarea ready con
    ``reviewer_agent_id`` (para que el run del implementador la pase a in_review)."""
    ids = {
        "tenant": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
        "implementer": uuid4(),
        "reviewer": uuid4(),
        "task": uuid4(),
    }
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(
                text(
                    "TRUNCATE approval_requests, executions, task_dependencies, tasks,"
                    " plans, agents, projects, organizations RESTART IDENTITY CASCADE"
                )
            )
            s.add(Organization(id=ids["tenant"], name="E2E cycle", slug="e2e-cycle"))
            await s.flush()
            s.add(
                Project(
                    id=ids["project"],
                    tenant_id=ids["tenant"],
                    name="E2E cycle project",
                    status="active",
                    is_template=False,
                    worker_config={"assignment_policy": "load_balanced"},
                )
            )
            await s.flush()
            s.add(
                Plan(
                    id=ids["plan"],
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    title="Ciclo autónomo",
                    slug="ciclo-autonomo",
                    status="in_progress",
                )
            )
            s.add(
                Agent(
                    id=ids["implementer"],
                    tenant_id=ids["tenant"],
                    name="Impl",
                    role="backend-dev",
                    system_prompt="Implementas.",
                    agent_type="ai",
                    scope="project_local",
                    project_id=ids["project"],
                    model_config=_IMPLEMENTER_MODEL,
                )
            )
            s.add(
                Agent(
                    id=ids["reviewer"],
                    tenant_id=ids["tenant"],
                    name="Rev",
                    role="reviewer",
                    system_prompt="Revisas.",
                    agent_type="ai",
                    scope="project_local",
                    project_id=ids["project"],
                    model_config=_reviewer_model(verdict_tag),
                )
            )
            await s.flush()
            s.add(
                Task(
                    id=ids["task"],
                    tenant_id=ids["tenant"],
                    project_id=ids["project"],
                    plan_id=ids["plan"],
                    title="Endpoint público v1",
                    description="ciclo autónomo e2e",
                    status="ready",
                    priority="medium",
                    assigned_agent_id=ids["implementer"],
                    reviewer_agent_id=ids["reviewer"],
                    acceptance_criteria=["el endpoint responde 200"],
                )
            )
        return ids
    finally:
        await engine.dispose()


async def _dispatch(db_url: str, ids: dict[str, UUID], *, old: str, new: str) -> dict[str, Any]:
    """Publica un ``task.status_changed`` (old→new), corre el consumer una vez y
    devuelve el request de ejecución que el dispatcher encoló para el worker."""
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    engine = create_async_engine(db_url)
    stream = f"test:cycle:{uuid4().hex[:8]}"
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        settings = OrchestratorSettings(
            redis_url=TEST_REDIS_URL,
            events_stream=stream,
            consumer_group="cycle",
            consumer_name="cycle-1",
            block_ms=200,
        )
        celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
        dispatcher = TaskDispatcher(sessionmaker=sm, celery_app=celery_app, settings=settings)
        consumer = StreamConsumer(redis, settings, dispatcher.handle)
        await consumer.ensure_group()
        await redis.delete("default")
        await redis.xadd(
            stream,
            {
                "type": "task.status_changed",
                "tenant_id": str(ids["tenant"]),
                "project_id": str(ids["project"]),
                "task_id": str(ids["task"]),
                "occurred_at": "2026-07-09T00:00:00+00:00",
                "payload": json.dumps({"old_status": old, "new_status": new}),
            },
        )
        result = await consumer.consume_once()
        assert result.processed == 1, "el orchestrator no procesó el evento"
        messages = await redis.lrange("default", 0, -1)
        await redis.delete("default")
        await redis.delete(stream)
        assert len(messages) == 1, "el dispatcher no encoló exactamente un job"
        body = json.loads(base64.b64decode(json.loads(messages[0])["body"]))
        _args, kwargs, _embed = body
        return kwargs["request"]  # type: ignore[no-any-return]
    finally:
        await redis.aclose()
        await engine.dispose()


async def _task_status(db_url: str, task_id: UUID) -> str:
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            return (await s.execute(select(Task).where(Task.id == task_id))).scalar_one().status
    finally:
        await engine.dispose()


async def _execution_statuses(db_url: str, task_id: UUID) -> list[str]:
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            rows = await list_executions_for_task(s, task_id)
            return [r.status for r in rows]
    finally:
        await engine.dispose()


def _run(request: dict[str, Any], admin_database_url: str) -> dict[str, Any]:
    """Corre ``run_execution`` como lo hace Celery (evento loop propio de la tarea)."""
    from workers.tasks import run_execution

    os.environ["WORKERS_DATABASE_URL"] = admin_database_url
    os.environ["WORKERS_EVENTS_REDIS_URL"] = TEST_REDIS_URL
    reset_settings_cache()
    try:
        return run_execution(request)
    finally:
        os.environ.pop("WORKERS_DATABASE_URL", None)
        os.environ.pop("WORKERS_EVENTS_REDIS_URL", None)
        reset_settings_cache()


@requires_docker
def test_plan_task_travels_dispatch_run_review_to_done(
    _migrated: None, _agent_runtime_image: None, admin_database_url: str
) -> None:
    """El ciclo feliz completo: implementador → in_review → reviewer approve → done."""
    ids = asyncio.run(_seed(admin_database_url, verdict_tag="<verdict>approve</verdict>"))

    # --- 1) implementador: ready → dispatch → run → in_review ---------------
    impl_req = asyncio.run(_dispatch(admin_database_url, ids, old="backlog", new="ready"))
    assert impl_req["agent_id"] == str(ids["implementer"])
    assert impl_req.get("review") in (False, None)
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "in_progress"

    impl_outcome = _run(impl_req, admin_database_url)
    assert impl_outcome["status"] == ExecutionStatus.DONE
    # Con reviewer adjunto, el run del implementador deja la tarea en in_review.
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "in_review"

    # --- 2) reviewer: in_review → dispatch review → run → done --------------
    review_req = asyncio.run(_dispatch(admin_database_url, ids, old="in_progress", new="in_review"))
    assert review_req["review"] is True
    assert review_req["agent_id"] == str(ids["reviewer"])
    assert "el endpoint responde 200" in review_req["review_context"]["acceptance_criteria"]

    _run(review_req, admin_database_url)

    # El verdict approve cierra la tarea; hay 2 ejecuciones (impl + review) done.
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "done"
    statuses = asyncio.run(_execution_statuses(admin_database_url, ids["task"]))
    assert statuses.count(ExecutionStatus.DONE) == 2


@requires_docker
def test_reviewer_reject_sends_task_back_to_backlog(
    _migrated: None, _agent_runtime_image: None, admin_database_url: str
) -> None:
    """El reviewer rechaza (``<verdict>reject</verdict>``) → la tarea vuelve a
    backlog para otra ronda del implementador (ADR 0084/0096)."""
    ids = asyncio.run(_seed(admin_database_url, verdict_tag="<verdict>reject</verdict>"))

    impl_req = asyncio.run(_dispatch(admin_database_url, ids, old="backlog", new="ready"))
    assert _run(impl_req, admin_database_url)["status"] == ExecutionStatus.DONE
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "in_review"

    review_req = asyncio.run(_dispatch(admin_database_url, ids, old="in_progress", new="in_review"))
    assert review_req["review"] is True
    _run(review_req, admin_database_url)

    # reject → la tarea vuelve a backlog (no cierra).
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "backlog"
