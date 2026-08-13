"""`/auth/register` con límite por IP (authz-6).

Por qué importa MÁS ahora que antes: desde el ADR 0134 (opción C) el registro
está cerrado y se entra con un token de invitación. Eso convierte
`/auth/register` en el único endpoint anónimo contra el que se puede probar
un secreto en bucle — un oráculo de adivinación de tokens sin coste. `login`
lleva ventana deslizante desde el principio; `register` no llevaba ninguna.

Lo que estos tests fijan:

  1. Un chorro de intentos desde la misma IP acaba en **429** con
     `Retry-After`, y el 429 llega ANTES de tocar la base de datos (el
     presupuesto se gasta con tokens inválidos, que es el caso del atacante).
  2. La ventana es **por IP**: otra IP conserva su presupuesto entero. Sin
     esto, un atacante detrás de una IP podría dejar sin registrar a todo el
     mundo — el límite se convertiría en una denegación de servicio.
  3. La puerta de arranque (tabla `users` vacía) también cuenta: no es una
     excepción por la que colar tráfico ilimitado.

Pre-condición: postgres y redis del docker-compose sanos en el host.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_LIMIT = 3
_WINDOW = 900


async def _reset(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE user_invitations, user_org_memberships, users, organizations"
            " RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _count_users(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await conn.fetchval("SELECT count(*) FROM users"))
    finally:
        await conn.close()


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    migrations_pg_dsn: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """Misma forma que `test_auth_invitations.configured_app`, con el
    presupuesto de registro bajado a 3 para que el test no tenga que emitir
    cientos de peticiones (cada alta real cuesta un argon2 de 64 MiB)."""
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_REGISTER_RATE_LIMIT_COUNT", str(_LIMIT))
    monkeypatch.setenv("API_SERVER_REGISTER_RATE_LIMIT_WINDOW_SECONDS", str(_WINDOW))

    from prometheus_client import CollectorRegistry

    monkeypatch.setattr(
        "api_server.metrics.get_default_registry",
        CollectorRegistry,
    )

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


def _client(app, ip: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Forwarded-For": ip},
    )


def _payload(email: str) -> dict[str, str]:
    return {
        "email": email,
        "password": "longenoughpw",
        "invitation_token": f"aainv_{uuid4().hex[:8]}_made-up",
    }


@pytest.mark.asyncio
async def test_register_returns_429_once_the_per_ip_budget_is_spent(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _reset(migrations_pg_dsn)
    async with _client(configured_app, "203.0.113.7") as client:
        first = await client.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        assert first.status_code == 201, first.text

        # Presupuesto: _LIMIT peticiones. La primera ya se gastó una.
        statuses = [
            (
                await client.post("/auth/register", json=_payload(f"probe{i}@example.com"))
            ).status_code
            for i in range(_LIMIT)
        ]
        last = await client.post("/auth/register", json=_payload("probe-final@example.com"))

    assert statuses[: _LIMIT - 1] == [403] * (_LIMIT - 1), statuses
    assert statuses[-1] == 429, f"la petición {_LIMIT + 1} debía agotar el presupuesto: {statuses}"
    assert last.status_code == 429, last.text
    assert last.headers.get("Retry-After") == str(_WINDOW)
    # Nada se ha creado: el 429 corta antes del alta.
    assert await _count_users(migrations_pg_dsn) == 1


@pytest.mark.asyncio
async def test_the_budget_is_per_ip_so_one_abuser_cannot_lock_everyone_out(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un límite global convertiría la protección en la denegación de servicio
    que dice evitar."""
    await _reset(migrations_pg_dsn)
    async with _client(configured_app, "198.51.100.3") as founder:
        seeded = await founder.post(
            "/auth/register",
            json={"email": "founder@example.com", "password": "longenoughpw"},
        )
        assert seeded.status_code == 201, seeded.text

    async with _client(configured_app, "198.51.100.1") as abuser:
        for i in range(_LIMIT + 2):
            await abuser.post("/auth/register", json=_payload(f"burn{i}@example.com"))
        exhausted = await abuser.post("/auth/register", json=_payload("burn-last@example.com"))

    assert exhausted.status_code == 429, exhausted.text

    async with _client(configured_app, "198.51.100.2") as newcomer:
        resp = await newcomer.post("/auth/register", json=_payload("newcomer@example.com"))

    # 403 = la respuesta normal del registro cerrado. Lo que NO puede ser es
    # 429: eso significaría que el presupuesto del abusón es el de todos.
    assert resp.status_code == 403, resp.text
    assert resp.status_code != 429


@pytest.mark.asyncio
async def test_the_bootstrap_gate_is_rate_limited_too(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Con `users` vacía el registro está abierto: si esa puerta no contase
    contra el presupuesto, bastaría con no traer token para tener un endpoint
    anónimo ilimitado que además escribe filas."""
    await _reset(migrations_pg_dsn)
    async with _client(configured_app, "192.0.2.99") as client:
        statuses = []
        for i in range(_LIMIT + 1):
            resp = await client.post(
                "/auth/register",
                json={"email": f"boot{i}@example.com", "password": "longenoughpw"},
            )
            statuses.append(resp.status_code)

    assert statuses[0] == 201, statuses
    assert statuses[-1] == 429, f"la puerta de arranque esquivó el límite: {statuses}"
