"""El histórico de versiones publicadas — `task_mkt2_11` (ADR 0142 D7).

> **Nombre.** El plan pedía `tests/integration/test_marketplace_versioning.py`.
> Ese fichero YA EXISTE desde `task_09_12` con 32 KB de la aritmética semver y
> del camino de update del plan 09, y machacarlo habría borrado esa cobertura.
> Lo de aquí es lo del ADR 0142 —las filas de `marketplace_listing_versions`—,
> así que va aparte y con el nombre de la tabla.

Lo que este fichero prueba y no prueba ningún otro:

1. **Re-publicar la MISMA semver no duplica el histórico**: actualiza la fila.
   Es la única concesión al carácter append-only de la tabla, y es deliberada —
   mientras una versión está en la cola su autor corrige, y obligarle a
   inventarse un `1.0.1` por cada corrección durante la revisión llenaría el
   histórico de versiones que nunca existieron.
2. **La fila de versión es un SNAPSHOT congelado** con el `config_schema` roto
   fuera del manifest, para que el formulario del despliegue lo encuentre sin
   recorrerlo entero.
3. **El pin llega solo con el primer despliegue** de un listing privado sin fila
   de versión (`ensure_listing_version`), que es el camino por el que pasa todo
   lo publicado antes de la fase 3.

El diff de permisos, la otra mitad de `task_mkt2_11`, se prueba puro en
`tests/unit/test_marketplace_permission_diff.py` y por HTTP en
`tests/integration/test_marketplace_update_flow.py`.
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


def _tool_yaml(*, version: str, description: str) -> str:
    return "\n".join(
        [
            "name: versioned-tool",
            f"version: {version}",
            f"description: {description}",
            "kind: tool",
            "entrypoint: versioned.main:run",
            "implementation:",
            "  runtime: python",
            "  module: versioned.main",
            f"  reference: git+https://acme.test/tools/versioned@v{version}",
        ]
    )


_MCP_MANIFEST = {
    "implementation_type": "mcp_tool",
    "implementation_ref": "https://acme.test/mcp",
    "targets": ["backend_dev"],
    "mcp_server": {"name": "acme", "transport": "streamable_http"},
    "config_schema": {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"],
    },
}


async def _seed(dsn: str) -> dict[str, UUID]:
    ids: dict[str, UUID] = {
        k: uuid4() for k in ("tenant", "admin", "source", "listing_mcp", "team", "agent", "project")
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'Ver','ver')", ids["tenant"]
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1,'ver@test.test','argon2-placeholder')",
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
            " VALUES ($1,'oficial-ver','official',true)",
            ids["source"],
        )
        # Un listing PRIVADO sin fila de versión: el estado en que quedó todo lo
        # publicado antes de la fase 3, y por el que pasa `ensure_listing_version`.
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level,"
            "  review_status, manifest, requested_permissions)"
            " VALUES ($1,$2,$3,'mcp_server','acme-mcp','1.0.0','verified','published',"
            "         $4::jsonb,'[]'::jsonb)",
            ids["listing_mcp"],
            ids["source"],
            ids["tenant"],
            json.dumps(_MCP_MANIFEST),
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo ver')",
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
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, team_id, name, slug)"
            " VALUES ($1,$2,$3,'Proyecto ver','proj-ver')",
            ids["project"],
            ids["tenant"],
            ids["team"],
        )
    finally:
        await conn.close()
    return ids


async def _versions(dsn: str, listing_id: UUID) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT version, manifest, config_schema, changelog"
            "  FROM marketplace_listing_versions WHERE listing_id = $1 ORDER BY version",
            listing_id,
        )
        return [
            {
                "version": r["version"],
                "manifest": json.loads(r["manifest"]),
                "config_schema": (
                    json.loads(r["config_schema"]) if r["config_schema"] is not None else None
                ),
                "changelog": r["changelog"],
            }
            for r in rows
        ]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 1. Re-publicar la misma semver no duplica el histórico
# ---------------------------------------------------------------------------
def test_republishing_the_same_version_updates_the_row_instead_of_duplicating(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

            created = await client.post(
                "/marketplace/private/listings",
                json={
                    "kind": "tool",
                    "manifest": _tool_yaml(version="1.0.0", description="Primera."),
                    "changelog": "Primera.",
                },
                headers=headers,
            )
            assert created.status_code == 201, created.text
            listing_id = UUID(created.json()["id"])

            # Corrige SIN cambiar la versión: sigue en la cola, es el mismo
            # release. El histórico no debe inventarse una entrada más.
            fixed = await client.put(
                f"/marketplace/private/listings/{listing_id}",
                json={
                    "manifest": _tool_yaml(version="1.0.0", description="Primera, corregida."),
                    "changelog": "Primera, corregida.",
                },
                headers=headers,
            )
            assert fixed.status_code == 200, fixed.text

            rows = await _versions(migrations_pg_dsn, listing_id)
            assert [r["version"] for r in rows] == ["1.0.0"], rows
            assert rows[0]["changelog"] == "Primera, corregida."
            assert rows[0]["manifest"]["description"] == "Primera, corregida."

            # Y una versión NUEVA sí abre entrada nueva.
            bumped = await client.put(
                f"/marketplace/private/listings/{listing_id}",
                json={
                    "manifest": _tool_yaml(version="1.1.0", description="Segunda."),
                    "changelog": "Segunda.",
                },
                headers=headers,
            )
            assert bumped.status_code == 200, bumped.text
            rows = await _versions(migrations_pg_dsn, listing_id)
            assert [r["version"] for r in rows] == ["1.0.0", "1.1.0"]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 2. La fila es un snapshot: el `config_schema` sale del manifest
# ---------------------------------------------------------------------------
def test_the_version_row_breaks_the_config_schema_out_of_the_manifest(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El formulario del despliegue lo busca ahí, no recorriendo el manifest."""

    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

            install = await client.post(
                "/marketplace/installations",
                json={"listing_id": str(ids["listing_mcp"])},
                headers=headers,
            )
            assert install.status_code == 201, install.text
            installation_id = install.json()["id"]

            # El listing PRIVADO no tenía fila de versión: la crea el despliegue.
            deployed = await client.post(
                f"/marketplace/installations/{installation_id}/deployments",
                json={
                    "project_id": str(ids["project"]),
                    "config": {"url": "https://acme.test/mcp"},
                },
                headers=headers,
            )
            assert deployed.status_code == 201, deployed.text

        rows = await _versions(migrations_pg_dsn, ids["listing_mcp"])
        assert [r["version"] for r in rows] == ["1.0.0"]
        assert rows[0]["config_schema"] == _MCP_MANIFEST["config_schema"], (
            "sin el `config_schema` roto fuera del manifest, el formulario del "
            "despliegue tiene que adivinar dónde está"
        )
        # Y el manifest completo también se conserva: la fila es el registro de
        # lo publicado, no un resumen.
        assert rows[0]["manifest"]["targets"] == ["backend_dev"]

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 3. El pin llega con el primer despliegue
# ---------------------------------------------------------------------------
def test_first_deploy_pins_the_installation_to_its_version_row(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    async def scenario() -> None:
        ids = await _seed(migrations_pg_dsn)
        async with _client(configured_app) as client:
            headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

            install = await client.post(
                "/marketplace/installations",
                json={"listing_id": str(ids["listing_mcp"])},
                headers=headers,
            )
            installation_id = install.json()["id"]

            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                before = await conn.fetchval(
                    "SELECT pinned_version_id FROM marketplace_installations WHERE id = $1",
                    UUID(installation_id),
                )
                assert before is None, (
                    "instalar un listing privado sin histórico no puede pinar nada:"
                    " la fila de versión aún no existe"
                )
            finally:
                await conn.close()

            await client.post(
                f"/marketplace/installations/{installation_id}/deployments",
                json={
                    "project_id": str(ids["project"]),
                    "config": {"url": "https://acme.test/mcp"},
                },
                headers=headers,
            )

            conn = await asyncpg.connect(migrations_pg_dsn)
            try:
                pinned = await conn.fetchval(
                    "SELECT v.version FROM marketplace_installations i"
                    "  JOIN marketplace_listing_versions v ON v.id = i.pinned_version_id"
                    " WHERE i.id = $1",
                    UUID(installation_id),
                )
                assert pinned == "1.0.0"
            finally:
                await conn.close()

    asyncio.run(scenario())
