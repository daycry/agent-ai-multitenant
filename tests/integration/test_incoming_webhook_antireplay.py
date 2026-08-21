"""Anti-replay del webhook entrante (authz-5).

El agujero que cierra: `incoming_webhook_events` tiene un índice único
**parcial** sobre `(config_id, delivery_id) WHERE delivery_id IS NOT NULL`. Un
emisor `generic` no manda cabecera de entrega, así que la fila se guardaba con
`delivery_id = NULL`, el índice no aplicaba y **la misma petición firmada podía
reproducirse infinitas veces**: cada repetición creaba un evento nuevo y
volvía a ejecutar la acción mapeada. Una entrega capturada de la red valía
como pulsador infinito.

Lo que se fija aquí:

  1. Un `generic` sin cabecera de entrega se guarda con clave derivada del
     **cuerpo** (nunca NULL), así que la segunda copia byte a byte se responde
     `duplicate` y NO crea un segundo evento.
  2. La derivación no rompe la semántica de quien SÍ manda id de entrega: dos
     entregas distintas con el mismo cuerpo entran las dos.
  3. La cabecera de entrega sigue mandando cuando existe (el reintento de
     GitHub sigue siendo idempotente por su id, no por el cuerpo).
  4. Ventana de frescura: una marca de tiempo rancia o futura se rechaza con
     401 y no persiste nada; sin cabecera, el flujo sigue como siempre.

Pre-condición: postgres y redis del docker-compose sanos en el host.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_SECRET = "s3cret-signing-key-antireplay"  # - fixture de test, no es un secreto real
_GENERIC_SIG_HEADER = "X-Signature-256"
_TIMESTAMP_HEADER = "X-Agentic-Timestamp"


# ---------------------------------------------------------------------------
# Semillas (BYPASSRLS, DSN de migraciones) — mismo patrón que
# tests/integration/test_webhook_signature.py
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_project(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_config(dsn: str, *, tenant_id: UUID, project_id: UUID) -> UUID:
    from api_server.webhooks.secrets import encrypt_signing_secret

    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO incoming_webhook_configs "
            "(id, tenant_id, project_id, origin, name, signing_secret_encrypted, enabled) "
            "VALUES ($1, $2, $3, 'generic', 'generic-config', $4, true)",
            config_id,
            tenant_id,
            project_id,
            encrypt_signing_secret(_SECRET),
        )
    finally:
        await conn.close()
    return config_id


async def _events(dsn: str, *, config_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return list(
            await conn.fetch(
                "SELECT delivery_id FROM incoming_webhook_events WHERE config_id = $1",
                config_id,
            )
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE incoming_webhook_events, incoming_webhook_configs, "
            "projects, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


def _signature(body: bytes) -> str:
    """El esquema `generic`: hex desnudo, sin prefijo de algoritmo."""
    return hmac.new(_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _setup(dsn: str) -> UUID:
    await _truncate_all(dsn)
    tenant = await _seed_tenant(dsn, slug=f"acme-{uuid4().hex[:6]}")
    project = await _seed_project(dsn, tenant_id=tenant, name="Proj")
    return await _seed_config(dsn, tenant_id=tenant, project_id=project)


# ===========================================================================
# 1. Sin cabecera de entrega: el replay literal se deduplica
# ===========================================================================
@pytest.mark.asyncio
async def test_generic_delivery_without_delivery_header_cannot_be_replayed(
    configured_app, migrations_pg_dsn: str
) -> None:
    config_id = await _setup(migrations_pg_dsn)
    body = json.dumps({"event": "ping", "n": 1}).encode("utf-8")
    headers = {
        _GENERIC_SIG_HEADER: _signature(body),
        "content-type": "application/json",
    }

    async with _client(configured_app) as client:
        first = await client.post(
            f"/webhooks/incoming/generic/{config_id}", content=body, headers=headers
        )
        replay = await client.post(
            f"/webhooks/incoming/generic/{config_id}", content=body, headers=headers
        )

    assert first.status_code == 202, first.text
    assert first.json()["status"] == "accepted"
    # El replay se reconoce, no se ejecuta: mismo evento, sin fila nueva.
    assert replay.status_code == 202, replay.text
    assert replay.json()["status"] == "duplicate", replay.text
    assert replay.json()["event_id"] == first.json()["event_id"]

    rows = await _events(migrations_pg_dsn, config_id=config_id)
    assert len(rows) == 1, f"el replay creó un evento nuevo: {rows}"
    stored = rows[0]["delivery_id"]
    assert stored is not None, "delivery_id NULL vuelve a esquivar el índice único parcial"
    assert stored.startswith("body-sha256:"), stored
    assert stored.endswith(hashlib.sha256(body).hexdigest())


@pytest.mark.asyncio
async def test_a_different_body_is_still_accepted(configured_app, migrations_pg_dsn: str) -> None:
    """La contra-prueba: si la dedup fuese por config y no por cuerpo, el
    segundo evento legítimo también se perdería y el test de arriba pasaría
    igual."""
    config_id = await _setup(migrations_pg_dsn)

    async with _client(configured_app) as client:
        statuses = []
        for n in (1, 2):
            body = json.dumps({"event": "ping", "n": n}).encode("utf-8")
            resp = await client.post(
                f"/webhooks/incoming/generic/{config_id}",
                content=body,
                headers={_GENERIC_SIG_HEADER: _signature(body)},
            )
            statuses.append((resp.status_code, resp.json()["status"]))

    assert statuses == [(202, "accepted"), (202, "accepted")], statuses
    assert len(await _events(migrations_pg_dsn, config_id=config_id)) == 2


# ===========================================================================
# 2. Con cabecera de entrega manda el emisor
# ===========================================================================
@pytest.mark.asyncio
async def test_the_senders_delivery_id_still_wins_over_the_derived_one(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Dos entregas DISTINTAS con cuerpo idéntico tienen que entrar las dos:
    quien identifica su entrega conserva su semántica de reintento y no paga
    la restricción que solo se le impone a quien no la trae."""
    config_id = await _setup(migrations_pg_dsn)
    body = json.dumps({"event": "ping"}).encode("utf-8")

    async with _client(configured_app) as client:
        results = []
        for delivery in ("delivery-A", "delivery-B", "delivery-A"):
            resp = await client.post(
                f"/webhooks/incoming/generic/{config_id}",
                content=body,
                headers={_GENERIC_SIG_HEADER: _signature(body), "X-Request-Id": delivery},
            )
            results.append(resp.json()["status"])

    # A y B entran; la repetición de A se deduplica por SU id, no por el cuerpo.
    assert results == ["accepted", "accepted", "duplicate"], results
    stored = sorted(r["delivery_id"] for r in await _events(migrations_pg_dsn, config_id=config_id))
    assert stored == ["delivery-A", "delivery-B"], stored


# ===========================================================================
# 3. Ventana de frescura
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("label", "offset"),
    [("rancia", -3600), ("futura", 3600)],
)
async def test_a_timestamp_outside_the_window_is_rejected(
    label: str, offset: int, configured_app, migrations_pg_dsn: str
) -> None:
    config_id = await _setup(migrations_pg_dsn)
    body = json.dumps({"event": "ping", "when": label}).encode("utf-8")

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/generic/{config_id}",
            content=body,
            headers={
                _GENERIC_SIG_HEADER: _signature(body),
                _TIMESTAMP_HEADER: str(int(time.time()) + offset),
            },
        )

    assert resp.status_code == 401, resp.text
    assert await _events(migrations_pg_dsn, config_id=config_id) == []


@pytest.mark.asyncio
async def test_a_fresh_timestamp_passes_and_a_missing_one_does_not_break_anything(
    configured_app, migrations_pg_dsn: str
) -> None:
    """La otra mitad de la guarda: sin esto, un rechazo indiscriminado pasaría
    los dos tests de arriba y dejaría el endpoint inservible."""
    config_id = await _setup(migrations_pg_dsn)

    async with _client(configured_app) as client:
        fresh_body = json.dumps({"event": "fresh"}).encode("utf-8")
        fresh = await client.post(
            f"/webhooks/incoming/generic/{config_id}",
            content=fresh_body,
            headers={
                _GENERIC_SIG_HEADER: _signature(fresh_body),
                _TIMESTAMP_HEADER: str(int(time.time())),
            },
        )
        bare_body = json.dumps({"event": "bare"}).encode("utf-8")
        bare = await client.post(
            f"/webhooks/incoming/generic/{config_id}",
            content=bare_body,
            headers={_GENERIC_SIG_HEADER: _signature(bare_body)},
        )

    assert fresh.status_code == 202, fresh.text
    assert bare.status_code == 202, bare.text
    assert len(await _events(migrations_pg_dsn, config_id=config_id)) == 2


@pytest.mark.asyncio
async def test_a_malformed_timestamp_is_rejected_not_ignored(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tratar «no parsea» como «no viene» sería la vía de escape obvia."""
    config_id = await _setup(migrations_pg_dsn)
    body = json.dumps({"event": "ping"}).encode("utf-8")

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/webhooks/incoming/generic/{config_id}",
            content=body,
            headers={
                _GENERIC_SIG_HEADER: _signature(body),
                _TIMESTAMP_HEADER: "not-a-number",
            },
        )

    assert resp.status_code == 401, resp.text
    assert await _events(migrations_pg_dsn, config_id=config_id) == []


@pytest.mark.asyncio
async def test_the_freshness_gate_runs_behind_the_mac_not_in_front_of_it(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Orden del contrato: quien no conoce el secreto recibe SIEMPRE la misma
    respuesta, mande la marca de tiempo que mande. Si la frescura se evaluase
    antes del MAC, un desconocido distinguiría «timestamp malo» de «firma
    mala» y tendría un oráculo gratis."""
    config_id = await _setup(migrations_pg_dsn)
    body = json.dumps({"event": "ping"}).encode("utf-8")

    async with _client(configured_app) as client:
        stale_and_unsigned = await client.post(
            f"/webhooks/incoming/generic/{config_id}",
            content=body,
            headers={
                _GENERIC_SIG_HEADER: "00" * 32,
                _TIMESTAMP_HEADER: str(int(time.time()) - 99_999),
            },
        )
        fresh_and_unsigned = await client.post(
            f"/webhooks/incoming/generic/{config_id}",
            content=body,
            headers={_GENERIC_SIG_HEADER: "00" * 32, _TIMESTAMP_HEADER: str(int(time.time()))},
        )

    assert stale_and_unsigned.status_code == 401
    assert stale_and_unsigned.json() == fresh_and_unsigned.json()
