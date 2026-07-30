"""prod-13 · task_prod13_20 + task_prod13_21 — caché de settings y válvula del chat.

Los dos piden Redis y PostgreSQL de verdad: una caché que se prueba con un doble
no prueba nada (el bug de una caché siempre está en la invalidación real), y un
rate limit de ventana deslizante vive dentro de un `ZSET`.

  * **perf-10** — `get_platform_setting` sirve de Redis con TTL corto, y la
    escritura INVALIDA. El test que importa es el de revocación: escribir un
    valor nuevo tiene que verse **de inmediato**, no en 30 s (decisión clave 6 del
    plan: ante la duda gana la frescura).
  * **api-4** — el chat del asistente devuelve 429 a partir del presupuesto, con
    los headers `X-RateLimit-*`, y el cap del TENANT muerde aunque cada usuario
    individual vaya por debajo del suyo.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


@pytest.fixture()
def migrated_db(alembic_config, test_database_url: str, test_redis_url: str) -> None:
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))


@pytest.fixture()
def wired(migrated_db: None, admin_database_url: str, test_redis_url: str, monkeypatch):
    """Apunta la config del api-server a la BD/Redis de prueba y limpia cachés."""
    monkeypatch.setenv("API_SERVER_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    try:
        yield
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _system_admin(dsn: str) -> UUID:
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin)"
            " VALUES ($1, $2, 'h', true)",
            user_id,
            f"sa-{user_id.hex[:8]}@test.local",
        )
    finally:
        await conn.close()
    return user_id


# ---------------------------------------------------------------------------
# perf-10 — caché de platform_settings
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_setting_read_is_served_from_redis_on_the_second_call(
    wired, migrations_pg_dsn: str
) -> None:
    """La segunda lectura NO va a PostgreSQL. Se prueba de la única forma que no
    se puede falsear: se cambia la fila POR DEBAJO (SQL directo, sin pasar por
    `set_platform_setting`) y se comprueba que la lectura sigue devolviendo el
    valor cacheado. Si no hubiera caché, devolvería el nuevo."""
    from api_server.db.platform_settings import get_platform_setting
    from api_server.db.session import get_admin_sessionmaker

    key = f"test.cache.{uuid4().hex[:8]}"
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, to_jsonb($2::text))",
            key,
            "original",
        )
    finally:
        await conn.close()

    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        assert await get_platform_setting(session, key) == "original"

    # Cambio a espaldas de la aplicación: la caché no puede saberlo.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE platform_settings SET value = to_jsonb($2::text) WHERE key = $1",
            key,
            "cambiado-por-detras",
        )
    finally:
        await conn.close()

    async with sessionmaker() as session:
        cached = await get_platform_setting(session, key)
    assert cached == "original", "no hubo caché: la lectura fue a la BD"


@pytest.mark.asyncio
async def test_write_invalidates_immediately_so_a_new_value_is_never_stale(
    wired, migrations_pg_dsn: str
) -> None:
    """El test de revocación. Escribir por la vía legítima tiene que verse en la
    lectura siguiente, sin esperar el TTL — si no, un límite de seguridad
    endurecido tardaría 30 s en aplicarse."""
    from api_server.db.models import User
    from api_server.db.platform_settings import get_platform_setting, set_platform_setting
    from api_server.db.session import get_admin_sessionmaker
    from sqlalchemy import select

    admin_id = await _system_admin(migrations_pg_dsn)
    key = f"test.invalidate.{uuid4().hex[:8]}"
    sessionmaker = get_admin_sessionmaker()

    async with sessionmaker() as session:
        # Cachea el MISS (la clave no existe): el caso que más se beneficia y el
        # que más fácil se queda pegado.
        assert await get_platform_setting(session, key, default="fallback") == "fallback"

    async with sessionmaker() as session, session.begin():
        admin = (await session.execute(select(User).where(User.id == admin_id))).scalar_one()
        await set_platform_setting(session, key, "nuevo", actor=admin)

    async with sessionmaker() as session:
        assert (
            await get_platform_setting(session, key, default="fallback") == "nuevo"
        ), "la caché sirvió el MISS anterior después de escribir el valor"


@pytest.mark.asyncio
async def test_two_callers_with_different_defaults_each_get_their_own(
    wired, migrations_pg_dsn: str
) -> None:
    """Lo que se cachea es SI la fila existe y su valor, no el resultado final.
    Cachear el resultado habría hecho que el primer llamante impusiera su
    `default` a todos los demás para esa clave."""
    from api_server.db.platform_settings import get_platform_setting
    from api_server.db.session import get_admin_sessionmaker

    key = f"test.defaults.{uuid4().hex[:8]}"
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        assert await get_platform_setting(session, key, default=3) == 3
        assert await get_platform_setting(session, key, default=99) == 99


@pytest.mark.asyncio
async def test_reads_still_work_with_redis_unreachable(wired, monkeypatch) -> None:
    """La caché es best-effort: si Redis está caído, la función tiene que ser
    exactamente la de antes, no un 500. Fail-open sobre el CACHÉ (no sobre la
    autorización), que es lo correcto aquí: PostgreSQL sigue siendo la verdad.

    Se apunta la config a un puerto MUERTO en vez de inyectar un doble: `get_redis`
    es `lru_cache`, y sustituirla por un lambda rompería el `cache_clear()` del
    teardown de la fixture. Un puerto cerrado es además el fallo real."""
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db import platform_settings as mod
    from api_server.db.session import get_admin_sessionmaker

    sessionmaker = get_admin_sessionmaker()
    monkeypatch.setenv("API_SERVER_REDIS_URL", "redis://127.0.0.1:1/0")
    get_settings.cache_clear()
    reset_redis_cache()

    async with sessionmaker() as session:
        assert await mod.get_platform_setting(session, "nope", default="fallback") == "fallback"
        # Y una escritura de caché fallida tampoco rompe la siguiente lectura.
        assert await mod.get_platform_setting(session, "nope", default="fallback") == "fallback"
    await mod.invalidate_platform_setting_cache("nope")


# ---------------------------------------------------------------------------
# api-4 — rate limit del chat del asistente
# ---------------------------------------------------------------------------
class _CapturingResponse:
    """Sustituto de `fastapi.Response` que solo necesita `.headers`."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def _principal(user_id: UUID, tenant_id: UUID):
    from api_server.auth.deps import AuthPrincipal

    return AuthPrincipal(user_id=user_id, tenant_id=tenant_id, session_id=uuid4())


@pytest.mark.asyncio
async def test_chat_rate_limit_429s_past_the_per_user_budget(wired, migrations_pg_dsn: str) -> None:
    from api_server.auth.deps import get_redis
    from api_server.db.session import get_admin_sessionmaker
    from api_server.routers.assistant import (
        DEFAULT_ASSISTANT_CHAT_RATE_LIMIT,
        enforce_assistant_chat_rate_limit,
    )
    from fastapi import HTTPException

    user_id, tenant_id = uuid4(), uuid4()
    principal = _principal(user_id, tenant_id)
    redis = get_redis()

    async with get_admin_sessionmaker()() as session:
        # Justo hasta el presupuesto: todas pasan.
        for i in range(DEFAULT_ASSISTANT_CHAT_RATE_LIMIT):
            response = _CapturingResponse()
            await enforce_assistant_chat_rate_limit(response, principal, session, redis)
            assert response.headers["X-RateLimit-Limit"] == str(DEFAULT_ASSISTANT_CHAT_RATE_LIMIT)
            assert int(response.headers["X-RateLimit-Remaining"]) == (
                DEFAULT_ASSISTANT_CHAT_RATE_LIMIT - i - 1
            )

        # La siguiente se pasa.
        with pytest.raises(HTTPException) as excinfo:
            await enforce_assistant_chat_rate_limit(_CapturingResponse(), principal, session, redis)

    exc = excinfo.value
    assert exc.status_code == 429
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "assistant_chat_rate_limited"
    assert exc.detail["scope"] == "user"
    assert exc.headers is not None
    assert int(exc.headers["Retry-After"]) >= 1
    assert exc.headers["X-RateLimit-Remaining"] == "0"


@pytest.mark.asyncio
async def test_tenant_cap_bites_even_when_no_single_user_is_over(
    wired, migrations_pg_dsn: str
) -> None:
    """El cap del tenant es el que impide que N usuarios sumen N veces el límite
    individual. Se baja por platform setting para no tener que hacer 120
    llamadas, lo que además prueba que el setting SE LEE."""
    from api_server.auth.deps import get_redis
    from api_server.db.models import User
    from api_server.db.platform_settings import set_platform_setting
    from api_server.db.session import get_admin_sessionmaker
    from api_server.routers.assistant import (
        ASSISTANT_CHAT_TENANT_RATE_LIMIT_KEY,
        enforce_assistant_chat_rate_limit,
    )
    from fastapi import HTTPException
    from sqlalchemy import select

    admin_id = await _system_admin(migrations_pg_dsn)
    tenant_id = uuid4()
    redis = get_redis()
    sessionmaker = get_admin_sessionmaker()

    async with sessionmaker() as session, session.begin():
        admin = (await session.execute(select(User).where(User.id == admin_id))).scalar_one()
        await set_platform_setting(session, ASSISTANT_CHAT_TENANT_RATE_LIMIT_KEY, 3, actor=admin)

    async with sessionmaker() as session:
        # Tres usuarios DISTINTOS, uno cada uno: nadie se pasa de su límite
        # individual (20), pero el tenant llega a su tope de 3.
        for _ in range(3):
            await enforce_assistant_chat_rate_limit(
                _CapturingResponse(), _principal(uuid4(), tenant_id), session, redis
            )

        with pytest.raises(HTTPException) as excinfo:
            await enforce_assistant_chat_rate_limit(
                _CapturingResponse(), _principal(uuid4(), tenant_id), session, redis
            )

    assert excinfo.value.status_code == 429
    assert isinstance(excinfo.value.detail, dict)
    assert excinfo.value.detail["scope"] == "tenant"


@pytest.mark.asyncio
async def test_a_broken_setting_falls_back_to_the_default_instead_of_disabling_the_valve(
    wired, migrations_pg_dsn: str
) -> None:
    """`0`, negativo o texto en el platform setting NO puede desactivar el límite
    en silencio: sería una forma de apagar la válvula con un typo."""
    from api_server.routers.assistant import DEFAULT_ASSISTANT_CHAT_RATE_LIMIT, _positive_int

    for broken in (0, -5, "muchos", None, "", [], {}):
        assert (
            _positive_int(broken, default=DEFAULT_ASSISTANT_CHAT_RATE_LIMIT)
            == DEFAULT_ASSISTANT_CHAT_RATE_LIMIT
        ), broken
    # Un valor legítimo sí manda.
    assert _positive_int(7, default=DEFAULT_ASSISTANT_CHAT_RATE_LIMIT) == 7
    assert _positive_int("7", default=DEFAULT_ASSISTANT_CHAT_RATE_LIMIT) == 7


@pytest.mark.asyncio
async def test_stream_endpoint_copies_the_rate_limit_headers_into_its_response() -> None:
    """El `Response` inyectado se descarta cuando el handler devuelve su propio
    objeto de respuesta. En `/chat/stream` los headers de rate limit tenían que
    copiarse a mano o el cliente del stream nunca los recibiría — un mecanismo
    entregado que nadie ve (apartado 5 de verificar-antes-de-implementar)."""
    from pathlib import Path

    source = Path("apps/api-server/src/api_server/routers/assistant.py").read_text(encoding="utf-8")
    assert (
        "**dict(response.headers)," in source
    ), "/chat/stream dejó de propagar los headers X-RateLimit-* a su StreamingResponse"
