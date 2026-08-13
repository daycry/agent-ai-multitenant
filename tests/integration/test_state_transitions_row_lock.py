"""El `FOR UPDATE` de las transiciones críticas serializa DE VERDAD
(task_prod13_22, hallazgo api-10).

Que `get_writable_or_404(..., for_update=True)` emita un `SELECT … FOR UPDATE`
ya lo fija un test unitario compilando el statement
(`tests/unit/test_row_lock_and_pagination.py`). Lo que ESE test no puede
demostrar —y es justo la propiedad por la que existe el candado— es que el
bloqueo dure hasta el final de la transacción del request y que el segundo
lector, al desbloquearse, decida sobre el estado NUEVO en vez de sobre el que
leyó el primero. Eso solo se ve con dos transacciones vivas a la vez, y por eso
esta suite es de integración.

Dos niveles, a propósito:

* **El de sesiones** (determinista): controla el interleave a mano y lleva su
  propio CONTROL sin candado en el mismo test. Es el que se pone rojo si alguien
  quita el `for_update`.
* **El del endpoint** (dos firmas en paralelo por HTTP): comprueba que el
  cableado real —dependencias, RLS, máquina de estados— produce el desenlace
  correcto. Es más débil como red (el interleave no está forzado), y eso queda
  escrito ahí en vez de fingir que prueba lo mismo que el primero.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Seed: un tenant, dos aprobadores, un proyecto y un plan en pending_approval.
# ---------------------------------------------------------------------------
async def _seed(dsn: str, *, threshold: str = "0") -> dict[str, UUID]:
    ids = {
        "tenant": uuid4(),
        "alice": uuid4(),
        "bob": uuid4(),
        "project": uuid4(),
        "plan": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE plan_comments, plans, projects, user_org_memberships,"
            " organizations, users, platform_settings RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["tenant"],
            "Tenant Lock",
            "tenant-lock-prod13",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-lock-prod13",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["alice"],
            "alice@lock.test",
            "h",
            ids["bob"],
            "bob@lock.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,$4),($5,$6,$7,$8)",
            uuid4(),
            ids["tenant"],
            ids["alice"],
            "tenant_admin",
            uuid4(),
            ids["tenant"],
            ids["bob"],
            "tenant_admin",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1,$2,$3)",
            ids["project"],
            ids["tenant"],
            "Lock Project",
        )
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)",
            "plan_approval_double_signature_threshold",
            f'"{threshold}"',
        )
    finally:
        await conn.close()
    return ids


async def _insert_plan(dsn: str, ids: dict[str, UUID], spec: dict) -> None:
    import json

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO plans (id, tenant_id, project_id, title, status, specification)"
            " VALUES ($1,$2,$3,$4,$5,$6::jsonb)",
            ids["plan"],
            ids["tenant"],
            ids["project"],
            "Plan bajo candado",
            "pending_approval",
            json.dumps(spec),
        )
    finally:
        await conn.close()


# Plan caro: con el umbral de doble firma en 0,01 USD la primera firma NO puede
# aprobar sola. Que el coste supere el umbral se comprueba en el test, porque si
# no lo superara el endpoint aprobaría a la primera y la segunda firma recibiría
# un 409 legítimo — un verde que no probaría nada de lo que buscamos.
_SPEC = {
    "metadata": {"default_model_id": "claude-opus-4-7"},
    "tasks": [
        {"id": "t1", "title": "A", "complexity": "xl", "model": "claude-opus-4-7"},
        {"id": "t2", "title": "B", "complexity": "xl", "model": "claude-opus-4-7"},
        {"id": "t3", "title": "C", "complexity": "xl", "model": "claude-opus-4-7"},
    ],
}
_DOUBLE_SIGNATURE_THRESHOLD = "0.01"


# ===========================================================================
# Nivel 1 — dos transacciones vivas, interleave controlado a mano
# ===========================================================================
async def _read_plan(session, plan_id: UUID, principal, *, for_update: bool):
    from api_server.db.domain import Plan
    from api_server.routers._helpers import get_writable_or_404

    return await get_writable_or_404(
        session,
        Plan,
        plan_id,
        principal,
        not_found_detail="plan not found",
        for_update=for_update,
    )


@pytest.mark.asyncio
async def test_for_update_blocks_the_second_reader_until_the_first_commits(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """El candado dura hasta el COMMIT, y el segundo lector ve el estado nuevo.

    El control está dentro: el mismo guion se corre con ``for_update=False`` y
    ahí el segundo lector no espera a nadie y decide sobre un estado ya rancio —
    que es exactamente la carrera de la doble firma. Sin ese arco, la mitad
    verde no demostraría que el candado hace algo.
    """
    from api_server.auth.deps import AuthPrincipal

    ids = await _seed(migrations_pg_dsn)
    await _insert_plan(migrations_pg_dsn, ids, _SPEC)

    principal = AuthPrincipal(user_id=ids["alice"], session_id=uuid7(), tenant_id=ids["tenant"])
    engine = create_async_engine(admin_database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as first, sessionmaker() as second:
            # (1) El primer firmante lee bajo candado y NO commitea todavía.
            plan_first = await _read_plan(first, ids["plan"], principal, for_update=True)
            assert plan_first.status == "pending_approval"

            # (2) El segundo intenta leer bajo candado: tiene que quedarse esperando.
            pending = asyncio.ensure_future(
                _read_plan(second, ids["plan"], principal, for_update=True)
            )
            done, _ = await asyncio.wait({pending}, timeout=1.0)
            assert not done, (
                "el segundo SELECT … FOR UPDATE no esperó al primero: "
                "el candado no está serializando nada"
            )

            # (3) El primero firma y cierra su transacción.
            plan_first.status = "pending_second_approval"
            plan_first.first_approved_by = ids["alice"]
            await first.commit()

            # (4) Al desbloquearse, el segundo lee la fila ACTUALIZADA (READ
            #     COMMITTED re-evalúa la versión viva), así que decide sobre el
            #     estado real y no sobre el que vio el primero.
            plan_second = await asyncio.wait_for(pending, timeout=10)
            assert plan_second.status == "pending_second_approval"
            assert plan_second.first_approved_by == ids["alice"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_without_for_update_the_second_reader_sees_stale_state(
    _migrated: None, admin_database_url: str, migrations_pg_dsn: str
) -> None:
    """El CONTROL del test anterior: sin candado no hay espera ni relectura."""
    from api_server.auth.deps import AuthPrincipal

    ids = await _seed(migrations_pg_dsn)
    await _insert_plan(migrations_pg_dsn, ids, _SPEC)

    principal = AuthPrincipal(user_id=ids["alice"], session_id=uuid7(), tenant_id=ids["tenant"])
    engine = create_async_engine(admin_database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as first, sessionmaker() as second:
            plan_first = await _read_plan(first, ids["plan"], principal, for_update=True)
            plan_first.status = "pending_second_approval"
            plan_first.first_approved_by = ids["alice"]
            await first.flush()  # escrito, aún SIN commitear

            plan_second = await asyncio.wait_for(
                _read_plan(second, ids["plan"], principal, for_update=False), timeout=5
            )
            # Lee al instante y lee lo viejo: los dos se creerían el PRIMER firmante.
            assert plan_second.status == "pending_approval"
            assert plan_second.first_approved_by is None
            await first.rollback()
    finally:
        await engine.dispose()


# ===========================================================================
# Nivel 2 — dos firmas en paralelo contra el endpoint real
# ===========================================================================
@pytest.fixture()
def configured_plan_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID) -> dict[str, str]:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    token = encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_two_parallel_signatures_end_in_an_approved_plan_with_two_signers(
    configured_plan_app, migrations_pg_dsn: str
) -> None:
    """Dos admins pulsando «Aprobar» a la vez sobre un plan de doble firma.

    Desenlace correcto: una primera firma + una segunda ⇒ el plan queda
    ``approved`` con DOS firmantes distintos. Sin el candado, las dos requests
    pueden leer ``pending_approval`` con ``first_approved_by = NULL`` y tomar las
    dos la rama de PRIMERA firma: el plan se queda en ``pending_second_approval``
    con una sola firma guardada, y los dos humanos creen haber firmado.

    Honestidad sobre su fuerza: el interleave NO está forzado, así que este test
    puede pasar por casualidad con el candado quitado. El que muerde siempre es
    el de sesiones de arriba; éste comprueba el cableado de punta a punta.
    """
    from decimal import Decimal

    from api_server.chat.cost import compute_ai_cost

    ids = await _seed(migrations_pg_dsn, threshold=_DOUBLE_SIGNATURE_THRESHOLD)
    await _insert_plan(migrations_pg_dsn, ids, _SPEC)
    # Sin esta comprobación el test podría pasar por el motivo equivocado: un
    # plan por debajo del umbral se aprueba con UNA firma y la segunda recibe un
    # 409 correcto.
    assert compute_ai_cost(_SPEC, default_model_id="claude-opus-4-7").cost_max > Decimal(
        _DOUBLE_SIGNATURE_THRESHOLD
    )
    alice = await _mint(ids["alice"], ids["tenant"])
    bob = await _mint(ids["bob"], ids["tenant"])

    async with (
        AsyncClient(transport=ASGITransport(app=configured_plan_app), base_url="http://test") as c1,
        AsyncClient(transport=ASGITransport(app=configured_plan_app), base_url="http://test") as c2,
    ):
        first, second = await asyncio.gather(
            c1.post(f"/plans/{ids['plan']}/approve", headers=alice),
            c2.post(f"/plans/{ids['plan']}/approve", headers=bob),
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        final = await c1.get(f"/plans/{ids['plan']}", headers=alice)

    body = final.json()
    assert body["status"] == "approved", (
        f"las dos firmas se pisaron: el plan se quedó sin la segunda ({body['status']})"
    )
    assert body["first_approved_by"] and body["approved_by"]
    assert body["first_approved_by"] != body["approved_by"], "el plan se aprobó con una sola firma"
