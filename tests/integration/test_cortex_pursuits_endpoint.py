"""Córtex — endpoints de curiosidad ``/owner/cortex/curiosity/pursuits`` (ADR 0078).

* ``GET /curiosity/pursuits`` — historial de persecuciones ("lo que está
  aprendiendo"): gated ``require_system_owner`` (DB-authoritative), filtro
  ``owner_user_id`` explícito (cross-owner OBLIGATORIO), filtro opcional por
  ``status``, orden ``created_at DESC``, y los campos ``approved``/``cost_usd``
  que la UI necesita para el gate y para el coste real.
* ``POST /curiosity/pursuits/{id}/approve`` — el **owner-approval gate** (paso 7
  del bucle): el owner aprueba o rechaza un tema propuesto ANTES de que el córtex
  salga a Internet. Aprobar conserva ``selected`` (el bucle lo continúa en su
  siguiente pasada); rechazar cierra en ``skipped``.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

pytestmark = pytest.mark.integration


@pytest.fixture()
def configured_app(
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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_curiosity_pursuits, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Pursuits Tenant",
            "pursuits-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, 'h', true), ($3, $4, 'h', false)",
            owner_id,
            "owner@pursuits.test",
            other_id,
            "otro@pursuits.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $2, $5, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
            uuid4(),
            other_id,
        )
        # Dos pursuits del owner (digested y surfaced) + uno del otro usuario.
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status,"
            " created_at, updated_at) VALUES"
            " ($1, $2, 'tema viejo', 'digested', now() - interval '1 hour', now()),"
            " ($3, $2, 'tema nuevo', 'surfaced', now(), now()),"
            " ($4, $5, 'tema ajeno', 'digested', now(), now())",
            uuid4(),
            owner_id,
            uuid4(),
            uuid4(),
            other_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


async def _mint(user_id: UUID, tenant_id: UUID, *, owner: bool) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=owner)


@pytest.mark.asyncio
async def test_lista_pursuits_del_owner_orden_y_filtro(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/owner/cortex/curiosity/pursuits",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200, resp.text
        items = resp.json()
        # Solo los del owner (jamás 'tema ajeno'), más reciente primero.
        topics = [item["topic"] for item in items]
        assert topics == ["tema nuevo", "tema viejo"]
        assert items[0]["status"] == "surfaced"
        assert items[0]["surfaced_at"] is None or isinstance(items[0]["surfaced_at"], str)

        # Filtro por status.
        resp2 = await client.get(
            "/owner/cortex/curiosity/pursuits?status=digested",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp2.status_code == 200
        assert [item["topic"] for item in resp2.json()] == ["tema viejo"]


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_no_owner_recibe_403(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    # Claim forjado: is_system_owner=True en el token, pero la BD dice false.
    token = await _mint(seed["other_id"], seed["tenant_id"], owner=True)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/owner/cortex/curiosity/pursuits",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403


# ===========================================================================
# POST /curiosity/pursuits/{id}/approve — el owner-approval gate (F4, ADR 0078)
# ===========================================================================
# El paso 7 del bucle deja el pursuit ``selected`` con ``approved IS NULL`` y NO sale
# a Internet. Este endpoint es la ÚNICA vía por la que ese veredicto se escribe: sin
# él la columna `approved` de la migración 0123 no tenía quien la moviera y el gate
# era un mecanismo entregado sin llamantes (el patrón nº5 de
# docs/03-guides/verificar-antes-de-implementar.md).
async def _insert_pursuit(
    dsn: str,
    *,
    owner_user_id: UUID,
    topic: str,
    status: str = "selected",
    approved: bool | None = None,
    cost_usd: str = "0",
) -> UUID:
    """Una fila de pursuit con su veredicto y coste explícitos (devuelve su id)."""
    pursuit_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status,"
            " approved, cost_usd, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, $6::numeric, now(), now())",
            pursuit_id,
            owner_user_id,
            topic,
            status,
            approved,
            cost_usd,
        )
    finally:
        await conn.close()
    return pursuit_id


async def _read_pursuit(dsn: str, pursuit_id: UUID) -> dict[str, object]:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, approved, metadata FROM cortex_curiosity_pursuits WHERE id = $1",
            pursuit_id,
        )
    finally:
        await conn.close()
    assert row is not None
    return dict(row)


def _client(app):
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_lista_pursuits_expone_approved_y_cost_usd(
    configured_app, migrations_pg_dsn: str
) -> None:
    """``approved`` y ``cost_usd`` viajan en el JSON del listado.

    Sin ``approved`` la UI no puede saber CUÁL pursuit espera decisión (su helper
    ``pursuitAwaitsApproval`` mira ``status==='selected' && approved===null``), así
    que el botón Aprobar/Rechazar no se podía pintar. Sin ``cost_usd``, el coste que
    el bucle ya persiste no llega a la pantalla: dato escrito y nunca leído."""
    seed = await _seed(migrations_pg_dsn)
    await _insert_pursuit(
        migrations_pg_dsn,
        owner_user_id=seed["owner_id"],
        topic="tema pendiente",
        status="selected",
        approved=None,
    )
    await _insert_pursuit(
        migrations_pg_dsn,
        owner_user_id=seed["owner_id"],
        topic="tema pagado",
        status="digested",
        approved=True,
        cost_usd="0.031250",
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.get(
            "/owner/cortex/curiosity/pursuits",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    by_topic = {item["topic"]: item for item in resp.json()}
    assert by_topic["tema pendiente"]["approved"] is None
    assert by_topic["tema pagado"]["approved"] is True
    assert by_topic["tema pagado"]["cost_usd"] == pytest.approx(0.03125)
    # Las filas históricas (sin veredicto ni coste) siguen siendo serializables.
    assert by_topic["tema viejo"]["approved"] is None
    assert by_topic["tema viejo"]["cost_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_aprobar_deja_approved_true_y_conserva_selected(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Aprobar escribe ``approved=true`` y **NO** toca el ``status``, que sigue
    ``selected``.

    Este es el punto en el que es fácil romper el gate sin que ningún test lo note:
    parece natural mover el pursuit a ``searching`` al aprobarlo, pero el que sale a
    buscar es el bucle, y su consulta de reanudación
    (``workers/cortex_curiosity.py::_find_resumable_pursuit``) exige literalmente
    ``status == 'selected'`` AND ``approved IS NOT FALSE``. Un ``status='searching'``
    escrito aquí dejaría el pursuit aprobado FUERA de esa consulta: nadie lo
    investigaría nunca y el owner vería "buscando…" para siempre. Lo confirma también
    el docstring de ``get_cortex_curiosity_approval_gate``: «el owner lo aprueba desde
    el panel y **la siguiente pasada lo continúa**»."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = await _insert_pursuit(
        migrations_pg_dsn, owner_user_id=seed["owner_id"], topic="qué es un DAG"
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/owner/cortex/curiosity/pursuits/{pursuit_id}/approve",
            json={"approved": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approved"] is True
    assert body["status"] == "selected", "aprobar NO adelanta el estado: eso lo hace el bucle"
    assert body["topic"] == "qué es un DAG"

    row = await _read_pursuit(migrations_pg_dsn, pursuit_id)
    assert row["approved"] is True
    assert row["status"] == "selected"


@pytest.mark.asyncio
async def test_rechazar_deja_skipped_con_su_razon(configured_app, migrations_pg_dsn: str) -> None:
    """Rechazar escribe ``approved=false`` Y cierra el pursuit en ``skipped``.

    Los dos a la vez, y por razones distintas: ``approved=false`` es lo que saca la
    fila de la consulta de reanudación del bucle (``approved IS NOT FALSE``), y
    ``skipped`` es lo que la convierte en terminal para el panel — dejarla en
    ``selected`` la mostraría eternamente como "esperando decisión" ya decidida. La
    razón queda en ``metadata.reason``, la misma clave que usa el bucle."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = await _insert_pursuit(
        migrations_pg_dsn, owner_user_id=seed["owner_id"], topic="tema que no quiero"
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/owner/cortex/curiosity/pursuits/{pursuit_id}/approve",
            json={"approved": False},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approved"] is False
    assert body["status"] == "skipped"

    row = await _read_pursuit(migrations_pg_dsn, pursuit_id)
    assert row["approved"] is False
    assert row["status"] == "skipped"
    assert json.loads(str(row["metadata"]))["reason"] == "owner_rejected"


@pytest.mark.asyncio
async def test_decision_repetida_es_idempotente_y_la_contraria_da_409(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La misma decisión dos veces es un no-op 200 (doble clic del panel); la decisión
    CONTRARIA sobre un pursuit ya decidido es un 409, no un silencioso volteo.

    Importa por lo que protege: un pursuit ya aprobado puede haber salido a Internet
    ya, y "des-aprobarlo" no desharía el gasto — mentiría al owner haciéndole creer
    que canceló algo que ya ocurrió."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = await _insert_pursuit(
        migrations_pg_dsn, owner_user_id=seed["owner_id"], topic="tema decidido"
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    headers = {"Authorization": f"Bearer {token}"}
    url = f"/owner/cortex/curiosity/pursuits/{pursuit_id}/approve"
    async with _client(configured_app) as client:
        first = await client.post(url, json={"approved": True}, headers=headers)
        assert first.status_code == 200, first.text
        # Doble clic: idempotente, sin cambiar nada.
        again = await client.post(url, json={"approved": True}, headers=headers)
        assert again.status_code == 200, again.text
        assert again.json()["approved"] is True
        # Decisión contraria: conflicto explícito.
        flip = await client.post(url, json={"approved": False}, headers=headers)
        assert flip.status_code == 409, flip.text

    row = await _read_pursuit(migrations_pg_dsn, pursuit_id)
    assert row["approved"] is True, "el 409 no debe haber mutado la fila"
    assert row["status"] == "selected"


@pytest.mark.asyncio
async def test_decidir_pursuit_ya_terminado_da_409(configured_app, migrations_pg_dsn: str) -> None:
    """Un pursuit que ya terminó su ciclo no se puede aprobar retroactivamente.

    Las filas anteriores a la migración 0123 quedaron en ``approved IS NULL`` a
    propósito (su veredicto es genuinamente desconocido), así que el gate NO puede
    apoyarse sólo en ``approved IS NULL`` para decidir qué es "pendiente": tiene que
    exigir además ``status='selected'``. Sin esa condición, aprobar un ``digested``
    de hace meses devolvería 200 y afirmaría que el owner aprobó algo que nunca vio."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = await _insert_pursuit(
        migrations_pg_dsn,
        owner_user_id=seed["owner_id"],
        topic="tema ya digerido",
        status="digested",
        approved=None,
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/owner/cortex/curiosity/pursuits/{pursuit_id}/approve",
            json={"approved": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 409, resp.text
    row = await _read_pursuit(migrations_pg_dsn, pursuit_id)
    assert row["approved"] is None
    assert row["status"] == "digested"


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_aprobar_pursuit_de_otro_owner_da_404_y_no_lo_muta(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El pursuit de OTRO usuario no se puede decidir, y da 404 (no 403).

    404 y no 403 a propósito: un 403 confirmaría que ese id EXISTE, y la tabla es
    tenant-less (sin RLS de respaldo, ADR 0074) — el aislamiento es el filtro
    ``owner_user_id`` explícito del propio UPDATE. La aserción que de verdad prueba el
    aislamiento es la última: la fila ajena sigue intacta."""
    seed = await _seed(migrations_pg_dsn)
    ajeno_id = await _insert_pursuit(
        migrations_pg_dsn,
        owner_user_id=seed["other_id"],
        topic="curiosidad ajena",
        status="selected",
        approved=None,
    )
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/owner/cortex/curiosity/pursuits/{ajeno_id}/approve",
            json={"approved": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404, resp.text
    row = await _read_pursuit(migrations_pg_dsn, ajeno_id)
    assert row["approved"] is None, "la fila del otro owner NO puede haberse tocado"
    assert row["status"] == "selected"


@pytest.mark.asyncio
async def test_aprobar_pursuit_inexistente_da_404(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    token = await _mint(seed["owner_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/owner/cortex/curiosity/pursuits/{uuid4()}/approve",
            json={"approved": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_aprobar_sin_ser_owner_da_403(configured_app, migrations_pg_dsn: str) -> None:
    """Gate ``require_system_owner`` DB-authoritative, igual que sus hermanos: el
    claim ``is_system_owner`` del token se forja y da igual."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = await _insert_pursuit(
        migrations_pg_dsn, owner_user_id=seed["owner_id"], topic="tema del owner"
    )
    token = await _mint(seed["other_id"], seed["tenant_id"], owner=True)
    async with _client(configured_app) as client:
        resp = await client.post(
            f"/owner/cortex/curiosity/pursuits/{pursuit_id}/approve",
            json={"approved": True},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 403, resp.text
    row = await _read_pursuit(migrations_pg_dsn, pursuit_id)
    assert row["approved"] is None
