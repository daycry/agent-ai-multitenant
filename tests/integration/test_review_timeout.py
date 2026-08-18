"""Integration tests: caducidad por veredicto de una review-runtime (Plan 06 task_06_33).

48 h sin veredicto → la sesión pasa a ``expired``, sus contenedores se reapan y
el plan que esperaba validación humana queda ``blocked``.

Estos tests medían ``ReviewRuntimeManager.expire_overdue``, la caducidad EN
MEMORIA que el commit 7959cdcb retiró por muerta. La CAPACIDAD no se fue con la
clase: hoy la implementa el barrido de beat
``workers.maintenance._expire_review_runtimes`` (paso 1 = marcar caducadas,
paso 3 = reapar sus contenedores) sobre la tabla ``review_sessions``, con el
filtro en ``review_session_repo.list_running_overdue``. Aquí se mide ese camino,
que es el que corre en producción.

``test_review_expiry_lifecycle.py`` ya cubre el caso feliz (caduca + bloquea el
plan + es idempotente). Lo que se fija aquí son las GUARDAS que aquél no ejerce
porque su semilla no las contiene: una sesión todavía en plazo no se toca, una
terminal ya vencida no se re-marca, y el límite ``expires_at < now`` es estricto.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from api_server.db.models import ReviewSession
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    """Intercepta el fan-out de notificaciones.

    No hay notification-dispatcher levantado en el test, y el enqueue real
    reintenta contra el broker ~20 veces antes de rendirse: sin este doble el
    test tarda de más y depende de que el broker esté vivo para algo que no está
    midiendo.
    """
    from api_server import celery_client

    events: list[dict[str, Any]] = []

    async def _fake_enqueue(event: dict[str, Any], **_kw: Any) -> bool:
        events.append(event)
        return True

    monkeypatch.setattr(celery_client, "enqueue_event_dispatch", _fake_enqueue)
    return events


async def _reset_and_seed_plan(dsn: str) -> dict[str, UUID]:
    """Wipe the review tables and seed one tenant/project/plan awaiting validation."""
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T to', 't-review-timeout')",
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


async def _insert_session(
    dsn: str,
    ids: dict[str, UUID],
    *,
    status: str,
    expires_at: datetime,
    container_ids: tuple[str, ...] = (),
) -> UUID:
    session_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO review_sessions"
            " (id, tenant_id, plan_id, spec, status, container_ids, expires_at)"
            " VALUES ($1, $2, $3, $4::jsonb, $5, $6::jsonb, $7)",
            session_id,
            ids["tenant"],
            ids["plan"],
            json.dumps({"plan_title": "Plan", "owner_user_id": None}),
            status,
            json.dumps(list(container_ids)),
            expires_at,
        )
    finally:
        await conn.close()
    return session_id


def _sessionmaker(settings: object):
    engine = create_async_engine(settings.database_url)  # type: ignore[attr-defined]
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_expire_overdue_marks_expired_and_reaps_its_containers(
    _migrated: None,
    workers_settings: object,
    migrations_pg_dsn: str,
    captured_events: list[dict[str, Any]],
) -> None:
    """Sucesor de ``test_expire_overdue_destroys_containers``.

    Una sesión ``running`` cuyo ``expires_at`` ya pasó se marca ``expired`` Y sus
    contenedores se destruyen en la MISMA pasada — el paso 3 del barrido reapa
    toda sesión terminal con ``container_ids``, y ``expired`` lo es. El
    ``docker rm -f`` es best-effort (no-op sin daemon o con el id inexistente),
    así que lo observable es que la fila queda sin contenedores asociados.
    """
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn,
        ids,
        status="running",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        container_ids=("agentic-review-overdue",),
    )

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert "error" not in result
    assert result["expired"] == 1
    assert result["reaped"] == 1

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
        assert row is not None
        assert row.status == "expired"
        # Los contenedores dejaron de estar asociados a la sesión: sin esto el
        # reap volvería a listarla en cada pasada (y los contenedores serían un
        # leak silencioso tras la caducidad).
        assert row.container_ids == []
        # La fila SOBREVIVE (ADR 0107): la caducidad es historia, no borrado.
        assert row.deleted_at is None
    finally:
        await engine.dispose()

    # Caducar en silencio dejaría el plan bloqueado sin que nadie se entere: la
    # caducidad escala al owner (C8 F40).
    assert [e["event_type"] for e in captured_events] == ["review_escalated"]
    assert captured_events[0]["context"]["reason"] == "verdict_timeout"


@pytest.mark.asyncio
async def test_running_session_within_budget_is_left_alone(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sucesor de ``test_expire_overdue_ignores_running_session_within_budget``.

    Una sesión todavía en plazo no la toca el barrido. Es la guarda que impide
    que un bug en el filtro caduque reviews vivas delante del validador.
    """
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn,
        ids,
        status="running",
        expires_at=datetime.now(UTC) + timedelta(hours=10),
        container_ids=("agentic-review-live",),
    )

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert result["expired"] == 0
    assert result["reaped"] == 0

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
            from api_server.db.domain import Plan

            plan = await db.get(Plan, ids["plan"])
        assert row is not None and row.status == "running"
        # Sus contenedores siguen asociados — no se reapan los de una sesión viva.
        assert row.container_ids == ["agentic-review-live"]
        # Y el plan sigue esperando al humano, no bloqueado.
        assert plan is not None and plan.status == "pending_human_validation"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_overdue_boundary_is_strict_on_expires_at(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sucesor de ``test_expire_overdue_now_override``.

    El ``now`` inyectable de ``list_running_overdue`` es lo que hace el barrido
    determinista, y aquí se usa como instrumento para fijar el límite REAL:
    ``expires_at < now``, estricto. Con ``now`` justo en el vencimiento la sesión
    no está vencida; un instante después, sí.
    """
    from api_server.db.review_session_repo import list_running_overdue

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    expires_at = datetime.now(UTC) + timedelta(hours=10)
    session_id = await _insert_session(
        migrations_pg_dsn, ids, status="running", expires_at=expires_at
    )

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            # Ahora mismo no ha vencido.
            assert await list_running_overdue(db) == []
            # Justo en el instante de vencimiento tampoco (el corte es estricto).
            assert await list_running_overdue(db, now=expires_at) == []
            # Un segundo después, sí.
            overdue = await list_running_overdue(db, now=expires_at + timedelta(seconds=1))
        assert [r.id for r in overdue] == [session_id]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_session_past_its_expiry_is_not_re_expired(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sucesor de ``test_already_terminal_sessions_skipped``.

    Una sesión ya juzgada (``approved``) cuyo ``expires_at`` pasó NO se re-marca
    ``expired``: el veredicto es historia que consumen el panel y
    generate-corrections (ADR 0107), y pisarlo con ``expired`` lo destruiría.
    Sus contenedores sí se reapan — eso es limpieza, no cambio de veredicto.
    """
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn,
        ids,
        status="approved",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
        container_ids=("agentic-review-judged",),
    )

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert result["expired"] == 0

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
        assert row is not None
        assert row.status == "approved"
        assert row.container_ids == []
    finally:
        await engine.dispose()
