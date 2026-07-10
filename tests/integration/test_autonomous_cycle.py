"""E2E del CICLO AUTÓNOMO completo — hallazgo #8 (A4), sobre contenedores reales.

Extiende ``test_e2e_smoke.py`` (que llega hasta el run del implementador) con la
segunda mitad del ciclo: el reviewer IA. Una tarea de un plan viaja de punta a punta

    ready → dispatch → run implementador (agent-runtime) → in_review →
    dispatch review → run reviewer (agent-runtime) → verdict → done | backlog

con las piezas de máquina de estados cableadas (orchestrator + Celery seam +
contenedor + ``_apply_review_verdict``), no mockeadas. Seams y límites DECLARADOS
(I-7, auditoría 2026-07-10):

  * Proceso worker de Celery: el test lee el mensaje que el dispatcher encoló e
    invoca ``run_execution`` con ese payload exacto — la misma llamada que haría
    un daemon.
  * El evento INICIAL (backlog→ready) se fabrica en un stream de test: en
    producción lo publica el API/beat de promoción, no el worker. En cambio el
    eslabón worker→orchestrator del REVIEW se prueba REAL: el dispatch del review
    consume de ``events:tasks`` el ``task.status_changed`` que ``run_cycle``
    publicó de verdad al soltar el run-lock (H1) — si
    ``publish_task_status_changed`` se rompiera, este test se pone rojo.
  * SIN dimensión git: el proyecto sembrado no tiene repos → no hay worktree ni
    commits con trailers, y el reviewer corre en el modo sin-workspace (ADR 0095).
    Esa dimensión tiene cobertura propia (``test_commit_trailers``,
    ``test_branch_push_*``, ``test_plan_completion``); aquí se prueba el CICLO de
    la máquina de estados.

Modelos SCRIPTED (sin LLM real): el implementador escribe+finish; el reviewer cierra
con el tag ``<verdict>approve|reject</verdict>`` (el canal de veredicto del run
reviewer, ADR 0084). Necesita el stack local: Docker + ``agent-runtime:v1`` + Postgres
+ Redis (``@requires_docker`` + ``_agent_runtime_image`` hacen skip honesto si faltan)
+ el api-server del stack dev (la runtime llama a la API interna en ``agentic-agents``).

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
# Overridable por env, como en conftest (I-7 menor: el hardcode rompía distinto
# que el resto de la suite si la CI mueve Redis).
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15")

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
    ``reviewer_agent_id`` (para que el run del implementador la pase a in_review).

    Limpia además el stream REAL ``events:tasks`` del Redis de test: el dispatch
    del review consumirá de ahí el evento que el worker publique de verdad (I-7),
    y los restos de sesiones/tests previos no deben contaminar la lectura."""
    from api_server.events import EVENTS_STREAM

    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await redis.delete(EVENTS_STREAM)
    finally:
        await redis.aclose()
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


async def _consume_and_take_job(
    db_url: str, *, events_stream: str, seed_event: dict[str, str] | None
) -> dict[str, Any]:
    """Corre el consumer/dispatcher REALES sobre ``events_stream`` una vez y devuelve
    el request de ejecución que el dispatcher encoló para el worker. Si
    ``seed_event`` viene, se publica antes (el evento inicial fabricado); con
    ``None`` se consume lo que YA haya publicado el worker (el eslabón real)."""
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        settings = OrchestratorSettings(
            redis_url=TEST_REDIS_URL,
            events_stream=events_stream,
            # Grupo fresco por llamada: ensure_group crea en id="0" (mkstream), así
            # que lee el stream desde el principio — incluidos los eventos que el
            # worker publicó ANTES de crear el grupo.
            consumer_group=f"cycle-{uuid4().hex[:8]}",
            consumer_name="cycle-1",
            block_ms=200,
        )
        celery_app = build_celery_app(WorkerSettings(broker_url=TEST_REDIS_URL))
        dispatcher = TaskDispatcher(sessionmaker=sm, celery_app=celery_app, settings=settings)
        consumer = StreamConsumer(redis, settings, dispatcher.handle)
        await consumer.ensure_group()
        await redis.delete("default")
        if seed_event is not None:
            await redis.xadd(events_stream, seed_event)
        result = await consumer.consume_once()
        assert result.processed == 1, "el orchestrator no procesó exactamente un evento"
        messages = await redis.lrange("default", 0, -1)
        await redis.delete("default")
        assert len(messages) == 1, "el dispatcher no encoló exactamente un job"
        body = json.loads(base64.b64decode(json.loads(messages[0])["body"]))
        _args, kwargs, _embed = body
        return kwargs["request"]  # type: ignore[no-any-return]
    finally:
        await redis.aclose()
        await engine.dispose()


async def _dispatch(db_url: str, ids: dict[str, UUID], *, old: str, new: str) -> dict[str, Any]:
    """Dispatch desde un ``task.status_changed`` FABRICADO en un stream de test —
    solo para el evento inicial (backlog→ready), que en producción publica el
    API/beat de promoción, no el worker."""
    stream = f"test:cycle:{uuid4().hex[:8]}"
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        request = await _consume_and_take_job(
            db_url,
            events_stream=stream,
            seed_event={
                "type": "task.status_changed",
                "tenant_id": str(ids["tenant"]),
                "project_id": str(ids["project"]),
                "task_id": str(ids["task"]),
                "occurred_at": "2026-07-09T00:00:00+00:00",
                "payload": json.dumps({"old_status": old, "new_status": new}),
            },
        )
        await redis.delete(stream)
        return request
    finally:
        await redis.aclose()


async def _dispatch_from_worker_event(db_url: str) -> dict[str, Any]:
    """Dispatch del REVIEW consumiendo el evento REAL de ``events:tasks`` (I-7):
    el ``task.status_changed`` in_progress→in_review que ``run_cycle`` publicó al
    soltar el run-lock (H1). Si ``publish_task_status_changed`` emitiera un payload
    malformado o al stream equivocado, el ``processed == 1`` de abajo se pone rojo
    — antes el test fabricaba este evento y ese fallo pasaba invisible."""
    from api_server.events import EVENTS_STREAM

    return await _consume_and_take_job(db_url, events_stream=EVENTS_STREAM, seed_event=None)


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
    # El evento in_progress→in_review NO se fabrica: se consume el que el worker
    # publicó de verdad en events:tasks al soltar el run-lock (I-7).
    review_req = asyncio.run(_dispatch_from_worker_event(admin_database_url))
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

    review_req = asyncio.run(_dispatch_from_worker_event(admin_database_url))
    assert review_req["review"] is True
    _run(review_req, admin_database_url)

    # reject → la tarea vuelve a backlog (no cierra).
    assert asyncio.run(_task_status(admin_database_url, ids["task"])) == "backlog"
