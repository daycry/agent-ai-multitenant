"""prod-14 · task_prod14_08 — el directorio de usuarios no se filtra entre tenants.

`users` es una tabla GLOBAL: no tiene `tenant_id` y por tanto NO tiene RLS. Es
deliberado —el login ocurre antes de saber a qué tenant pertenece nadie, y una
persona puede ser miembro de varios— pero deja el aislamiento del directorio en
manos de la disciplina de cada query: el anclaje correcto es
`JOIN user_org_memberships` filtrado por el tenant del llamante, y la RLS **no
está ahí para atrapar al que se olvide**. Eso es exactamente el tipo de
invariante que se rompe en silencio en un refactor.

El ADR 0137 inventarió y clasificó todos los `select(User…)`; esto es la otra
mitad: la guarda EJECUTABLE que impide que la clasificación envejezca. Se prueban
las dos superficies de contexto-tenant que devuelven personas:

  * `GET /human-agents/assignable-users` — el selector de asignación de la UI;
  * la resolución de usuario del asistente (`_resolve_tenant_user`), que
    traduce «asígnaselo a Marta» a un `user_id`.

Y en las dos se comprueba lo mismo desde dos ángulos, porque uno solo no basta:

  1. el listado del tenant A NO contiene a nadie de B (lo obvio);
  2. una búsqueda por el email EXACTO de alguien de B no lo encuentra. Esto es lo
     que de verdad importa: un listado puede estar bien paginado y filtrado y aun
     así el lookup dirigido revelar la existencia de una cuenta ajena — que es la
     enumeración de correos de toda la organización.

El PII del usuario NO se compara solo por id: se afirma que su EMAIL no aparece
en el cuerpo de la respuesta. Un id filtrado es feo; un correo filtrado es un
incidente de protección de datos.
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

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_EMAIL_A = "alice.tenant.a@example.test"
_EMAIL_B = "bruno.tenant.b@example.test"
_NAME_B = "Bruno Solo De B"


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a, tenant_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE human_agent_config, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'A', 'tenant-a'),"
            " ($2, 'B', 'tenant-b')",
            tenant_a,
            tenant_b,
        )
        await conn.execute(
            "INSERT INTO users (id, email, full_name, password_hash) VALUES"
            " ($1, $2, 'Alice De A', 'x'), ($3, $4, $5, 'x')",
            user_a,
            _EMAIL_A,
            user_b,
            _EMAIL_B,
            _NAME_B,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_a,
            user_a,
            uuid4(),
            tenant_b,
            user_b,
        )
    finally:
        await conn.close()
    return {"tenant_a": tenant_a, "tenant_b": tenant_b, "user_a": user_a, "user_b": user_b}


@pytest.fixture()
def configured_app(
    alembic_config: Any,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


def _client(app: Any, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


# ===========================================================================
# 1. El selector de asignación de la UI
# ===========================================================================
@pytest.mark.asyncio
async def test_assignable_users_never_lists_a_member_of_another_tenant(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)

    async with _client(configured_app, await _token(ids["user_a"], ids["tenant_a"])) as client:
        response = await client.get("/human-agents/assignable-users")

    assert response.status_code == 200, response.text
    returned = {row["user_id"] for row in response.json()}

    # La guarda contra el paso en vacío: si el listado viniera vacío por
    # cualquier motivo (RBAC, paginación, un seed roto), la aserción de no-fuga
    # pasaría sin haber comprobado nada.
    assert str(ids["user_a"]) in returned, (
        "el listado no trae ni al propio miembro del tenant: este test estaría"
        f" pasando en vacío. Devolvió {returned!r}"
    )
    assert str(ids["user_b"]) not in returned, (
        "el directorio de usuarios se filtra: el tenant A ve al usuario del tenant B"
    )
    assert _EMAIL_B not in response.text, (
        f"el correo de un usuario de otro tenant ({_EMAIL_B}) apareció en la"
        " respuesta: eso es enumeración del directorio de la organización"
    )


@pytest.mark.asyncio
async def test_each_tenant_sees_only_its_own_directory(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """La simétrica. Sin ella, un filtro que devolviera SIEMPRE el mismo tenant
    (un `tenant_id` mal cableado, p. ej. el primero de la tabla) pasaría el test
    de arriba."""
    ids = await _seed(migrations_pg_dsn)

    async with _client(configured_app, await _token(ids["user_b"], ids["tenant_b"])) as client:
        response = await client.get("/human-agents/assignable-users")

    assert response.status_code == 200, response.text
    returned = {row["user_id"] for row in response.json()}
    assert returned == {str(ids["user_b"])}, (
        f"el tenant B debería ver exactamente a su único miembro; vio {returned!r}"
    )
    assert _EMAIL_A not in response.text


# ===========================================================================
# 2. La resolución de usuario del asistente
# ===========================================================================
@pytest.mark.asyncio
async def test_the_assistant_cannot_resolve_a_user_from_another_tenant(
    configured_app: Any, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Ni por email exacto ni por nombre completo.

    Es el lookup DIRIGIDO, el que de verdad permite enumerar: preguntar por un
    correo concreto y ver si «existe» delata la cuenta aunque ningún listado la
    muestre nunca.
    """
    from api_server.assistant.tools import _resolve_tenant_user
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    ids = await _seed(migrations_pg_dsn)

    engine = create_async_engine(app_database_url)
    try:
        session = async_sessionmaker(engine, expire_on_commit=False)()
        try:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(ids["tenant_a"])},
            )
            ctx = _AssistantCtx(session=session, tenant_id=ids["tenant_a"])

            own_id, own_matches = await _resolve_tenant_user(ctx, _EMAIL_A)
            foreign_email_id, foreign_email_matches = await _resolve_tenant_user(ctx, _EMAIL_B)
            foreign_name_id, foreign_name_matches = await _resolve_tenant_user(ctx, _NAME_B)
        finally:
            await session.close()
    finally:
        await engine.dispose()

    # Control de que la resolución FUNCIONA; si no, los tres asserts de no-fuga
    # serían ciertos por accidente.
    assert own_id == ids["user_a"], (
        "la resolución no encuentra ni al usuario del propio tenant"
        f" ({own_id!r}, candidatos {own_matches!r}): el test pasaría en vacío"
    )

    assert foreign_email_id is None and not foreign_email_matches, (
        "el asistente resolvió por EMAIL EXACTO a un usuario de otro tenant"
        f" ({foreign_email_id!r} / {foreign_email_matches!r}): es enumeración de"
        " cuentas del directorio global"
    )
    assert foreign_name_id is None and not foreign_name_matches, (
        "el asistente resolvió por NOMBRE a un usuario de otro tenant"
        f" ({foreign_name_id!r} / {foreign_name_matches!r})"
    )


class _AssistantCtx:
    """Lo mínimo que `_resolve_tenant_user` usa de su contexto real.

    Un doble a propósito y no el `ToolContext` de producción: lo que se quiere
    fijar es que la consulta se ancla en `ctx.tenant_id`, y construir el contexto
    completo arrastraría el proveedor LLM y media aplicación a un test de
    aislamiento.
    """

    def __init__(self, *, session: Any, tenant_id: UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id
