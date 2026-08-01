"""prod-14 · task_prod14_10 (y prod-13 · task_prod13_13) — el nombre duplicado
sale como 409 de dominio, no como 500.

La migración 0126 puso los índices únicos parciales `(tenant_id, name)` sobre
`teams`, `skills` y `agents`. Con eso, el segundo intento de crear un nombre ya
usado en el tenant deja de colarse… y empieza a reventar: sin nadie que atrape la
`IntegrityError`, FastAPI la convierte en un **500**. Para quien usa la UI eso es
indistinguible de «la plataforma se ha roto», y encima el operador ha declarado
prioritaria la resolución por nombre en la pantalla de asignación.

Lo que se fija aquí:

  * el segundo POST con el mismo nombre devuelve **409**, no 500 ni 201;
  * el cuerpo del 409 trae un código de dominio estable
    (`duplicate_team_name` / `duplicate_skill_name` / `duplicate_agent_name`),
    no el texto crudo de PostgreSQL — que diría `duplicate key value violates
    unique constraint "uq_teams_tenant_name_live"` y filtraría el esquema;
  * el índice es PARCIAL sobre `deleted_at IS NULL`: reusar el nombre de algo
    borrado sigue permitido, y eso también se comprueba, porque un 409 de más
    ahí sería una regresión funcional silenciosa;
  * el mismo nombre en OTRO tenant se acepta — el índice lleva `tenant_id`
    delante, y sin esa comprobación el test pasaría igual con un índice global
    que sería una fuga de aislamiento al revés.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a, tenant_b = uuid4(), uuid4()
    user_a, user_b = uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE teams, skills, agents, user_org_memberships, organizations,"
            " users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'A', 'tenant-a'),"
            " ($2, 'B', 'tenant-b')",
            tenant_a,
            tenant_b,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x'), ($3, $4, 'x')",
            user_a,
            "a@a.test",
            user_b,
            "b@b.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
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


# Las tres familias, con su payload mínimo y el código de dominio esperado.
_CASES = (
    ("/teams", {"name": "Equipo Único"}, "duplicate_team_name"),
    (
        "/skills",
        {
            "name": "Skill Única",
            "category": "backend",
            "prompt_fragment": "haz cosas",
        },
        "duplicate_skill_name",
    ),
    (
        "/agents",
        {
            "name": "Agente Único",
            "role": "backend_dev",
            "system_prompt": "eres un backend",
            "scope": "global_tenant_template",
        },
        "duplicate_agent_name",
    ),
)


def _detail_code(body: Any) -> str:
    """El código de dominio del 409, venga como dict o como string."""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("error", ""))
    return str(detail or "")


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "payload", "expected_code"), _CASES)
async def test_duplicate_name_in_the_same_tenant_is_a_409_not_a_500(
    configured_app: Any,
    migrations_pg_dsn: str,
    path: str,
    payload: dict[str, Any],
    expected_code: str,
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user_a"], ids["tenant_a"])

    async with _client(configured_app, token) as client:
        first = await client.post(path, json=payload)
        assert first.status_code == 201, first.text

        second = await client.post(path, json=payload)

    assert second.status_code == 409, (
        f"POST {path} con un nombre ya usado devolvió {second.status_code} en vez de"
        f" 409. Un 500 aquí es la IntegrityError del índice de la 0126 escapando sin"
        f" traducir. Cuerpo: {second.text[:400]}"
    )
    body = second.json()
    assert _detail_code(body) == expected_code, (
        f"el 409 no trae el código de dominio esperado ({expected_code}); trae"
        f" {body!r}. Si eso es el texto de PostgreSQL, además filtra el nombre del"
        " índice"
    )
    assert "duplicate key value" not in second.text, (
        "el error crudo de PostgreSQL llegó al cliente: eso es lo que"
        " `_integrity.integrity_conflict` viene a impedir"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "payload", "expected_code"), _CASES)
async def test_the_same_name_in_another_tenant_is_accepted(
    configured_app: Any,
    migrations_pg_dsn: str,
    path: str,
    payload: dict[str, Any],
    expected_code: str,
) -> None:
    """El índice lleva `tenant_id` delante: el nombre es único POR TENANT."""
    ids = await _seed(migrations_pg_dsn)

    async with _client(configured_app, await _token(ids["user_a"], ids["tenant_a"])) as client_a:
        assert (await client_a.post(path, json=payload)).status_code == 201

    async with _client(configured_app, await _token(ids["user_b"], ids["tenant_b"])) as client_b:
        response = await client_b.post(path, json=payload)

    assert response.status_code == 201, (
        f"el tenant B no pudo usar un nombre que solo existe en el tenant A"
        f" ({response.status_code}): el índice dejó de estar acotado por tenant."
        f" Cuerpo: {response.text[:400]}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "payload", "expected_code"), _CASES)
async def test_the_name_of_a_soft_deleted_row_can_be_reused(
    configured_app: Any,
    migrations_pg_dsn: str,
    path: str,
    payload: dict[str, Any],
    expected_code: str,
) -> None:
    """El índice es PARCIAL (`WHERE deleted_at IS NULL`) a propósito."""
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user_a"], ids["tenant_a"])

    async with _client(configured_app, token) as client:
        created = await client.post(path, json=payload)
        assert created.status_code == 201, created.text
        deleted = await client.delete(f"{path}/{created.json()['id']}")
        assert deleted.status_code in (200, 204), deleted.text

        again = await client.post(path, json=payload)

    assert again.status_code == 201, (
        "no se pudo reutilizar el nombre de una fila BORRADA: el índice único dejó"
        f" de ser parcial sobre `deleted_at IS NULL` ({again.status_code}:"
        f" {again.text[:300]})"
    )
