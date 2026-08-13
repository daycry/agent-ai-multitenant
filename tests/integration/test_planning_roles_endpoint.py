"""`GET /projects/{id}/planning-roles` — a quién se puede @-mencionar (`task_wf_43`).

El compositor del chat hardcodeaba los nueve `PlanningRole` del enum, así que
ofrecía mencionar a especialistas que el equipo del proyecto no tiene. La
mención salía, `pm_decide` la descartaba por no estar en `team_roles` y el turno
quedaba vacío: la afordancia parecía rota. Este endpoint devuelve el equipo
REAL, que es exactamente el conjunto con el que el servidor intersecta.

Cubre: el equipo real, el proyecto sin equipo (solo PM), el descarte de roles
que no son portavoces de planificación, y el aislamiento cross-tenant.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a, tenant_b = uuid4(), uuid4()
    admin_a, admin_b = uuid4(), uuid4()
    project_a, project_solo, project_b = uuid4(), uuid4(), uuid4()
    team_a = uuid4()
    agent_pm, agent_qa, agent_researcher = uuid4(), uuid4(), uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, agents, projects,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'Tenant A', 'tenant-a-roles'), ($2, 'Tenant B', 'tenant-b-roles'),"
            " ($3, 'Platform', 'platform-roles')",
            tenant_a,
            tenant_b,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, 'a@roles.test', 'x'), ($2, 'b@roles.test', 'x')",
            admin_a,
            admin_b,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_a,
            admin_a,
            uuid4(),
            tenant_b,
            admin_b,
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1, $2, 'Squad A')",
            team_a,
            tenant_a,
        )
        # `researcher` NO es un PlanningRole: existe en el equipo pero no es
        # portavoz de planificación, así que no debe poder mencionarse.
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt, model_config)"
            " VALUES"
            " ($1, $2, 'pm', 'project_manager', 'global_tenant_template', 'ai', 'p', '{}'),"
            " ($3, $2, 'qa', 'qa', 'global_tenant_template', 'ai', 'p', '{}'),"
            " ($4, $2, 'res', 'researcher', 'global_tenant_template', 'ai', 'p', '{}')",
            agent_pm,
            tenant_a,
            agent_qa,
            agent_researcher,
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1, $2), ($1, $3), ($1, $4)",
            team_a,
            agent_pm,
            agent_qa,
            agent_researcher,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, team_id) VALUES"
            " ($1, $2, 'Con equipo', $3), ($4, $2, 'Sin equipo', NULL),"
            " ($5, $6, 'Ajeno', NULL)",
            project_a,
            tenant_a,
            team_a,
            project_solo,
            project_b,
            tenant_b,
        )
    finally:
        await conn.close()

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "admin_a": admin_a,
        "project_a": project_a,
        "project_solo": project_solo,
        "project_b": project_b,
    }


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
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_planning_roles_are_the_real_team_not_the_whole_enum(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{seeded['project_a']}/planning-roles", headers=headers)
        assert resp.status_code == 200, resp.text
        roles = resp.json()["roles"]

    # El equipo tiene PM + QA + researcher. `researcher` no es portavoz de
    # planificación: ofrecerlo sería ofrecer un turno vacío.
    assert roles == ["project_manager", "qa"]


@pytest.mark.asyncio
async def test_a_project_without_a_team_offers_only_the_pm(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(
            f"/projects/{seeded['project_solo']}/planning-roles", headers=headers
        )
        assert resp.status_code == 200, resp.text

    # El PM es el único rol obligatorio: conduce el turno aunque no haya equipo.
    assert resp.json()["roles"] == ["project_manager"]


@pytest.mark.asyncio
async def test_another_tenants_project_is_a_404_not_an_empty_list(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/projects/{seeded['project_b']}/planning-roles", headers=headers)

    # Un 200 con lista vacía confirmaría que el proyecto existe. RLS lo esconde
    # y el endpoint lo convierte en 404.
    assert resp.status_code == 404, resp.text
