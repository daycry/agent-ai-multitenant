"""Forkear DOS VECES al mismo destino da 409, no 500.

## El defecto

La migración 0126 (2026-07-30) puso dos índices únicos parciales sobre `agents`:

    uq_agents_tenant_project_name_live  (tenant_id, project_id, name)
                                        WHERE deleted_at IS NULL AND project_id IS NOT NULL
    uq_agents_tenant_name_global_live   (tenant_id, name)
                                        WHERE deleted_at IS NULL AND project_id IS NULL

Son la regla de negocio correcta —un espacio de nombres por tenant/proyecto— y no
se tocan. El problema era el otro lado: tres endpoints crean un agente HEREDANDO
el nombre del origen cuando no se les da uno, y nadie atrapaba la `IntegrityError`
del índice. Resultado: forkear dos veces la misma plantilla al mismo destino
reventaba con un **500**, que para quien usa la UI es indistinguible de «la
plataforma se ha roto» cuando lo que ha pasado es que ese nombre ya está cogido.

Los tres sitios, y el índice en el que cae cada uno:

  * `POST /agents/{id}/fork`                    → índice de PROYECTO
    (el fork es `project_local`, `project_id` no nulo)
  * `POST /teams/{id}/adopt`                    → índice de PROYECTO o de TENANT
    según `target`, y por partida doble: el nombre del EQUIPO y el de cada
    MIEMBRO forkeado
  * `POST /human-agents/templates/{id}/clone`   → índice de TENANT
    (la copia es `global_tenant_template`, `project_id` nulo)

## Lo que se fija aquí

  * el segundo intento devuelve **409**, no 500 ni una `IntegrityError` cruda;
  * el cuerpo trae el código de dominio estable `duplicate_agent_name` y un
    `suggested_name` LIBRE — la decisión del operador es que la API NO
    auto-renombra (el nombre es identidad: por él se eligen agentes en los
    `role_map` y al montar equipos), pero sí pone la sugerencia delante del
    usuario para que la UI la ofrezca editable;
  * el `suggested_name` funciona de verdad: reenviar el fork con ese nombre da
    201. Sin esta comprobación la sugerencia podría ser un nombre igual de
    ocupado y el test seguiría verde;
  * el mensaje distingue los DOS índices en términos de usuario («en este
    proyecto» vs «en este tenant»), sin filtrar el nombre del índice —lo que
    `_integrity` existe para impedir.
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

_TRUNCATE = (
    "TRUNCATE agent_skills, agent_tools, team_members, teams, human_agent_config,"
    " agents, projects, user_org_memberships, organizations, users"
    " RESTART IDENTITY CASCADE"
)


async def _seed(dsn: str) -> dict[str, UUID]:
    """Un tenant con proyecto + tres orígenes forkeables del tenant plataforma.

    Los tres son `global_builtin` (visibles a cualquier tenant vía la policy de
    SELECT), que es el caso real: lo que un tenant forkea es catálogo.
    """
    tenant = uuid4()
    user = uuid4()
    project = uuid4()
    ai_builtin = uuid4()
    human_template = uuid4()
    team_member_agent = uuid4()
    builtin_team = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_TRUNCATE)
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'A', 'tenant-a'),"
            " ($2, 'Platform', 'platform')",
            tenant,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'x')",
            user,
            "a@a.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            user,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Proyecto A')",
            project,
            tenant,
        )
        # Origen del fork de agente IA.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config,"
            " scope, project_id) VALUES ($1, $2, 'Built-in PM', 'project_manager',"
            " 'eres un PM', '{}'::jsonb, 'global_builtin', NULL)",
            ai_builtin,
            _PLATFORM_TENANT_ID,
        )
        # Origen del clone de plantilla HUMANA.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config,"
            " scope, project_id, agent_type, is_template) VALUES ($1, $2, 'Diseñador UX',"
            " 'reviewer', 'eres UX', '{}'::jsonb, 'global_builtin', NULL, 'human', true)",
            human_template,
            _PLATFORM_TENANT_ID,
        )
        # Equipo built-in con UN miembro: suficiente para que la adopción tenga
        # que forkear un agente y choque con el índice a la segunda.
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config,"
            " scope, project_id) VALUES ($1, $2, 'Built-in Backend', 'backend_dev',"
            " 'eres backend', '{}'::jsonb, 'global_builtin', NULL)",
            team_member_agent,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'Equipo Base', true)",
            builtin_team,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO team_members (tenant_id, team_id, agent_id, role_in_team)"
            " VALUES ($1, $2, $3, 'backend_dev')",
            _PLATFORM_TENANT_ID,
            builtin_team,
            team_member_agent,
        )
    finally:
        await conn.close()

    return {
        "tenant": tenant,
        "user": user,
        "project": project,
        "ai_builtin": ai_builtin,
        "human_template": human_template,
        "builtin_team": builtin_team,
        "team_member_agent": team_member_agent,
    }


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


def _detail(body: Any) -> dict[str, Any]:
    """El dict del 409 (`{"error", "message", ...}`), o {} si no lo es."""
    detail = body.get("detail") if isinstance(body, dict) else None
    return detail if isinstance(detail, dict) else {}


def _assert_agent_name_conflict(response: Any, *, scope_word: str, name: str) -> dict[str, Any]:
    """Las cuatro cosas que exige la decisión del operador, en un sitio."""
    assert response.status_code == 409, (
        f"se esperaba 409 y llegó {response.status_code}. Un 500 aquí es la"
        f" IntegrityError de los índices de la 0126 escapando sin traducir."
        f" Cuerpo: {response.text[:500]}"
    )
    detail = _detail(response.json())
    assert detail.get("error") == "duplicate_agent_name", (
        f"el 409 no trae el código de dominio estable; trae {detail!r}"
    )
    message = str(detail.get("message", ""))
    assert name in message, f"el mensaje no dice QUÉ nombre choca: {message!r}"
    other_word = "tenant" if scope_word == "proyecto" else "proyecto"
    assert f"en este {scope_word}" in message and f"en este {other_word}" not in message, (
        f"el mensaje no distingue en qué espacio de nombres choca (esperaba"
        f" «en este {scope_word}» para saber si fue el índice de proyecto o el de"
        f" tenant): {message!r}"
    )
    assert detail.get("namespace") == ("project" if scope_word == "proyecto" else "tenant"), (
        f"el `namespace` del 409 no corresponde al índice violado: {detail!r}"
    )
    assert "duplicate key value" not in response.text and "uq_agents" not in response.text, (
        "el error crudo de PostgreSQL (o el nombre del índice) llegó al cliente"
    )
    suggestion = detail.get("suggested_name")
    assert isinstance(suggestion, str) and suggestion and suggestion != name, (
        f"el 409 no trae un `suggested_name` distinto del que choca: {detail!r}."
        " La decisión es no auto-renombrar, pero sí poner la sugerencia delante"
        " del usuario para que la UI la ofrezca editable"
    )
    return detail


# ===========================================================================
# 1) POST /agents/{id}/fork  -- índice de PROYECTO
# ===========================================================================
@pytest.mark.asyncio
async def test_forking_the_same_agent_twice_into_a_project_is_409(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user"], ids["tenant"])
    body = {"project_id": str(ids["project"])}

    async with _client(configured_app, token) as client:
        first = await client.post(f"/agents/{ids['ai_builtin']}/fork", json=body)
        assert first.status_code == 201, first.text
        assert first.json()["name"] == "Built-in PM", "el fork hereda el nombre del origen"

        second = await client.post(f"/agents/{ids['ai_builtin']}/fork", json=body)
        detail = _assert_agent_name_conflict(second, scope_word="proyecto", name="Built-in PM")

        # La sugerencia tiene que SERVIR: con ella el fork entra.
        third = await client.post(
            f"/agents/{ids['ai_builtin']}/fork",
            json={**body, "name": detail["suggested_name"]},
        )

    assert third.status_code == 201, (
        f"el `suggested_name` que la API propuso ({detail['suggested_name']!r}) no"
        f" era libre: {third.status_code} {third.text[:300]}"
    )
    assert third.json()["name"] == detail["suggested_name"]


@pytest.mark.asyncio
async def test_forking_the_same_agent_into_another_project_still_works(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El índice es por (tenant, proyecto): el mismo nombre en OTRO proyecto es
    legal, y un 409 de más aquí sería una regresión funcional silenciosa."""
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user"], ids["tenant"])
    other_project = uuid4()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Proyecto B')",
            other_project,
            ids["tenant"],
        )
    finally:
        await conn.close()

    async with _client(configured_app, token) as client:
        first = await client.post(
            f"/agents/{ids['ai_builtin']}/fork", json={"project_id": str(ids["project"])}
        )
        assert first.status_code == 201, first.text
        second = await client.post(
            f"/agents/{ids['ai_builtin']}/fork", json={"project_id": str(other_project)}
        )

    assert second.status_code == 201, (
        f"el mismo nombre en otro proyecto del tenant debía colar: {second.status_code}"
        f" {second.text[:300]}"
    )


# ===========================================================================
# 2) POST /human-agents/templates/{id}/clone  -- índice de TENANT
# ===========================================================================
@pytest.mark.asyncio
async def test_cloning_the_same_human_template_twice_is_409(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """La copia es `global_tenant_template` con `project_id` NULO, así que cae en
    el índice GLOBAL del tenant, no en el de proyecto."""
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user"], ids["tenant"])
    path = f"/human-agents/templates/{ids['human_template']}/clone"

    async with _client(configured_app, token) as client:
        first = await client.post(path, json={})
        assert first.status_code == 201, first.text
        assert first.json()["name"] == "Diseñador UX"

        second = await client.post(path, json={})
        detail = _assert_agent_name_conflict(second, scope_word="tenant", name="Diseñador UX")

        third = await client.post(path, json={"name": detail["suggested_name"]})

    assert third.status_code == 201, (
        f"el `suggested_name` propuesto ({detail['suggested_name']!r}) no era libre:"
        f" {third.status_code} {third.text[:300]}"
    )


# ===========================================================================
# 3) POST /teams/{id}/adopt  -- el equipo Y sus miembros forkeados
# ===========================================================================
@pytest.mark.asyncio
async def test_adopting_the_same_team_twice_is_409_on_the_team_name(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Primer choque de la re-adopción: el nombre del EQUIPO
    (`uq_teams_tenant_name_live`, también de la 0126)."""
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user"], ids["tenant"])
    body = {"target": "project", "project_id": str(ids["project"])}

    async with _client(configured_app, token) as client:
        first = await client.post(f"/teams/{ids['builtin_team']}/adopt", json=body)
        assert first.status_code == 201, first.text
        assert first.json()["name"] == "Equipo Base", "la adopción hereda el nombre del origen"

        second = await client.post(f"/teams/{ids['builtin_team']}/adopt", json=body)

    assert second.status_code == 409, (
        f"se esperaba 409 por nombre de equipo repetido y llegó {second.status_code}:"
        f" {second.text[:400]}"
    )
    assert _detail(second.json()).get("error") == "duplicate_team_name", second.text


@pytest.mark.asyncio
async def test_adopting_the_same_team_twice_is_409_on_the_member_agent(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Segundo choque, el que estaba tapado: con el equipo renombrado, el que
    revienta es el AGENTE miembro, que se forkea con el nombre del origen y no
    hay forma de renombrarlo en la petición."""
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user"], ids["tenant"])

    async with _client(configured_app, token) as client:
        first = await client.post(
            f"/teams/{ids['builtin_team']}/adopt",
            json={"target": "project", "project_id": str(ids["project"])},
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            f"/teams/{ids['builtin_team']}/adopt",
            json={
                "target": "project",
                "project_id": str(ids["project"]),
                "name": "Equipo Base (segundo)",
            },
        )

    _assert_agent_name_conflict(second, scope_word="proyecto", name="Built-in Backend")


# ===========================================================================
# 4) POST /human-agents  -- alta directa con un nombre ya usado
# ===========================================================================
@pytest.mark.asyncio
async def test_creating_a_human_agent_with_a_taken_name_is_409(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """No hereda el nombre (lo da el usuario), pero compartía el defecto: el
    `session.flush()` desnudo convertía el nombre repetido en un 500."""
    ids = await _seed(migrations_pg_dsn)
    token = await _token(ids["user"], ids["tenant"])
    payload = {
        "name": "Revisor Humano",
        "role": "reviewer",
        "system_prompt": "revisas",
        "config": {},
    }

    async with _client(configured_app, token) as client:
        first = await client.post("/human-agents", json=payload)
        assert first.status_code == 201, first.text

        second = await client.post("/human-agents", json=payload)

    _assert_agent_name_conflict(second, scope_word="tenant", name="Revisor Humano")
