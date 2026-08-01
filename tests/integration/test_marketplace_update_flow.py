"""Actualizar una instalación: delta, refresco y rollback — `task_mkt2_12` (D7).

Los tres nodos que el plan declara irrenunciables, y están los tres:

1. **Delta-solo**: un permiso YA concedido no se vuelve a preguntar. Es el punto
   entero de D7 — re-preguntar por todo enseña al operador a darle a «aceptar»
   sin leer, y entonces el consentimiento deja de proteger de nada.
2. **Un despliegue con un campo requerido nuevo sin default queda `disabled`
   con motivo, y LOS DEMÁS SÍ se actualizan.** Sin esto, una sola config
   huérfana congela al tenant entero en una versión vieja.
3. **El rollback restaura config y pin**, por el mismo endpoint.

Va por HTTP de punta a punta. El único tramo sembrado a mano es el catálogo:
publicar una versión nueva de un listing GLOBAL sigue siendo cosa del seeder de
plataforma, y el modelo de versiones del plan 09 —una fila de listing por
versión, misma `(source, kind, name)`— es el que el endpoint de update recorre.
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

pytestmark = [pytest.mark.integration]


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


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# El catálogo: v1 y v2 del MISMO listing lógico (dos filas, plan 09)
# ---------------------------------------------------------------------------
_PERM_DOMAINS = {"type": "allowed_domains", "value": ["api.acme.test"]}
_PERM_PATHS = {"type": "allowed_paths", "value": ["/tmp"]}

_V1_MANIFEST = {
    "implementation_type": "mcp_tool",
    "implementation_ref": "https://acme.test/mcp",
    "targets": ["backend_dev"],
    "mcp_server": {"name": "acme", "transport": "streamable_http"},
    "config_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "timeout_s": {"type": "integer", "default": 30},
        },
        "required": ["url"],
    },
}

#: v2 «buena»: añade un permiso y un campo CON default → todo se refresca.
_V2_COMPATIBLE = {
    **_V1_MANIFEST,
    "config_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "timeout_s": {"type": "integer", "default": 30},
            "region": {"type": "string", "default": "eu"},
        },
        "required": ["url"],
    },
}

#: v2 «rompedora»: exige un campo nuevo SIN default → el despliegue no se puede
#: aplicar sin un humano.
_V2_BREAKING = {
    **_V1_MANIFEST,
    "config_schema": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "timeout_s": {"type": "integer", "default": 30},
            "workspace": {"type": "string"},
        },
        "required": ["url", "workspace"],
    },
}


async def _seed(dsn: str, *, v2_manifest: dict[str, Any]) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant",
            "admin",
            "source",
            "listing_v1",
            "listing_v2",
            "team",
            "agent",
            "project_a",
            "project_b",
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'Upd','upd')", ids["tenant"]
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1,'upd@test.test','argon2-placeholder')",
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,'tenant_admin')",
            uuid4(),
            ids["tenant"],
            ids["admin"],
        )
        await conn.execute(
            "INSERT INTO marketplace_sources (id, name, source_type, is_trusted)"
            " VALUES ($1,'oficial-upd','official',true)",
            ids["source"],
        )
        # PRIVADOS del tenant a propósito: `ensure_listing_version` solo puede
        # crear la fila de versión (y por tanto el PIN) de un listing propio —
        # fabricar el histórico del catálogo global desde una sesión de tenant
        # sería un agujero, no una comodidad.
        for key, version, manifest, perms in (
            ("listing_v1", "1.0.0", _V1_MANIFEST, [_PERM_DOMAINS]),
            ("listing_v2", "1.1.0", v2_manifest, [_PERM_DOMAINS, _PERM_PATHS]),
        ):
            await conn.execute(
                "INSERT INTO marketplace_listings"
                " (id, source_id, tenant_id, kind, name, version, trust_level,"
                "  review_status, manifest, requested_permissions)"
                " VALUES ($1,$2,$3,'mcp_server','acme-mcp',$4,'verified','published',"
                "         $5::jsonb,$6::jsonb)",
                ids[key],
                ids["source"],
                ids["tenant"],
                version,
                json.dumps(manifest),
                json.dumps(perms),
            )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo upd')",
            ids["team"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, scope, system_prompt)"
            " VALUES ($1,$2,'Bea','backend_dev','global_tenant_template','Eres Bea.')",
            ids["agent"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1,$2)",
            ids["team"],
            ids["agent"],
        )
        for key, name, slug in (
            ("project_a", "Proyecto A", "proj-a"),
            ("project_b", "Proyecto B", "proj-b"),
        ):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, team_id, name, slug)"
                " VALUES ($1,$2,$3,$4,$5)",
                ids[key],
                ids["tenant"],
                ids["team"],
                name,
                slug,
            )
    finally:
        await conn.close()
    return ids


async def _deployment_rows(dsn: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT d.id, d.project_id, d.status, d.config, d.deployed_version,"
            "       d.disabled_reason, p.slug"
            "  FROM marketplace_deployments d JOIN projects p ON p.id = d.project_id"
            " ORDER BY p.slug"
        )
        return [
            {
                "id": r["id"],
                "slug": r["slug"],
                "status": r["status"],
                "config": json.loads(r["config"]),
                "version": r["deployed_version"],
                "disabled_reason": r["disabled_reason"],
            }
            for r in rows
        ]
    finally:
        await conn.close()


async def _pinned(dsn: str) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT v.version FROM marketplace_installations i"
            "  JOIN marketplace_listing_versions v ON v.id = i.pinned_version_id"
        )
    finally:
        await conn.close()


async def _install_and_deploy(
    client: AsyncClient, headers: dict[str, str], ids: dict[str, UUID]
) -> str:
    """Instala v1 y la despliega en los DOS proyectos, con config distinta."""
    install = await client.post(
        "/marketplace/installations",
        json={"listing_id": str(ids["listing_v1"])},
        headers=headers,
    )
    assert install.status_code == 201, install.text
    installation_id = install.json()["id"]

    for project, url in (
        ("project_a", "https://a.acme.test/mcp"),
        ("project_b", "https://b.acme.test/mcp"),
    ):
        deployed = await client.post(
            f"/marketplace/installations/{installation_id}/deployments",
            json={"project_id": str(ids[project]), "config": {"url": url}},
            headers=headers,
        )
        assert deployed.status_code == 201, deployed.text
    return installation_id


# ---------------------------------------------------------------------------
# 1. El delta: se ve antes, y solo se pregunta por lo nuevo
# ---------------------------------------------------------------------------
def test_update_check_shows_the_permission_delta(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_COMPATIBLE)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            check = await client.get(
                f"/marketplace/installations/{installation_id}/update-check",
                headers=headers,
            )
            assert check.status_code == 200, check.text
            body = check.json()
            assert body["target_version"] == "1.1.0"
            assert body["requires_consent"] is True
            delta = body["permission_delta"]
            assert [p["type"] for p in delta["added"]] == ["allowed_paths"], (
                "el delta tiene que traer SOLO lo nuevo: `allowed_domains` ya "
                "estaba en la versión pinada y volver a preguntarlo enseña a "
                "aceptar sin leer"
            )
            assert delta["removed"] == []
            assert delta["changed"] == []

    asyncio.run(scenario())


def test_update_without_consenting_the_delta_is_refused_with_the_delta(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_COMPATIBLE)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            resp = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={},
                headers=headers,
            )
            assert resp.status_code == 409, resp.text
            detail = resp.json()["detail"]
            assert detail["error"] == "consent_required"
            assert detail["pending"] == ["allowed_paths"]

            # Y NADA se movió: ni la versión, ni los despliegues.
            rows = await _deployment_rows(migrations_pg_dsn)
            assert {r["version"] for r in rows} == {"1.0.0"}
            assert await _pinned(migrations_pg_dsn) == "1.0.0"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. El refresco de despliegues
# ---------------------------------------------------------------------------
def test_consenting_the_delta_updates_every_deployment(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_COMPATIBLE)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            resp = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"consent": {"allowed_paths": "grant"}},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["from_version"] == "1.0.0"
            assert body["to_version"] == "1.1.0"
            assert body["deployments"]["updated"] == 2
            assert body["deployments"]["disabled"] == 0

        rows = await _deployment_rows(migrations_pg_dsn)
        assert [r["version"] for r in rows] == ["1.1.0", "1.1.0"]
        # El campo nuevo tomó su default y la config propia de cada proyecto
        # sobrevivió: refrescar no es reconfigurar.
        assert rows[0]["config"]["region"] == "eu"
        assert rows[0]["config"]["url"] == "https://a.acme.test/mcp"
        assert rows[1]["config"]["url"] == "https://b.acme.test/mcp"
        assert await _pinned(migrations_pg_dsn) == "1.1.0"

    asyncio.run(scenario())


def test_a_broken_deployment_is_disabled_with_a_reason_and_the_rest_go_on(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El nodo irrenunciable: el fallo de uno no congela a los demás."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_BREAKING)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            # A ESTE proyecto sí le damos el campo nuevo antes de actualizar:
            # así uno de los dos puede seguir y el otro no, que es el escenario
            # que el plan pide y el que un test con un solo proyecto no ve.
            rows_before = await _deployment_rows(migrations_pg_dsn)
            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                await conn.execute(
                    "UPDATE marketplace_deployments"
                    '   SET config = config || \'{"workspace": "equipo-a"}\'::jsonb'
                    " WHERE id = $1",
                    rows_before[0]["id"],
                )
            finally:
                await conn.close()

            resp = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"consent": {"allowed_paths": "grant"}},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["deployments"]["updated"] == 1
            assert resp.json()["deployments"]["disabled"] == 1

        rows = await _deployment_rows(migrations_pg_dsn)
        ok, broken = rows[0], rows[1]
        assert ok["status"] == "active" and ok["version"] == "1.1.0"
        assert (
            broken["status"] == "disabled"
        ), "un despliegue que no encaja en el esquema nuevo NO se aplica a medias"
        assert broken["version"] == "1.0.0", "y no avanza de versión fingiendo que sí"
        assert broken["disabled_reason"] and "workspace" in broken["disabled_reason"], (
            "`disabled` sin motivo es un estado mudo: el operador ve una "
            f"capacidad apagada y ningún sitio donde leer qué falta ({broken})"
        )
        # Su config vieja se conserva para que el humano rellene lo que falta.
        assert broken["config"]["url"] == "https://b.acme.test/mcp"

    asyncio.run(scenario())


def test_a_disabled_deployment_comes_back_when_it_fits_again(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Sin camino de vuelta, la única salida de un `disabled` sería re-desplegar."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_BREAKING)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"consent": {"allowed_paths": "grant"}},
                headers=headers,
            )
            rows = await _deployment_rows(migrations_pg_dsn)
            assert {r["status"] for r in rows} == {"disabled"}

            # El humano rellena lo que faltaba…
            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                await conn.execute(
                    "UPDATE marketplace_deployments"
                    '   SET config = config || \'{"workspace": "relleno"}\'::jsonb'
                )
            finally:
                await conn.close()

            # …y volver a pedir la MISMA versión los revive.
            again = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"target_version": "1.1.0", "allow_rollback": True},
                headers=headers,
            )
            assert again.status_code == 200, again.text

        rows = await _deployment_rows(migrations_pg_dsn)
        assert [r["status"] for r in rows] == ["active", "active"]
        assert all(r["disabled_reason"] is None for r in rows)

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 3. El rollback, por el mismo endpoint
# ---------------------------------------------------------------------------
def test_rollback_restores_the_pin_and_the_config(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_COMPATIBLE)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"consent": {"allowed_paths": "grant"}},
                headers=headers,
            )
            assert await _pinned(migrations_pg_dsn) == "1.1.0"

            # Sin el opt-in explícito, ir hacia atrás es un 409: es la guarda
            # correcta para una actualización accidental.
            refused = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"target_version": "1.0.0"},
                headers=headers,
            )
            assert refused.status_code == 409, refused.text

            back = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"target_version": "1.0.0", "allow_rollback": True},
                headers=headers,
            )
            assert back.status_code == 200, back.text
            assert back.json()["to_version"] == "1.0.0"

        assert await _pinned(migrations_pg_dsn) == "1.0.0"
        rows = await _deployment_rows(migrations_pg_dsn)
        assert [r["version"] for r in rows] == ["1.0.0", "1.0.0"]
        # El campo que solo existía en 1.1.0 se fue: no se arrastra basura que
        # la versión a la que se vuelve no entiende.
        assert "region" not in rows[0]["config"]
        assert rows[0]["config"]["url"] == "https://a.acme.test/mcp"

    asyncio.run(scenario())


def test_rolling_back_does_not_re_ask_for_permissions_already_granted(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Volver atrás pide MENOS permisos, no más: no hay nada que consentir."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_COMPATIBLE)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)
            await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"consent": {"allowed_paths": "grant"}},
                headers=headers,
            )

            back = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"target_version": "1.0.0", "allow_rollback": True},
                headers=headers,
            )
            assert back.status_code == 200, back.text
            assert back.json()["permission_delta"]["requires_consent"] is False
            assert [p["type"] for p in back.json()["permission_delta"]["removed"]] == [
                "allowed_paths"
            ]

    asyncio.run(scenario())


def test_denying_a_new_permission_disables_the_installation(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Coherente con el consentimiento del alta: lo que le falta un permiso, no vive."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn, v2_manifest=_V2_COMPATIBLE)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}
            installation_id = await _install_and_deploy(client, headers, ids)

            resp = await client.post(
                f"/marketplace/installations/{installation_id}/update",
                json={"consent": {"allowed_paths": "deny"}},
                headers=headers,
            )
            assert resp.status_code == 200, resp.text
            assert resp.json()["installation"]["status"] == "disabled"
            assert [p["type"] for p in resp.json()["installation"]["denied_permissions"]] == [
                "allowed_paths"
            ]

    asyncio.run(scenario())
