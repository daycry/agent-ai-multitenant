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

import asyncio
import base64
import json
import os
from functools import lru_cache
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

from ._docker_helpers import BASE_IMAGE, docker_client, ensure_base_image, skip_or_fail
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


#: Reloj del sondeo a ``/healthz``. Corto a propósito: si el api-server está ahí
#: contesta en milisegundos, y si no está el fallo es de resolución (inmediato).
_HEALTHZ_TIMEOUT_S = 5


@lru_cache(maxsize=1)
def _internal_api_unreachable_reason() -> str | None:
    """``None`` si el sandbox alcanza ``/healthz`` del api-server; si no, el motivo.

    Cacheado (``lru_cache``): son cuatro tests en el mismo proceso y levantar un
    contenedor por cada uno para preguntar lo mismo no aporta nada.

    Se sondea DESDE LA RED DEL SANDBOX (``agent_network``) y contra la MISMA URL
    que el worker inyecta (``agent_internal_api_url``), porque esa es la ruta que
    importa: la red de agentes es ``internal`` y no comparte resolución con el
    host, así que un ``curl`` desde el runner no dice nada sobre ella.
    """
    settings = WorkerSettings()
    url = settings.agent_internal_api_url.rstrip("/")
    client = docker_client()
    try:
        ensure_base_image(client)
        # La red la crea el worker al lanzar; crearla aquí evita que el sondeo
        # falle por «network not found» y confunda «no existe la red» con «no
        # contesta el api-server».
        from workers.container import AgentContainerRunner

        AgentContainerRunner(settings).ensure_network()
        # Sin `sys.exit`: si `urlopen` levanta, el intérprete sale != 0 y docker-py
        # lo entrega como `ContainerError` con el stderr dentro — que es justo el
        # texto que se quiere en el mensaje.
        probe = (
            "import urllib.request\n"
            f"urllib.request.urlopen({url + '/healthz'!r}, "
            f"timeout={_HEALTHZ_TIMEOUT_S}).read()\n"
        )
        try:
            client.containers.run(
                BASE_IMAGE,
                ["python", "-c", probe],
                network=settings.agent_network,
                remove=True,
            )
        except docker.errors.ContainerError as exc:  # pragma: no cover - env-dependent
            detail = (exc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            return (
                f"el api-server no contesta en {url}/healthz desde la red "
                f"{settings.agent_network}: {detail[-1] if detail else exc}"
            )
        except docker.errors.APIError as exc:  # pragma: no cover - env-dependent
            return f"no se pudo sondear {url}/healthz desde {settings.agent_network}: {exc}"
        return None
    finally:
        client.close()


def require_internal_api_reachable() -> None:
    """Skip local / FAIL bajo CI si el sandbox no alcanza la API interna.

    **Por qué existe.** El worker mintea ``AGENTIC_INTERNAL_TOKEN`` en cuanto la
    tarea tiene agente asignado, y desde prod-01 (task_11 / sandbox-4) el runtime
    con token exige que ``/internal/agent/*`` responda: ``ensure_reachable()``
    revienta el arranque en vez de degradar en silencio. O sea que **un run con
    agente asignado necesita al api-server vivo en ``agentic-agents``**, y sin él
    la ejecución acaba ``failed``.

    Sin esta precondición eso se manifiesta como ``assert 'failed' == 'done'``
    tres capas más allá de su causa — que es exactamente lo que pasó el
    2026-08-19, la primera vez que el job de integración de CI llegó a dar
    veredicto: su stack levanta ``postgres redis vault minio`` y el api-server
    sólo existe en el overlay de aplicaciones. Aquí el fallo dice su nombre.

    Bajo CI **no salta**: ``skip_or_fail`` falla, porque allí el job SÍ levanta el
    api-server y su ausencia es una regresión del job (mismo criterio que
    :func:`require_agent_runtime_image`, finding tests-6).
    """
    reason = _internal_api_unreachable_reason()
    if reason is not None:  # pragma: no cover - env-dependent
        skip_or_fail(
            f"{reason}. Levanta el api-server del stack de aplicaciones "
            "(docker/docker-compose.manuals.yml en dev; el paso «Bring up the "
            "api-server» del job test-integration en CI)."
        )


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


#: Cuánto del ``output`` del run entra en el mensaje de fallo. Suficiente para el
#: error del runtime (una línea) sin volcar un poema entero en el informe.
_FAILURE_OUTPUT_CAP = 600


def why_the_run_failed(db_url: str, task_id: UUID, outcome: dict[str, Any]) -> str:
    """El motivo del run, para usar como MENSAJE de un assert sobre su estado.

    Va como segundo operando del ``assert`` (``assert x == y, why_the_run_failed(...)``),
    así que Python sólo lo evalúa cuando la aserción YA ha fallado: el camino feliz
    no paga la consulta.

    Existe porque ``assert 'failed' == 'done'`` no dice nada y el motivo SÍ está
    guardado —``abort_code`` y ``output`` de la fila, más el último paso del
    ``steps_log``—, sólo que nadie lo miraba. La aserción no se relaja: se le
    cuelga el diagnóstico que ya estaba en la base de datos.

    Nunca levanta: se invoca con un test ya en rojo, y un error aquí taparía el
    fallo real con un traceback del propio helper.
    """
    try:
        return asyncio.run(_run_failure_detail(db_url, task_id, outcome))
    except Exception as exc:  # pragma: no cover - diagnóstico best-effort
        return f"run no-done ({outcome}); además falló la lectura del motivo: {exc!r}"


async def _run_failure_detail(db_url: str, task_id: UUID, outcome: dict[str, Any]) -> str:
    from api_server.db.execution_repo import list_executions_for_task

    engine = create_async_engine(db_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s:
            rows = await list_executions_for_task(s, task_id)
            lines = [f"outcome={outcome}"]
            for row in rows:
                last_step = row.steps_log[-1] if row.steps_log else None
                lines.append(
                    f"  execution {row.id}: status={row.status} "
                    f"abort_code={row.abort_code} finish_status={row.finish_status} "
                    f"output={(row.output or '')[:_FAILURE_OUTPUT_CAP]!r} "
                    f"last_step={last_step!r}"
                )
            return "\n".join(lines)
    finally:
        await engine.dispose()


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
