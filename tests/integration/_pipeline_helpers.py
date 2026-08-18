"""Helpers compartidos del harness de pipeline (smoke + ciclo autónomo).

M-6 (auditoría 2026-07-10): ``test_e2e_smoke.py`` y ``test_autonomous_cycle.py``
llevaban ~80 líneas duplicadas (consumer/dispatcher, seam del worker, queries de
estado) — un cambio del contrato (p. ej. el formato del body Celery) se arreglaba
en un fichero y no en el otro. Este módulo es la única copia; cada test conserva
su seed y sus asserts.

El seam del worker (``run_worker_job``) es el MISMO que jugaría un daemon Celery:
lee el request que el dispatcher encoló e invoca ``run_execution`` con ese payload
exacto. El resto (orchestrator, contenedor, DB) es real.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from uuid import UUID, uuid4

from orchestrator.config import Settings as OrchestratorSettings
from orchestrator.consumer import StreamConsumer
from orchestrator.dispatch import TaskDispatcher
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.celery_app import build_celery_app
from workers.config import Settings as WorkerSettings
from workers.config import reset_settings_cache

import docker

from ._docker_helpers import docker_client, skip_or_fail
from ._redis_url import TEST_REDIS_URL  # con credencial; ver _redis_url.py
from ._wiring import point_everything_at_the_test_redis

AGENT_RUNTIME_IMAGE = "agent-runtime:v1"
# Overridable por env, como en conftest.


def require_agent_runtime_image() -> None:
    """Skip local si ``agent-runtime:v1`` no está construido; FAIL bajo CI (el job
    de integración la construye — su ausencia allí es una regresión real, tests-6)."""
    client = docker_client()
    try:
        client.images.get(AGENT_RUNTIME_IMAGE)
    except docker.errors.ImageNotFound:  # pragma: no cover - env-dependent
        skip_or_fail(
            f"{AGENT_RUNTIME_IMAGE} not built — run: docker build -t {AGENT_RUNTIME_IMAGE} ..."
        )
    finally:
        client.close()


async def consume_and_take_job(
    db_url: str, *, events_stream: str, seed_event: dict[str, str] | None
) -> dict[str, Any]:
    """Corre el consumer/dispatcher REALES sobre ``events_stream`` una vez y devuelve
    el request de ejecución que el dispatcher encoló para el worker.

    Con ``seed_event`` se publica antes (el evento inicial fabricado — en producción
    lo emite el API/beat de promoción, no el worker); con ``None`` se consume lo que
    YA haya publicado el worker (el eslabón real, I-7). Grupo fresco por llamada:
    ``ensure_group`` crea en ``id="0"`` (mkstream), así que lee el stream desde el
    principio — incluidos los eventos publicados ANTES de crear el grupo."""
    point_everything_at_the_test_redis()
    redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        settings = OrchestratorSettings(
            redis_url=TEST_REDIS_URL,
            events_stream=events_stream,
            consumer_group=f"pipe-{uuid4().hex[:8]}",
            consumer_name="pipe-1",
            block_ms=200,
        )
        celery_app = build_celery_app(
            WorkerSettings(broker_url=TEST_REDIS_URL, result_backend=TEST_REDIS_URL)
        )
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


def status_changed_event(
    ids: dict[str, UUID], *, old: str, new: str, occurred_at: str = "2026-07-09T00:00:00+00:00"
) -> dict[str, str]:
    """Un ``task.status_changed`` fabricado (solo para el evento INICIAL del ciclo)."""
    return {
        "type": "task.status_changed",
        "tenant_id": str(ids["tenant"]),
        "project_id": str(ids["project"]),
        "task_id": str(ids["task"]),
        "occurred_at": occurred_at,
        "payload": json.dumps({"old_status": old, "new_status": new}),
    }


def run_worker_job(request: dict[str, Any], admin_database_url: str) -> dict[str, Any]:
    """Corre ``run_execution`` como lo hace Celery (evento loop propio de la tarea)."""
    from workers.tasks import run_execution

    os.environ["WORKERS_DATABASE_URL"] = admin_database_url
    os.environ["WORKERS_EVENTS_REDIS_URL"] = TEST_REDIS_URL
    # El run toca TRES Redis más allá del stream de eventos (la caché de
    # platform_settings del api-server, el broker del memorizer y el del
    # despacho de eventos). Los tres degradan en silencio contra sus defaults
    # de producción, pero cobran ~13 s + ~80 s + ~80 s de reloj por run — que es
    # lo que hacía saltar el `timeout(300)` del ciclo autónomo. Ver `_wiring.py`.
    point_everything_at_the_test_redis()
    reset_settings_cache()
    try:
        return run_execution(request)  # type: ignore[no-any-return]
    finally:
        os.environ.pop("WORKERS_DATABASE_URL", None)
        os.environ.pop("WORKERS_EVENTS_REDIS_URL", None)
        reset_settings_cache()


async def task_status(db_url: str, task_id: UUID) -> str:
    from api_server.db.domain import Task

    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            return str(
                (await s.execute(select(Task).where(Task.id == task_id))).scalar_one().status
            )
    finally:
        await engine.dispose()


async def execution_statuses(db_url: str, task_id: UUID) -> list[str]:
    from api_server.db.execution_repo import list_executions_for_task

    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            rows = await list_executions_for_task(s, task_id)
            return [str(r.status) for r in rows]
    finally:
        await engine.dispose()


async def audit_event_kinds(db_url: str, task_id: UUID) -> list[str]:
    """Los ``kind`` de los task_audit_events de la tarea (p. ej. el
    ``review_comment`` que un reject persiste para la siguiente ronda)."""
    from sqlalchemy import text as sql_text

    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            rows = await s.execute(
                sql_text("SELECT kind FROM task_audit_events WHERE task_id = :tid"),
                {"tid": str(task_id)},
            )
            return [str(r[0]) for r in rows]
    finally:
        await engine.dispose()
