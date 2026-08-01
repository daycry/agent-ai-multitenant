"""La cadena de alertas, de punta a punta (prod-08 `task_prod08_alert_e2e_03`).

Este es EL test del plan. Todo lo demás de prod-08 (el exporter, las reglas, el
receiver de respaldo) existe para que ocurra esto: que una alerta sintética
acabe siendo **una notificación que un System Admin puede leer**.

Por qué no bastaba con lo que ya había
--------------------------------------
`tests/integration/test_internal_alerts_ingest.py` cubre bien el endpoint, pero
sustituye `enqueue_event_dispatch` por una lista en memoria: comprueba la forma
del evento y ahí se acaba. Eso deja sin verificar justo el tramo donde esta base
falla una y otra vez — «mecanismo entregado, cero llamantes»:

  * que `infra_alert` esté en el registro de eventos del dispatcher (si no,
    `resolve_event_dispatch` devuelve `no_op` y no pasa nada, en silencio);
  * que exista plantilla ES/EN para él (sin plantilla, el cuerpo va vacío);
  * que el canal platform-scoped se resuelva con `tenant_id IS NULL` (con la
    resolución de tenant, un evento de plataforma no encuentra canal);
  * que la fila persistida sea VISIBLE en el inbox de plataforma — que es lo
    único que un humano mira.

Un fallo en cualquiera de esos cuatro puntos no produce error ni log de fallo:
produce silencio, que es indistinguible de «no ha pasado nada malo».

Qué se sustituye y qué no
-------------------------
Solo el **salto por el broker**: en vez de `apply_async`, las tareas Celery se
invocan en proceso. El registro de eventos, la resolución de canales, el render
de plantillas, la persistencia y el endpoint del inbox son los reales, contra el
Postgres de test con RLS.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_TOKEN = "test-alerts-token"


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
    monkeypatch.setenv("API_SERVER_ALERTS_INGEST_TOKEN", _TOKEN)

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


@pytest.fixture()
def dispatcher_settings(admin_database_url: str, test_redis_url: str, monkeypatch):
    """Settings del notification-dispatcher apuntando al stack de test.

    Usa la conexión ADMIN (BYPASSRLS) porque eso es lo que el dispatcher tiene
    en producción: es un servicio de plataforma, no una petición de tenant.
    """
    monkeypatch.setenv("NOTIFY_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("NOTIFY_EVENTS_REDIS_URL", test_redis_url)
    from notification_dispatcher.config import get_settings as nd_settings

    nd_settings.cache_clear()
    yield nd_settings()
    nd_settings.cache_clear()


async def _seed_platform_channel(dsn: str) -> dict[str, Any]:
    """Un System Admin y un canal in_app platform-scoped. NINGÚN log previo: la
    fila que se busque al final tiene que haberla creado la cadena."""
    ids: dict[str, Any] = {"system_admin": uuid4(), "channel": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE notification_log_reads, notification_logs,"
            " notification_preferences, notification_channels,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin)"
            " VALUES ($1, 'root@alertchain.test', 'h', true)",
            ids["system_admin"],
        )
        await conn.execute(
            "INSERT INTO notification_channels"
            " (id, scope, channel_type, tenant_id, name, enabled, config)"
            " VALUES ($1, 'platform', 'in_app', NULL, 'Bandeja del System Admin', true, '{}')",
            ids["channel"],
        )
    finally:
        await conn.close()
    return ids


def _webhook(fingerprint: str = "e2e-1") -> dict[str, Any]:
    """Un webhook v4 de Alertmanager como el que entrega de verdad, con la
    alerta que este plan hace que exista: `ServiceDown`."""
    return {
        "version": "4",
        "groupKey": '{}:{alertname="ServiceDown"}',
        "status": "firing",
        "receiver": "platform-notifier",
        "alerts": [
            {
                "status": "firing",
                "fingerprint": fingerprint,
                "labels": {
                    "alertname": "ServiceDown",
                    "severity": "critical",
                    "job": "api-server",
                    "instance": "api-server:8000",
                },
                "annotations": {
                    "summary": "El target api-server (api-server:8000) no responde",
                    "description": "Prometheus lleva 2 minutos sin poder scrapear el target.",
                },
                "startsAt": "2026-07-31T03:00:00Z",
            }
        ],
    }


def _bridge_the_broker(monkeypatch: pytest.MonkeyPatch, settings: Any) -> list[dict[str, Any]]:
    """Sustituye SOLO el salto por el broker: `enqueue_event_dispatch` pasa a
    ejecutar en proceso el mismo camino que ejecutaría el worker del dispatcher
    (resolver el plan → entregar cada envío)."""
    delivered: list[dict[str, Any]] = []

    async def _dispatch_in_process(event: dict[str, Any], **_: Any) -> bool:
        from notification_dispatcher.event_mapping import (
            DispatchDecision,
            IncomingEvent,
            resolve_event_dispatch,
        )
        from notification_dispatcher.tasks import SendRequest, _send_notification
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(settings.database_url)
        try:
            sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
            plan = await resolve_event_dispatch(
                IncomingEvent.from_dict(event), settings=settings, sessionmaker=sessionmaker
            )
        finally:
            await engine.dispose()

        for decision in plan.decisions:
            if decision.decision is DispatchDecision.SUPPRESSED or decision.send_request is None:
                continue
            # `send_request` viaja como dict (es la carga JSON de Celery); el
            # worker real hace exactamente este `from_dict` al recibirla.
            request = SendRequest.from_dict(decision.send_request)
            delivered.append(await _send_notification(request, settings))
        return True

    monkeypatch.setattr(
        "api_server.routers.internal_alerts.enqueue_event_dispatch", _dispatch_in_process
    )
    return delivered


async def _mint_system_admin_token(user_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(sid, user_id=user_id, tenant_id=None, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=None, is_system_admin=True)


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# El test del plan
# ===========================================================================
@pytest.mark.asyncio
async def test_a_synthetic_alert_becomes_a_notification_the_system_admin_can_read(
    configured_app, dispatcher_settings, migrations_pg_dsn: str, monkeypatch
) -> None:
    seeded = await _seed_platform_channel(migrations_pg_dsn)
    delivered = _bridge_the_broker(monkeypatch, dispatcher_settings)

    async with _client(configured_app) as client:
        ingest = await client.post(
            "/internal/alerts/ingest",
            json=_webhook(),
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
    assert ingest.status_code == 200, ingest.text
    assert ingest.json()["accepted"] == 1

    # 1. La cadena entregó algo (si `infra_alert` no estuviera en el registro
    #    del dispatcher, el plan sería no_op y esto estaría vacío EN SILENCIO).
    assert delivered, "la alerta no produjo ningún envío: el fan-out murió por el camino"
    assert delivered[0]["status"] == "sent", delivered[0]

    # 2. Quedó persistida como notificación platform-scoped.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT tenant_id, event_type, channel_type, status, subject, body"
            " FROM notification_logs WHERE event_type = 'infra_alert'"
        )
    finally:
        await conn.close()

    assert row is not None, "no se creó la fila de notificación en BD"
    assert row["tenant_id"] is None, "una alerta de infraestructura no pertenece a ningún tenant"
    assert row["channel_type"] == "in_app"
    assert row["status"] == "sent"
    # 3. Y el mensaje dice QUÉ pasó. Sin plantilla registrada las dos columnas
    #    llegarían vacías (AUD16-11), y una notificación que solo dice «pasó un
    #    infra_alert» es casi tan inútil como ninguna.
    rendered = f"{row['subject']} {row['body']}"
    assert "ServiceDown" in rendered, rendered
    assert "api-server:8000" in rendered, rendered

    # 4. Lo único que de verdad importa: el System Admin la VE en su bandeja.
    token = await _mint_system_admin_token(seeded["system_admin"])
    async with _client(configured_app) as client:
        inbox = await client.get(
            "/notifications/platform/logs", headers={"Authorization": f"Bearer {token}"}
        )

    assert inbox.status_code == 200, inbox.text
    body = inbox.json()
    assert body["total"] == 1, body
    assert body["unread"] == 1
    assert body["items"][0]["event_type"] == "infra_alert"


@pytest.mark.asyncio
async def test_the_resolved_alert_also_reaches_the_inbox(
    configured_app, dispatcher_settings, migrations_pg_dsn: str, monkeypatch
) -> None:
    """El «ya se arregló» es tan operativo como el «se rompió».

    Sin él, el operador que recibió `ServiceDown` a las 3:00 no tiene forma de
    saber que a las 3:05 el servicio volvió, y acude a un incidente cerrado.
    """
    await _seed_platform_channel(migrations_pg_dsn)
    delivered = _bridge_the_broker(monkeypatch, dispatcher_settings)

    firing = _webhook(fingerprint="e2e-resolved")
    resolved = _webhook(fingerprint="e2e-resolved")
    resolved["status"] = "resolved"
    resolved["alerts"][0]["status"] = "resolved"

    async with _client(configured_app) as client:
        headers = {"Authorization": f"Bearer {_TOKEN}"}
        first = await client.post("/internal/alerts/ingest", json=firing, headers=headers)
        # Un repeat del MISMO estado se deduplica…
        repeat = await client.post("/internal/alerts/ingest", json=firing, headers=headers)
        # …pero la transición firing→resolved es información nueva y pasa.
        second = await client.post("/internal/alerts/ingest", json=resolved, headers=headers)

    assert first.json()["accepted"] == 1
    assert repeat.json()["deduped"] == 1
    assert second.json()["accepted"] == 1
    assert len(delivered) == 2, f"esperaba firing + resolved, hubo {len(delivered)}"

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM notification_logs WHERE event_type = 'infra_alert'"
        )
    finally:
        await conn.close()
    assert count == 2


@pytest.mark.asyncio
async def test_without_a_platform_channel_nothing_is_delivered(
    configured_app, dispatcher_settings, migrations_pg_dsn: str, monkeypatch
) -> None:
    """La contraprueba, para que el test de arriba no pueda pasar por accidente.

    Si borrando el canal siguiera «entregándose» algo, es que el test no está
    midiendo la cadena real sino otra cosa.
    """
    await _seed_platform_channel(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("DELETE FROM notification_channels")
    finally:
        await conn.close()

    delivered = _bridge_the_broker(monkeypatch, dispatcher_settings)

    async with _client(configured_app) as client:
        resp = await client.post(
            "/internal/alerts/ingest",
            json=_webhook(fingerprint="e2e-nochannel"),
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )

    # El endpoint acepta igual (su trabajo es encolar, no entregar)…
    assert resp.status_code == 200
    # …pero sin canal no hay entrega ni fila.
    assert delivered == []
