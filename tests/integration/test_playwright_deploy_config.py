"""Playwright se muda al despliegue: dos proyectos, dos `base_url` (`task_mkt2_13`).

Éste es el test que demuestra que el rediseño del ADR 0142 **sirve para algo**.
El modelo viejo pedía la config guiada al INSTALAR y la guardaba a nivel de
tenant; con eso, la frase «el proyecto A prueba `app-a.example` y el B
`app-b.example`» era literalmente inexpresable: había una sola config para los
dos. Aquí se ejerce el caso completo por HTTP y se comprueba que los dos
despliegues conviven con valores distintos.

## Qué se afirma, y por qué cada cosa

1. **Instalar ya no pide config.** No se afirma leyendo la UI (que no está en
   este proceso) sino el contrato: `marketplace_installations` **no tiene
   columna de configuración** y la respuesta del install no expone ninguna. Es
   la misma comprobación que respalda no escribir migración de datos: no hay
   valores viejos que migrar porque nunca hubo dónde guardarlos.
2. **Dos proyectos, dos `base_url`.** El caso que da nombre a la tarea.
3. **La validación tipada sigue viva, en el sitio nuevo.** Una `base_url` de
   solo espacios casa `type: string` y no la caza el dialecto genérico; la caza
   `PlaywrightToolConfig` a través del `x-typed-validator` que el
   `config_schema` declara. Y el despliegue rechazado **no deja fila**.

## Una verdad incómoda que este test fija en vez de tapar

El listing oficial de Playwright **no se materializa** en una fila `tools` al
instalarse: su manifest no declara `implementation_type` (declara
`implementation.runtime: node-playwright`), y la materialización del ADR 0100
solo cubre `mcp_tool` / `http_endpoint`. Así que desplegarlo registra config y
auditoría pero **no asigna nada a ningún agente**, y el servicio lo dice en un
`warning`. El test lo AFIRMA en lugar de mirar hacia otro lado: si algún día se
materializa, este test se pone rojo y obliga a actualizar la documentación que
hoy describe la limitación.
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

_PROJECT_A_URL = "https://app-a.example"
_PROJECT_B_URL = "https://app-b.example"


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


async def _seed(dsn: str) -> dict[str, UUID]:
    """Un tenant con DOS proyectos y el listing OFICIAL de Playwright.

    El manifest no se inventa: se pide a `playwright_listing_manifest()`, así
    que si el `config_schema` de la tool cambia, este test corre contra el
    nuevo — que es justo lo que se quiere de un test de la tool destacada.
    """
    from api_server.marketplace.playwright import (
        PLAYWRIGHT_TOOL_NAME,
        PLAYWRIGHT_TOOL_VERSION,
        playwright_listing_manifest,
    )

    ids: dict[str, UUID] = {
        k: uuid4()
        for k in (
            "tenant",
            "admin",
            "source",
            "listing",
            "team",
            "agent_qa",
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'Dos Sitios','dos-sitios')",
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1,'qa@dos-sitios.test','argon2-placeholder')",
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
            " VALUES ($1,'official-catalog','official',true)",
            ids["source"],
        )
        await conn.execute(
            "INSERT INTO marketplace_listings"
            " (id, source_id, tenant_id, kind, name, version, trust_level, manifest,"
            "  requested_permissions)"
            " VALUES ($1,$2,NULL,'tool',$3,$4,'verified',$5::jsonb,'[]'::jsonb)",
            ids["listing"],
            ids["source"],
            PLAYWRIGHT_TOOL_NAME,
            PLAYWRIGHT_TOOL_VERSION,
            json.dumps(playwright_listing_manifest()),
        )
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name) VALUES ($1,$2,'Equipo QA')",
            ids["team"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, scope, system_prompt)"
            " VALUES ($1,$2,'Quim QA','qa','global_tenant_template','Eres Quim.')",
            ids["agent_qa"],
            ids["tenant"],
        )
        await conn.execute(
            "INSERT INTO team_members (team_id, agent_id) VALUES ($1,$2)",
            ids["team"],
            ids["agent_qa"],
        )
        for key, name, slug in (
            ("project_a", "Tienda A", "tienda-a"),
            ("project_b", "Tienda B", "tienda-b"),
        ):
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, team_id, name, slug) VALUES ($1,$2,$3,$4,$5)",
                ids[key],
                ids["tenant"],
                ids["team"],
                name,
                slug,
            )
    finally:
        await conn.close()
    return ids


async def _installation_config_columns(dsn: str) -> list[str]:
    """Columnas de `marketplace_installations` cuyo nombre huele a configuración."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_name = 'marketplace_installations'"
        )
        return sorted(r["column_name"] for r in rows if "config" in r["column_name"])
    finally:
        await conn.close()


async def _deployment_rows(dsn: str) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT d.status, d.config, p.slug AS project_slug"
            " FROM marketplace_deployments d JOIN projects p ON p.id = d.project_id"
            " ORDER BY p.slug"
        )
        return [
            {
                "status": r["status"],
                "config": json.loads(r["config"]),
                "project_slug": r["project_slug"],
            }
            for r in rows
        ]
    finally:
        await conn.close()


async def _install(
    client: AsyncClient, headers: dict[str, str], listing_id: UUID
) -> dict[str, Any]:
    response = await client.post(
        "/marketplace/installations",
        json={"listing_id": str(listing_id)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def _deploy(
    client: AsyncClient,
    headers: dict[str, str],
    installation_id: str,
    *,
    project_id: UUID,
    config: dict[str, Any],
) -> Any:
    return await client.post(
        f"/marketplace/installations/{installation_id}/deployments",
        json={"project_id": str(project_id), "config": config, "role_map": ["qa"]},
        headers=headers,
    )


# ===========================================================================
# El test que da sentido a la fase 5
# ===========================================================================
@pytest.mark.asyncio
async def test_two_projects_can_run_playwright_against_different_base_urls(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """Lo que el modelo viejo no podía expresar: una `base_url` por proyecto."""
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

    async with _client(configured_app) as client:
        installation = await _install(client, headers, ids["listing"])

        # (1) Instalar NO captura configuración: ni la respuesta la expone…
        assert "config" not in installation, installation
        # …ni la tabla tiene dónde guardarla (de ahí que no haga falta migrar
        # datos viejos: nunca existieron).
        assert await _installation_config_columns(migrations_pg_dsn) == []

        # (2) Dos despliegues, dos base_url.
        for project_key, base_url in (
            ("project_a", _PROJECT_A_URL),
            ("project_b", _PROJECT_B_URL),
        ):
            response = await _deploy(
                client,
                headers,
                installation["id"],
                project_id=ids[project_key],
                config={"base_url": base_url, "browsers": ["chromium", "webkit"]},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["already_deployed"] is False
            assert body["deployment"]["config"]["base_url"] == base_url
            # Los defaults del `config_schema` se aplican en el despliegue, no
            # en un formulario de instalación que ya no existe.
            assert body["deployment"]["config"]["timeout_ms"] == 30000
            assert body["deployment"]["config"]["screenshots"] == "only-on-failure"
            # La limitación honesta: sin fila de catálogo materializada, el
            # despliegue no asigna la tool a ningún agente y lo DICE.
            assert any("materializada" in w for w in body["warnings"]), body["warnings"]

    # (3) Y conviven: dos filas activas con configuración independiente.
    rows = await _deployment_rows(migrations_pg_dsn)
    assert [r["project_slug"] for r in rows] == ["tienda-a", "tienda-b"]
    assert [r["status"] for r in rows] == ["active", "active"]
    assert [r["config"]["base_url"] for r in rows] == [_PROJECT_A_URL, _PROJECT_B_URL]
    # El resto de la config es la misma sin ser la MISMA: cada fila es suya.
    assert all(r["config"]["browsers"] == ["chromium", "webkit"] for r in rows)


@pytest.mark.asyncio
async def test_the_typed_playwright_validation_still_bites_at_deploy_time(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """`PlaywrightToolConfig` sobrevive al formulario: ahora valida el despliegue.

    Una `base_url` en blanco casa `type: string` y el dialecto genérico la
    dejaría pasar; el `x-typed-validator` del `config_schema` la rechaza. Y el
    despliegue rechazado NO deja fila a medias.
    """
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

    async with _client(configured_app) as client:
        installation = await _install(client, headers, ids["listing"])
        response = await _deploy(
            client,
            headers,
            installation["id"],
            project_id=ids["project_a"],
            config={"base_url": "   "},
        )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert any("base_url" in e for e in detail["errors"]), detail
    assert await _deployment_rows(migrations_pg_dsn) == []


@pytest.mark.asyncio
async def test_an_unknown_browser_is_rejected_before_anything_is_written(
    configured_app: Any, migrations_pg_dsn: str
) -> None:
    """El dialecto genérico también sigue mordiendo sobre el esquema real."""
    ids = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _mint(ids['admin'], ids['tenant'])}"}

    async with _client(configured_app) as client:
        installation = await _install(client, headers, ids["listing"])
        response = await _deploy(
            client,
            headers,
            installation["id"],
            project_id=ids["project_b"],
            config={"base_url": _PROJECT_B_URL, "browsers": ["netscape"]},
        )

    assert response.status_code == 422, response.text
    assert any("netscape" in e for e in response.json()["detail"]["errors"])
    assert await _deployment_rows(migrations_pg_dsn) == []
