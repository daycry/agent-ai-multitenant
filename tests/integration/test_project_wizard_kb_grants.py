"""Wizard de proyecto: `apply_template_kb_grants` controla la concesión de KBs
(Plan 06.17 task_06_17_14).

El wizard de proyecto (`projects/new/page.tsx`) envía `template_id` + un flag
`apply_template_kb_grants`:

  * con `template_id` + `apply_template_kb_grants=true` → el proyecto nace
    pre-granteado con las `default_kb_grants` de la plantilla (kb_projects).
  * con `template_id` + `apply_template_kb_grants=false` → la plantilla se
    adopta (el wizard hereda team/config en el front) pero NO se conceden KBs.
  * "proyecto en blanco" (sin `template_id`) → no concede nada.
  * compatibilidad: `template_id` sin el flag mantiene el comportamiento previo
    (concede; el flag por defecto es `true`).

Se conduce de extremo a extremo por la app ASGI con la sesión RLS real (las KBs
built-in viven bajo PLATFORM_TENANT_ID y solo son visibles vía la policy
`knowledge_bases_builtin_read`). Reusa el patrón de seed de
`test_project_template_autogrant.py`.
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

PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

API_REST_KB_SLUGS = (
    "python-fastapi-conventions",
    "api-rest-guidelines",
    "postgresql-best-practices",
)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _run_catalog_seeds(admin_url: str) -> None:
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
    from api_server.seeds.builtin_kbs import seed_builtin_kbs
    from api_server.seeds.builtin_project_templates import seed_builtin_project_templates
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(admin_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_agents(session)
            await seed_builtin_teams(session)
            await seed_builtin_kb_categories(session)
            await seed_builtin_kbs(session)
            await seed_builtin_project_templates(session)
    finally:
        await engine.dispose()


async def _seed_tenant(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    admin_user = uuid4()
    nonce = uuid4().hex[:8]

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', $2)",
            tenant,
            f"acme-{nonce}",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            admin_user,
            f"admin-{nonce}@acme.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            admin_user,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "admin_user": admin_user}


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
    asyncio.run(_run_catalog_seeds(admin_database_url))
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


async def _kb_project_ids(dsn: str, project_id: UUID) -> set[UUID]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT kb_id FROM kb_projects WHERE project_id = $1", project_id)
    finally:
        await conn.close()
    return {r["kb_id"] for r in rows}


def _api_rest_template_id() -> UUID:
    from api_server.seeds.builtin_project_templates import _project_template_id

    return _project_template_id("api-rest")


def _expected_kb_ids() -> set[UUID]:
    from api_server.seeds.builtin_kbs import kb_id_for_slug

    return {kb_id_for_slug(s) for s in API_REST_KB_SLUGS}


# ---------------------------------------------------------------------------
# Wizard con plantilla + flag explícito true → concede las KBs default.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wizard_template_apply_grants_true_grants_kbs(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/projects",
            headers=headers,
            json={
                "name": "P-wizard-grants",
                "template_id": str(_api_rest_template_id()),
                "apply_template_kb_grants": True,
            },
        )
        assert r.status_code == 201, r.text
        project_id = UUID(r.json()["id"])

    assert await _kb_project_ids(migrations_pg_dsn, project_id) == _expected_kb_ids()


# ---------------------------------------------------------------------------
# Wizard con plantilla pero flag false → adopta la plantilla SIN conceder KBs.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wizard_template_apply_grants_false_grants_nothing(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/projects",
            headers=headers,
            json={
                "name": "P-wizard-nogrants",
                "template_id": str(_api_rest_template_id()),
                "apply_template_kb_grants": False,
            },
        )
        assert r.status_code == 201, r.text
        project_id = UUID(r.json()["id"])

    assert await _kb_project_ids(migrations_pg_dsn, project_id) == set()


# ---------------------------------------------------------------------------
# "Proyecto en blanco" (sin template_id) → no concede nada.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_blank_project_grants_nothing(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/projects",
            headers=headers,
            json={"name": "P-blank"},
        )
        assert r.status_code == 201, r.text
        project_id = UUID(r.json()["id"])

    assert await _kb_project_ids(migrations_pg_dsn, project_id) == set()


# ---------------------------------------------------------------------------
# Compatibilidad: template_id SIN el flag → concede (default true).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_template_without_flag_defaults_to_grant(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/projects",
            headers=headers,
            json={
                "name": "P-default-grant",
                "template_id": str(_api_rest_template_id()),
            },
        )
        assert r.status_code == 201, r.text
        project_id = UUID(r.json()["id"])

    assert await _kb_project_ids(migrations_pg_dsn, project_id) == _expected_kb_ids()
