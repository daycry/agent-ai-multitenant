"""Integration tests: composición de una review-runtime (Plan 06 task_06_26).

Estos tests medían ``ReviewRuntimeManager.create``, el alta EN MEMORIA que el
commit 7959cdcb retiró por muerta. La CAPACIDAD no se fue con la clase: hoy la
implementa la tarea Celery ``workers.compose_review_runtime``
(``workers.tasks.review_runtime_task._compose_review_runtime``), que persiste la
fila ``review_sessions``, calcula el vencimiento, respeta el cap por tenant y
lanza el contenedor de la app. Aquí se mide ese camino, que es el que corre en
producción.

Lo que cubría el manager y aquí se recupera:

  * ``create`` sellaba ``expires_at = now + 48 h`` → hoy lo sella
    ``expires_in_seconds`` (mismo valor por defecto que ``DEFAULT_VERDICT_TIMEOUT_S``).
  * ``create`` guardaba los ids de los contenedores lanzados → hoy se escriben en
    la fila tras el spawn.
  * ``create`` arrastraba el spec (checklist humana + servicios) → hoy viaja como
    JSONB en ``review_sessions.spec``.
  * ``create`` rechazaba el N+1-ésimo del tenant → hoy lo rechaza el conteo en BD
    ANTES de crear fila o contenedor. ``tests/unit/test_review_tenant_cap.py``
    solo fija la decisión pura ``tenant_cap_exceeded``; su docstring dice que «el
    conteo en BD y el retorno temprano están integration-tested», y no lo estaban
    hasta este fichero.

El daemon Docker se sustituye por un doble: lo que se mide es el CABLEADO
(spawn → fila), no el arranque real de un contenedor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from api_server.db.models import ReviewSession
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.review_runtime import DEFAULT_TENANT_CAP, DEFAULT_VERDICT_TIMEOUT_S

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    from alembic import command

    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str, test_redis_url: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", test_redis_url)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


@pytest.fixture()
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Intercepta el fan-out de notificaciones (no hay dispatcher en el test)."""
    from api_server import celery_client

    events: list[dict[str, Any]] = []

    async def _fake_enqueue(event: dict[str, Any], **_kw: Any) -> bool:
        events.append(event)
        return True

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", _fake_enqueue)
    return events


class _FakeContainer:
    def __init__(self, cid: str) -> None:
        self.id = cid


class _FakeContainers:
    def __init__(self) -> None:
        self.runs: list[tuple[str, dict[str, Any]]] = []

    def run(self, image: str, **kwargs: Any) -> _FakeContainer:
        self.runs.append((image, kwargs))
        return _FakeContainer("cid-main-1")


class _FakeDockerClient:
    def __init__(self) -> None:
        self.containers = _FakeContainers()


async def _reset_and_seed_plan(dsn: str) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T co', 't-review-compose')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template)"
            " VALUES ($1, $2, 'P', 'active', false)",
            ids["project"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status)"
            " VALUES ($1, $2, $3, 'Plan', 'pending_human_validation')",
            ids["plan"],
            ids["tenant"],
            ids["project"],
        )
    finally:
        await conn.close()
    return ids


def _request(ids: dict[str, UUID], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "tenant_id": str(ids["tenant"]),
        "plan_id": str(ids["plan"]),
        "repo_name": "backend",
        # Explícito para que la resolución de worktree no toque git.
        "worktree_host_path": "/data/worktrees/plan-1",
        "plan_title": "Plan",
    }
    base.update(overrides)
    return base


def _sessionmaker(settings: object):
    engine = create_async_engine(settings.database_url)  # type: ignore[attr-defined]
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_compose_persists_the_session_with_the_48h_verdict_ttl(
    _migrated: None,
    workers_settings: object,
    migrations_pg_dsn: str,
    captured_events: list[dict[str, Any]],
) -> None:
    """Sucesor de ``test_create_spawns_containers_and_sets_expires_at`` (mitad TTL).

    Componer una review sella el plazo de veredicto por defecto — el mismo valor
    que ``DEFAULT_VERDICT_TIMEOUT_S``, que es lo que ata la constante viva del
    módulo al comportamiento de producción. Sin ``main_image`` no hay app que
    lanzar (hallazgo #4): la fila y las URLs firmadas siguen siendo útiles y el
    spec lo declara con ``app_configured=False``.
    """
    from workers.tasks.review_runtime_task import _compose_review_runtime

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    before = datetime.now(UTC)

    result = await _compose_review_runtime(_request(ids), workers_settings)  # type: ignore[arg-type]

    assert result["status"] == "running"
    assert result["container_ids"] == []
    assert "no review app image configured" in result["note"]

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, UUID(result["session_id"]))
        assert row is not None
        assert row.status == "running"
        assert row.plan_id == ids["plan"]
        assert row.kind == "plan"
        # El plazo por defecto es el de la constante del módulo, ni más ni menos.
        assert row.expires_at >= before + timedelta(seconds=DEFAULT_VERDICT_TIMEOUT_S - 60)
        assert row.expires_at <= datetime.now(UTC) + timedelta(
            seconds=DEFAULT_VERDICT_TIMEOUT_S + 60
        )
        assert row.spec["app_configured"] is False
    finally:
        await engine.dispose()

    # Y el owner se entera de que hay validación pendiente.
    assert [e["event_type"] for e in captured_events] == ["human_validation_needed"]


@pytest.mark.asyncio
async def test_compose_records_the_spawned_container_on_the_row(
    _migrated: None,
    workers_settings: object,
    migrations_pg_dsn: str,
    captured_events: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sucesor de ``test_create_spawns_containers_and_sets_expires_at`` (mitad spawn).

    Con imagen configurada se lanza el contenedor de la app y su id queda EN LA
    FILA: es lo que luego reapa el barrido de caducidad, así que un spawn que no
    se registre es un contenedor huérfano garantizado. Se comprueba además el
    nombre determinista, que es el contrato por el que la api-server hace de
    proxy inverso a la app (ADR 0062).
    """
    import workers.tasks.review_runtime_task as task_mod

    client = _FakeDockerClient()
    monkeypatch.setattr(task_mod, "get_docker_client", lambda: client)

    ids = await _reset_and_seed_plan(migrations_pg_dsn)

    result = await task_mod._compose_review_runtime(  # type: ignore[arg-type]
        _request(ids, main_image="backend:plan-1", main_port=8080), workers_settings
    )

    assert result["container_ids"] == ["cid-main-1"]
    session_id = result["session_id"]

    # El doble recibió la orden de arranque, con la imagen pedida y el nombre
    # que el proxy espera resolver.
    assert len(client.containers.runs) == 1
    image, kwargs = client.containers.runs[0]
    assert image == "backend:plan-1"
    assert kwargs["name"] == f"agentic-review-{session_id}"

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, UUID(session_id))
        assert row is not None
        assert row.container_ids == ["cid-main-1"]
        assert row.spec["app_configured"] is True
        assert row.spec["main_host"] == f"agentic-review-{session_id}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_compose_carries_the_checklist_and_service_config_into_the_spec(
    _migrated: None,
    workers_settings: object,
    migrations_pg_dsn: str,
    captured_events: list[dict[str, Any]],
) -> None:
    """Sucesor de ``test_create_carries_checklist_and_aux_services``.

    La checklist humana del plan y los servicios declarados del proyecto viajan
    íntegros al ``spec`` persistido: de ahí los lee la SPA de review para pintar
    lo que el validador tiene que marcar, y de ahí se rehidrata la sesión si el
    worker se reinicia a mitad de review.
    """
    from workers.tasks.review_runtime_task import _compose_review_runtime

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    checklist = [
        {"id": "human_06_01", "description": "ciclo end-to-end", "checklist": ["abrir", "cerrar"]},
        {"id": "human_06_02", "description": "el plan aparece en el tablero"},
    ]
    repository_config = {
        "services": [
            {"alias": "postgres", "image": "postgres:16-alpine"},
            {"alias": "redis", "image": "redis:7-alpine"},
        ]
    }

    result = await _compose_review_runtime(  # type: ignore[arg-type]
        _request(ids, human_checklist=checklist, repository_config=repository_config),
        workers_settings,
    )

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, UUID(result["session_id"]))
        assert row is not None
        assert [c["id"] for c in row.spec["human_checklist"]] == ["human_06_01", "human_06_02"]
        assert row.spec["human_checklist"][0]["checklist"] == ["abrir", "cerrar"]
        assert [s["alias"] for s in row.spec["repository_config"]["services"]] == [
            "postgres",
            "redis",
        ]
        # El tenant_id NO se duplica dentro del spec (va en su propia columna).
        assert "tenant_id" not in row.spec
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_compose_refuses_the_n_plus_first_session_of_a_tenant(
    _migrated: None,
    workers_settings: object,
    migrations_pg_dsn: str,
    captured_events: list[dict[str, Any]],
) -> None:
    """Sucesor de la guarda de cap que ``ReviewRuntimeManager.create`` hacía en
    memoria (y que ``test_review_cap.py``, borrado con ella, medía allí).

    Con el cap ya ocupado por sesiones ACTIVAS, la siguiente se rechaza SIN crear
    fila ni contenedor. Es el orden que importa: si se crease la fila primero, un
    bucle desbocado dejaría sesiones acumuladas aunque el cap "funcionase".
    """
    from workers.tasks.review_runtime_task import _compose_review_runtime

    ids = await _reset_and_seed_plan(migrations_pg_dsn)

    now = datetime.now(UTC)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        for i in range(DEFAULT_TENANT_CAP):
            await conn.execute(
                "INSERT INTO review_sessions"
                " (id, tenant_id, plan_id, spec, status, container_ids, expires_at)"
                " VALUES ($1, $2, $3, $4::jsonb, $5, '[]'::jsonb, $6)",
                uuid4(),
                ids["tenant"],
                ids["plan"],
                json.dumps({"plan_title": "Plan"}),
                # Mezcla a propósito: `suspended` también ocupa cupo.
                "suspended" if i == 0 else "running",
                now + timedelta(hours=10),
            )
    finally:
        await conn.close()

    result = await _compose_review_runtime(_request(ids), workers_settings)  # type: ignore[arg-type]

    assert result["status"] == "tenant_cap_exceeded"
    assert result["active"] == DEFAULT_TENANT_CAP
    assert result["cap"] == DEFAULT_TENANT_CAP
    assert "session_id" not in result

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            total = await db.scalar(select(func.count()).select_from(ReviewSession))
        # No se creó una sexta fila.
        assert total == DEFAULT_TENANT_CAP
    finally:
        await engine.dispose()

    # Y nadie recibió aviso de "hay validación pendiente" por una sesión que no existe.
    assert captured_events == []
