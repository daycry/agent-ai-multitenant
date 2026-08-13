"""`/ws/kanban` lee un stream POR PROYECTO, no el global (task_prod13_19, perf-5).

El hallazgo: cada socket del tablero abría un `XREAD` sobre `events:tasks`, el
stream ÚNICO de toda la plataforma, y descartaba en Python lo que no fuera de su
proyecto. Con N proyectos activos, cada socket recibe por la red N veces los
eventos que le importan — el coste crece con la actividad AJENA, que es la clase
de curva que no se ve en desarrollo y duele en producción.

El arreglo tiene dos mitades y las dos se fijan aquí:

1. **Dual-write**: el publicador escribe en el stream global (que consume el
   orchestrator con un grupo de consumidores) Y en `events:tasks:{project_id}`.
   Quitar el global rompería el despacho de tareas, así que el test lo exige.
2. **El socket lee el de su proyecto**: sin el dual-write, cambiar el consumidor
   dejaría el tablero mudo; sin cambiar el consumidor, el dual-write sería un
   stream que nadie lee. Por eso hay un test de cada mitad y uno que las cruza.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi.testclient import TestClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed mínimo: un tenant, un usuario miembro y dos proyectos suyos.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "user": uuid4(),
        "project": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE projects, user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3)",
            ids["tenant"],
            "Kanban Tenant",
            "kanban-tenant-prod13",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3)",
            ids["user"],
            "kanban@prod13.test",
            "argon2-placeholder",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES ($1,$2,$3,$4)",
            uuid4(),
            ids["tenant"],
            ids["user"],
            "admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, slug, status) VALUES ($1,$2,$3,$4,$5)",
            ids["project"],
            ids["tenant"],
            "Tablero",
            "tablero-prod13",
            "active",
        )
    finally:
        await conn.close()
    return ids


@pytest.fixture()
def ws_client(configured_app, test_redis_url: str) -> Iterator[TestClient]:
    from api_server.auth.deps import get_redis
    from redis.asyncio import Redis

    configured_app.dependency_overrides[get_redis] = lambda: Redis.from_url(
        test_redis_url, decode_responses=True
    )
    try:
        yield TestClient(configured_app)
    finally:
        configured_app.dependency_overrides.clear()


def _mint(user_id: UUID, tenant_id: UUID) -> str:
    async def _create() -> UUID:
        from api_server.auth.sessions import SessionStore
        from redis.asyncio import Redis

        from tests.integration.conftest import TEST_REDIS_URL

        sid = uuid7()
        redis: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
        try:
            await SessionStore(redis).create(
                sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
            )
        finally:
            await redis.aclose()
        return sid

    from api_server.auth.jwt import encode_jwt

    sid = asyncio.run(_create())
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _event_fields(tenant_id: UUID, project_id: UUID, task_id: UUID) -> dict[str, str]:
    return {
        "type": "task.status_changed",
        "tenant_id": str(tenant_id),
        "project_id": str(project_id),
        "task_id": str(task_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "payload": json.dumps({"old_status": "ready", "new_status": "in_progress"}),
    }


async def _xadd(url: str, stream: str, fields: dict[str, str]) -> None:
    from redis.asyncio import Redis

    redis: Redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.xadd(stream, fields)
    finally:
        await redis.aclose()


async def _clear(url: str, *streams: str) -> None:
    from redis.asyncio import Redis

    redis: Redis = Redis.from_url(url, decode_responses=True)
    try:
        await redis.delete(*streams)
    finally:
        await redis.aclose()


# ===========================================================================
# Mitad 1 — el publicador escribe en los DOS streams
# ===========================================================================
def test_publisher_dual_writes_global_and_per_project(test_redis_url: str) -> None:
    """El global sigue existiendo porque el orchestrator lo consume con un
    grupo de consumidores; el por-proyecto es el que alimenta al tablero."""
    from api_server.db.domain import Task
    from api_server.events import EVENTS_STREAM, project_task_events_stream, publish_task_created
    from redis.asyncio import Redis

    tenant_id, project_id, task_id = uuid4(), uuid4(), uuid4()
    per_project = project_task_events_stream(str(project_id))

    async def _run() -> tuple[list, list]:
        redis: Redis = Redis.from_url(test_redis_url, decode_responses=True)
        try:
            await redis.delete(EVENTS_STREAM, per_project)
            task = Task(
                id=task_id,
                tenant_id=tenant_id,
                project_id=project_id,
                title="t",
                status="ready",
                priority="normal",
            )
            await publish_task_created(redis, task)
            return (
                await redis.xrange(EVENTS_STREAM),
                await redis.xrange(per_project),
            )
        finally:
            await redis.aclose()

    global_entries, project_entries = asyncio.run(_run())

    assert len(global_entries) == 1, "el stream global sigue siendo el del orchestrator"
    assert len(project_entries) == 1, "falta el stream por proyecto que lee el tablero"
    assert global_entries[0][1]["task_id"] == str(task_id)
    assert project_entries[0][1] == global_entries[0][1], "los dos llevan el MISMO evento"


def test_per_project_stream_key_is_derived_from_the_global_one() -> None:
    """Un nombre derivado y no inventado: el prefijo es el stream global, así
    que un `SCAN events:tasks*` los ve todos (operabilidad)."""
    from api_server.events import EVENTS_STREAM, project_task_events_stream

    pid = str(uuid4())
    assert project_task_events_stream(pid) == f"{EVENTS_STREAM}:{pid}"


# ===========================================================================
# Mitad 2 — el socket lee el stream de SU proyecto
# ===========================================================================
def test_kanban_socket_reads_the_project_stream_not_the_global_one(
    ws_client, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    """El test que cruza las dos mitades — y el que hay que saber leer en rojo.

    Se publican DOS eventos del MISMO proyecto y tenant: un señuelo que solo
    está en el stream global (el que el pump antiguo entregaba) y el bueno, que
    solo está en el del proyecto. Si el socket siguiera leyendo el global, el
    primer evento entregado sería el señuelo.

    El señuelo no es decorativo: **sin él este test no falla, se cuelga.** Con el
    código viejo el socket se queda esperando en un stream vacío y
    ``receive_json`` de ``TestClient`` bloquea sin plazo, así que la regresión
    aparecería como una suite colgada en vez de como un rojo. Un test que se
    cuelga es peor que uno que falla: nadie sabe si es él o es el entorno.
    """
    from api_server.events import EVENTS_STREAM, project_task_events_stream

    ids = asyncio.run(_seed(migrations_pg_dsn))
    token = _mint(ids["user"], ids["tenant"])
    stream = project_task_events_stream(str(ids["project"]))
    decoy_task, project_task = uuid4(), uuid4()

    asyncio.run(_clear(test_redis_url, EVENTS_STREAM, stream))
    asyncio.run(
        _xadd(
            test_redis_url,
            EVENTS_STREAM,
            _event_fields(ids["tenant"], ids["project"], decoy_task),
        )
    )
    asyncio.run(
        _xadd(test_redis_url, stream, _event_fields(ids["tenant"], ids["project"], project_task))
    )

    with ws_client.websocket_connect(f"/ws/kanban/{ids['project']}?token={token}") as ws:
        event = ws.receive_json()

    assert event["task_id"] == str(project_task), (
        "el socket entregó el evento que solo estaba en el stream global: "
        "sigue leyendo events:tasks"
    )
    assert event["payload"] == {"old_status": "ready", "new_status": "in_progress"}
