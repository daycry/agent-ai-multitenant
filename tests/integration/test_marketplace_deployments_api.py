"""Los cuatro endpoints de despliegue por HTTP — `task_mkt2_04`.

El servicio ya está probado en `test_marketplace_deploy_service.py`. Lo que se
comprueba AQUÍ es lo que solo el router puede romper:

* el **gating RBAC** real (un `tenant_user` no despliega ni retira, y no por un
  `if` del test sino porque la dependencia lo rechaza);
* la traducción de errores: 404 para lo invisible (cross-tenant incluido — un
  403 confirmaría que existe), 409 para el estado, 422 con la lista de errores
  del formulario;
* que `GET /projects/{id}/marketplace/available` **deja de ofrecer** lo que ya
  está desplegado, que es toda su razón de ser;
* que la respuesta lleva `warnings` y `oauth_pending`: un despliegue que no
  entregó nada no puede parecer un éxito limpio.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.cross_tenant]


@pytest.fixture()
def configured_app(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
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
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


_TOOL_MANIFEST: dict[str, Any] = {
    "implementation_type": "http_endpoint",
    "implementation_ref": "https://status.example.test/api",
    "targets": ["qa"],
    "config_schema": {
        "type": "object",
        "properties": {"base_url": {"type": "string"}},
        "required": ["base_url"],
    },
}


async def _seed(dsn: str) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant_a",
            "tenant_b",
            "admin_a",
            "member_a",
            "admin_b",
            "source",
            "listing",
            "team_a",
            "agent_qa",
            "project_a",
            "project_a2",
            "project_b",
            "team_b",
        )
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE marketplace_deployments, marketplace_audit_entries,"
            " marketplace_installations, marketplace_listing_versions,"
            " marketplace_listings, marketplace_sources, agent_tools, agent_skills,"
            " team_members, tools, skills, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,$2,$3),($4,$5,$6)",
            ids["tenant_a"],
            "API A",
            "api-a",
            ids["tenant_b"],
            "API B",
            "api-b",
        )
        for user, email in (
            (ids["admin_a"], "api-admin-a@test.test"),
            (ids["member_a"], "api-member-a@test.test"),
            (ids["admin_b"], "api-admin-b@test.test"),
        ):
            await conn.execute(
                "INSERT INTO users (id, email, password_hash) VALUES ($1,$2,'argon2-placeholder')",
                user,
                email,
            )
        for user, tenant, role in (
            (ids["admin_a"], ids["tenant_a"], "tenant_admin"),
            (ids["member_a"], ids["tenant_a"], "tenant_user"),
            (ids["admin_b"], ids["tenant_b"], "tenant_admin"),
        ):
            await conn.execute(
                "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
                " VALUES ($1,$2,$3,$4)",
                uuid4(),
                tenant,
                user,
                role,
            )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-api','official',true)",
            ids["source"],
        )
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " VALUES ($1,$2,NULL,'tool','status-checker','1.0.0','verified',$3::jsonb,'[]'::jsonb)",
            ids["listing"],
            ids["source"],
            json.dumps(_TOOL_MANIFEST),
        )
        for team, tenant, name in (
            (ids["team_a"], ids["tenant_a"], "Equipo API A"),
            (ids["team_b"], ids["tenant_b"], "Equipo API B"),
        ):
            await conn.execute(
                "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,$3)", team, tenant, name
            )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, scope, system_prompt)"
            " VALUES ($1,$2,'Quim QA','qa','global_tenant_template','Eres QA.')",
            ids["agent_qa"],
            ids["tenant_a"],
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1,$2)",
            ids["team_a"],
            ids["agent_qa"],
        )
        for project, tenant, team, name, slug in (
            (ids["project_a"], ids["tenant_a"], ids["team_a"], "API A1", "api-a1"),
            (ids["project_a2"], ids["tenant_a"], ids["team_a"], "API A2", "api-a2"),
            (ids["project_b"], ids["tenant_b"], ids["team_b"], "API B1", "api-b1"),
        ):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, team_id, name, slug) VALUES ($1,$2,$3,$4,$5)",
                project,
                tenant,
                team,
                name,
                slug,
            )
    finally:
        await conn.close()
    return ids


async def _install(dsn: str, ids: dict[str, UUID]) -> UUID:
    installation = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO marketplace_installations"
            " (id, tenant_id, listing_id, version, status, installed_by)"
            " VALUES ($1,$2,$3,'1.0.0','enabled',$4)",
            installation,
            ids["tenant_a"],
            ids["listing"],
            ids["admin_a"],
        )
        await conn.execute(
            "INSERT INTO tools"
            " (id, tenant_id, name, category, implementation_type, implementation_ref,"
            "  security_level, source_listing_id, source_installation_id, source_version)"
            " VALUES ($1,$2,'status-checker','network','http_endpoint',"
            "         'https://status.example.test/api','sandboxed',$3,$4,'1.0.0')",
            uuid4(),
            ids["tenant_a"],
            ids["listing"],
            installation,
        )
    finally:
        await conn.close()
    return installation


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ===========================================================================
# Camino feliz + la respuesta honesta
# ===========================================================================
@pytest.mark.asyncio
async def test_deploy_then_list_then_retire(configured_app: Any, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["already_deployed"] is False
        assert body["deployment"]["status"] == "active"
        assert body["deployment"]["project_id"] == str(ids["project_a"])
        assert body["deployment"]["created_refs"]["agent_tools"]
        deployment_id = body["deployment"]["id"]

        listed = await client.get(
            f"/marketplace/installations/{installation}/deployments", headers=headers
        )
        assert listed.status_code == 200
        assert [d["id"] for d in listed.json()] == [deployment_id]

        retired = await client.post(
            f"/marketplace/deployments/{deployment_id}/retire", headers=headers
        )
        assert retired.status_code == 200, retired.text
        assert retired.json() == {
            "deployment_id": deployment_id,
            "status": "retired",
            "removed_refs": 1,
        }

        # La fila SIGUE en el listado: la ficha enseña historial.
        after = await client.get(
            f"/marketplace/installations/{installation}/deployments", headers=headers
        )
        assert [d["status"] for d in after.json()] == ["retired"]


@pytest.mark.asyncio
async def test_second_deploy_returns_already_deployed_with_a_warning(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    payload = {"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}}

    async with _client(configured_app) as client:
        first = await client.post(
            f"/marketplace/installations/{installation}/deployments", json=payload, headers=headers
        )
        second = await client.post(
            f"/marketplace/installations/{installation}/deployments", json=payload, headers=headers
        )
    assert first.status_code == 201 and second.status_code == 201
    assert second.json()["already_deployed"] is True
    assert second.json()["warnings"], "un no-op sin aviso parece un despliegue nuevo"
    assert second.json()["deployment"]["id"] == first.json()["deployment"]["id"]


@pytest.mark.asyncio
async def test_a_deploy_that_reaches_no_agent_says_so(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """`warnings` no es decoración: sin agentes del rol, el 201 tiene que avisar."""
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={
                "project_id": str(ids["project_a"]),
                "config": {"base_url": "https://a.test"},
                "role_map": ["architect"],  # el equipo solo tiene un `qa`
            },
            headers=headers,
        )
    assert created.status_code == 201
    assert created.json()["deployment"]["created_refs"] == {}
    assert any("ningún agente" in w for w in created.json()["warnings"]), created.json()


# ===========================================================================
# RBAC
# ===========================================================================
@pytest.mark.asyncio
async def test_a_tenant_user_can_read_but_not_deploy_or_retire(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    admin = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    member = {"Authorization": f"Bearer {await _mint(ids['member_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        denied = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=member,
        )
        assert denied.status_code == 403, denied.text

        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=admin,
        )
        assert created.status_code == 201
        deployment_id = created.json()["deployment"]["id"]

        # Leer sí puede.
        listed = await client.get(
            f"/marketplace/installations/{installation}/deployments", headers=member
        )
        assert listed.status_code == 200 and len(listed.json()) == 1

        # Retirar no.
        denied_retire = await client.post(
            f"/marketplace/deployments/{deployment_id}/retire", headers=member
        )
        assert denied_retire.status_code == 403


# ===========================================================================
# Errores
# ===========================================================================
@pytest.mark.asyncio
async def test_invalid_config_is_422_with_the_field_errors(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {}},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict)
    assert any("base_url" in e for e in detail["errors"]), detail


@pytest.mark.asyncio
async def test_cross_tenant_project_is_404_not_403(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Un 403 confirmaría que el proyecto del otro tenant existe."""
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_b"]), "config": {"base_url": "https://a.test"}},
            headers=headers,
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_another_tenant_cannot_list_or_retire_this_deployment(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    admin_a = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}
    admin_b = {"Authorization": f"Bearer {await _mint(ids['admin_b'], ids['tenant_b'])}"}

    async with _client(configured_app) as client:
        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=admin_a,
        )
        deployment_id = created.json()["deployment"]["id"]

        listed = await client.get(
            f"/marketplace/installations/{installation}/deployments", headers=admin_b
        )
        assert listed.status_code == 404, listed.text

        retired = await client.post(
            f"/marketplace/deployments/{deployment_id}/retire", headers=admin_b
        )
        assert retired.status_code == 404, retired.text


@pytest.mark.asyncio
async def test_retiring_twice_is_409(configured_app: Any, migrations_pg_dsn: str) -> None:
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=headers,
        )
        deployment_id = created.json()["deployment"]["id"]
        assert (
            await client.post(f"/marketplace/deployments/{deployment_id}/retire", headers=headers)
        ).status_code == 200
        again = await client.post(
            f"/marketplace/deployments/{deployment_id}/retire", headers=headers
        )
    assert again.status_code == 409, again.text


# ===========================================================================
# GET /projects/{id}/marketplace/available
# ===========================================================================
@pytest.mark.asyncio
async def test_available_offers_the_install_and_stops_after_deploying(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Su única razón de ser: dejar de ofrecer lo que este proyecto ya tiene."""
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        before = await client.get(
            f"/projects/{ids['project_a']}/marketplace/available", headers=headers
        )
        assert before.status_code == 200, before.text
        offered = before.json()
        assert len(offered) == 1
        assert offered[0]["installation_id"] == str(installation)
        # Lo que la UI necesita para pintar el formulario sin una segunda vuelta.
        assert offered[0]["config_schema"]["required"] == ["base_url"]
        assert offered[0]["targets"] == ["qa"]

        await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=headers,
        )

        after = await client.get(
            f"/projects/{ids['project_a']}/marketplace/available", headers=headers
        )
        assert after.json() == [], "sigue ofreciendo lo que ya está desplegado"

        # Y el OTRO proyecto del mismo tenant lo sigue teniendo disponible.
        other = await client.get(
            f"/projects/{ids['project_a2']}/marketplace/available", headers=headers
        )
        assert len(other.json()) == 1


@pytest.mark.asyncio
async def test_available_of_another_tenants_project_is_404(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    ids = await _seed(migrations_pg_dsn)
    await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        resp = await client.get(
            f"/projects/{ids['project_b']}/marketplace/available", headers=headers
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_available_hides_a_retired_deployment_again(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Tras retirar, la capacidad vuelve a estar disponible (el índice es parcial)."""
    ids = await _seed(migrations_pg_dsn)
    installation = await _install(migrations_pg_dsn, ids)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin_a'], ids['tenant_a'])}"}

    async with _client(configured_app) as client:
        created = await client.post(
            f"/marketplace/installations/{installation}/deployments",
            json={"project_id": str(ids["project_a"]), "config": {"base_url": "https://a.test"}},
            headers=headers,
        )
        deployment_id = created.json()["deployment"]["id"]
        await client.post(f"/marketplace/deployments/{deployment_id}/retire", headers=headers)
        again = await client.get(
            f"/projects/{ids['project_a']}/marketplace/available", headers=headers
        )
    assert len(again.json()) == 1
