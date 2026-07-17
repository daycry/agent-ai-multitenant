"""PROJ-01 (auditoría proyecto 2026-07-17): la adopción de plantilla es
SERVER-SIDE completa.

Antes, `POST /projects` con `template_id` solo aplicaba los KB grants: el
resto de la forma (equipo, allowed_commands, runtime, dominios, política de
aprobación, worker/repository config) lo copiaba el wizard del front — la API
directa creaba proyectos INERTES (sin equipo → nada despachable; sin
allowed_commands → stack_exec deny-all). Ahora el servidor hereda del template
todo campo que el caller no fije explícitamente, y forkea el equipo por
defecto (`fork_team` no enviado + template ⇒ true).
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


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


async def _token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant, admin, template, team = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE team_members, teams, projects, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', $2), ($3, 'P', $4)",
            tenant,
            f"ta-{tenant.hex[:8]}",
            _PLATFORM_TENANT_ID,
            f"pl-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'a@ta.test', 'h')", admin
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin,
        )
        # Un equipo builtin del catálogo (sin miembros: el fork copia la fila).
        await conn.execute(
            "INSERT INTO teams (id, tenant_id, name, is_builtin)"
            " VALUES ($1, $2, 'Equipo CI4', true)",
            team,
            _PLATFORM_TENANT_ID,
        )
        # La plantilla builtin con TODA la forma (PROJ-01).
        await conn.execute(
            """
            INSERT INTO projects (
                id, tenant_id, name, status, is_template, team_id,
                allowed_commands, default_runtime_template, allowed_domains,
                worker_config, repository_config, human_approval_policy
            ) VALUES (
                $1, $2, 'Plantilla CI4', 'active', true, $3,
                ARRAY['composer', 'php', 'phpunit'], 'php-phpunit',
                ARRAY['packagist.org'],
                '{"assignment_policy": "skill_match"}'::jsonb,
                '{"language": "php", "framework": "codeigniter4"}'::jsonb,
                '{"preset": "development"}'::jsonb
            )
            """,
            template,
            _PLATFORM_TENANT_ID,
            team,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin": admin, "template": template, "team": team}


@pytest.mark.asyncio
async def test_adoption_inherits_full_shape_and_forks_team(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json={"name": "Mi CI4", "template_id": str(seeded["template"])},
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["allowed_commands"] == ["composer", "php", "phpunit"]
    assert body["default_runtime_template"] == "php-phpunit"
    assert body["allowed_domains"] == ["packagist.org"]
    assert body["worker_config"] == {"assignment_policy": "skill_match"}
    assert body["repository_config"] == {"language": "php", "framework": "codeigniter4"}
    assert body["human_approval_policy"] == {"preset": "development"}
    # fork_team por defecto con template: el proyecto apunta a un FORK del
    # equipo (fila nueva del tenant), no al builtin de la plantilla.
    assert body["team_id"] is not None
    assert body["team_id"] != str(seeded["team"])

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        fork = await conn.fetchrow(
            "SELECT tenant_id, forked_from_team_id FROM teams WHERE id = $1",
            UUID(body["team_id"]),
        )
    finally:
        await conn.close()
    assert fork is not None
    assert fork["tenant_id"] == seeded["tenant"]
    assert fork["forked_from_team_id"] == seeded["team"]


@pytest.mark.asyncio
async def test_adoption_respects_explicit_overrides(configured_app, migrations_pg_dsn: str) -> None:
    """Lo que el caller fija explícitamente GANA a la plantilla."""
    seeded = await _seed(migrations_pg_dsn)
    headers = {"Authorization": f"Bearer {await _token(seeded['admin'], seeded['tenant'])}"}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/projects",
            json={
                "name": "Mi CI4 custom",
                "template_id": str(seeded["template"]),
                "allowed_commands": ["composer"],
                "fork_team": False,
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["allowed_commands"] == ["composer"]
    # Sin fork explícito: referencia el equipo del template tal cual.
    assert body["team_id"] == str(seeded["team"])
    # El resto sigue heredado.
    assert body["default_runtime_template"] == "php-phpunit"
