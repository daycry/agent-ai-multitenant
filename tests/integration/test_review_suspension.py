"""Integration tests: suspensión por inactividad de una review-runtime (Plan 06 task_06_32).

Una sesión de validación que nadie toca durante la ventana de inactividad pasa a
``suspended``, para no tener contenedores de preview ocupando la máquina días
mientras el validador no aparece.

Estos tests medían ``ReviewRuntimeManager.suspend_idle``, la suspensión EN
MEMORIA que el commit 7959cdcb retiró por muerta. La CAPACIDAD no se fue con la
clase: hoy la implementa el paso 2 del barrido de beat
``workers.maintenance._expire_review_runtimes`` sobre ``review_sessions``, con el
filtro en ``review_session_repo.list_running_idle`` y la transición en
``suspend_session``. Aquí se mide ese camino, que es el que corre en producción.

Y no lo medía NADIE más: al reapuntarlos, ``list_running_idle`` / ``suspend_session``
pasan de cero cobertura a tenerla. ``test_review_expiry_lifecycle.py`` ejercita el
mismo barrido pero su semilla no contiene ninguna sesión inactiva, así que nunca
comprobó el contador ``suspended``.

OJO con la ventana: el barrido usa ``_SUSPEND_IDLE_AFTER = 24 h``, que NO es
``workers.review_runtime.DEFAULT_IDLE_SUSPEND_S`` (4 h). Los tests fijan el
comportamiento real (24 h).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
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


async def _reset_and_seed_plan(dsn: str) -> dict[str, UUID]:
    ids = {"tenant": uuid4(), "project": uuid4(), "plan": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE review_sessions, plans, projects, organizations RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T su', 't-review-suspend')",
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
    idle_for: timedelta,
) -> UUID:
    """Seed one session with a controlled ``last_activity_at``.

    ``expires_at`` siempre queda MUY en el futuro: aquí se mide la suspensión, y
    una sesión vencida la atraparía antes el paso 1 del barrido (que caduca
    ``running`` y ``suspended`` por igual).
    """
    session_id = uuid4()
    now = datetime.now(UTC)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO review_sessions"
            " (id, tenant_id, plan_id, spec, status, container_ids, expires_at,"
            "  last_activity_at)"
            " VALUES ($1, $2, $3, $4::jsonb, $5, '[]'::jsonb, $6, $7)",
            session_id,
            ids["tenant"],
            ids["plan"],
            json.dumps({"plan_title": "Plan", "owner_user_id": None}),
            status,
            now + timedelta(days=30),
            now - idle_for,
        )
    finally:
        await conn.close()
    return session_id


def _sessionmaker(settings: object):
    engine = create_async_engine(settings.database_url)  # type: ignore[attr-defined]
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_idle_session_is_suspended_and_stamped(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sucesor de ``test_suspend_idle_pauses_stale_sessions``.

    Una sesión ``running`` sin actividad más allá de la ventana pasa a
    ``suspended`` y queda sellada con ``suspended_at`` (el sello es lo que
    permite auditar cuánto lleva dormida).
    """
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn, ids, status="running", idle_for=timedelta(hours=30)
    )

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert "error" not in result
    assert result["suspended"] == 1
    # No caducó: sigue dentro de su plazo de veredicto.
    assert result["expired"] == 0

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
        assert row is not None
        assert row.status == "suspended"
        assert row.suspended_at is not None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_within_the_idle_window_stays_running(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sucesor de ``test_running_session_within_idle_budget_stays_running``.

    Cinco horas de inactividad NO suspenden: la ventana real del barrido es de
    24 h. El valor está elegido a propósito por encima de
    ``DEFAULT_IDLE_SUSPEND_S`` (4 h) para que este test fije cuál de los dos
    manda de verdad — si alguien "unificara" el barrido con esa constante
    creyendo que ya la usa, se pondría rojo aquí en vez de empezar a dormir
    sesiones que el validador está mirando.
    """
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn, ids, status="running", idle_for=timedelta(hours=5)
    )

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert result["suspended"] == 0

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
        assert row is not None
        assert row.status == "running"
        assert row.suspended_at is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_session_is_never_suspended(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Sucesor de ``test_terminal_session_not_suspended``.

    Una sesión ya juzgada lleva, por definición, mucho tiempo sin actividad. Si
    el filtro de inactividad no excluyera las terminales, cada barrido pisaría
    veredictos con ``suspended``.
    """
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn, ids, status="approved", idle_for=timedelta(hours=30)
    )

    result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]

    assert result["suspended"] == 0

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
        assert row is not None
        assert row.status == "approved"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_touch_activity_pushes_the_idle_window_back(
    _migrated: None, workers_settings: object, migrations_pg_dsn: str
) -> None:
    """Mitad superviviente de ``test_touch_resumes_suspended_session``.

    Aquel test medía DOS cosas del manager en memoria: que tocar una sesión
    reanudaba una ya suspendida (``suspended`` → ``running``), y que tocarla
    aleja la suspensión. Lo PRIMERO ya no existe en ninguna parte —
    ``touch_activity`` solo sella ``last_activity_at`` y nadie devuelve una
    sesión suspendida a ``running``— así que no se reapunta. Lo SEGUNDO sí es
    real y es lo que de verdad protege al validador: mientras abre páginas de la
    SPA de review, el barrido no le duerme la sesión debajo.
    """
    from api_server.db.review_session_repo import touch_activity
    from workers.maintenance import _expire_review_runtimes

    ids = await _reset_and_seed_plan(migrations_pg_dsn)
    session_id = await _insert_session(
        migrations_pg_dsn, ids, status="running", idle_for=timedelta(hours=30)
    )

    engine, sessionmaker = _sessionmaker(workers_settings)
    try:
        # El validador abre la review: se sella actividad fresca.
        async with sessionmaker() as db, db.begin():
            touched = await touch_activity(db, session_id)
        assert touched is not None

        result = await _expire_review_runtimes(workers_settings)  # type: ignore[arg-type]
        assert result["suspended"] == 0

        async with sessionmaker() as db:
            row = await db.get(ReviewSession, session_id)
        assert row is not None
        assert row.status == "running"
    finally:
        await engine.dispose()
