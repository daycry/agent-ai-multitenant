"""Integration tests for project-creation template auto-grant
(Plan 06.13 task_06_13_03).

Wires `apply_template_kb_grants` into `POST /projects`: when the request
carries an optional `template_id`, the freshly created project is
pre-granted the template's `default_kb_grants` (built-in KB slugs →
`kb_projects` rows).

Covers:

  * happy path: POST with the api-rest template_id → kb_projects holds
    the 3 declared built-in KB ids.
  * backward-compatible: POST without template_id grants nothing.
  * idempotency: re-using the same template (same slug list) does not
    duplicate kb_projects rows.
  * 404 on a non-existent template_id.
  * 404 on a plain (is_template=false) project id used as a template.
  * cross-tenant: a template owned by ANOTHER tenant grants nothing
    (404, no leakage of its default_kb_grants).

Driven end-to-end through the ASGI app so the real RLS session is
exercised — built-in KBs live under PLATFORM_TENANT_ID and are visible
to the tenant only via the `knowledge_bases_builtin_read` policy.
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

# The api-rest built-in template declares these three KB slugs (see
# seeds/builtin_project_templates.py + test_builtin_kbs_and_template_adoption).
API_REST_KB_SLUGS = (
    "python-fastapi-conventions",
    "api-rest-guidelines",
    "postgresql-best-practices",
)


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------
async def _run_catalog_seeds(admin_url: str) -> None:
    """Seed the platform tenant + the built-in KB + project-template
    catalog through the real (BYPASSRLS) seed code, exactly like
    test_builtin_kbs_and_template_adoption does."""
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
            # Order mirrors _seeded_engine in
            # test_builtin_kbs_and_template_adoption: templates reference
            # teams (→ agents), KBs reference categories (FK).
            await ensure_platform_tenant(session)
            await seed_builtin_agents(session)
            await seed_builtin_teams(session)
            await seed_builtin_kb_categories(session)
            await seed_builtin_kbs(session)
            await seed_builtin_project_templates(session)
    finally:
        await engine.dispose()


async def _seed_tenant(dsn: str) -> dict[str, UUID]:
    """One real tenant with a tenant_admin, plus a SECOND tenant that
    owns its own private template (for the cross-tenant case)."""
    tenant = uuid4()
    admin_user = uuid4()
    foreign_tenant = uuid4()
    foreign_template = uuid4()
    plain_project = uuid4()
    # Unique suffix so re-seeding across tests in the same (session-scoped)
    # DB does not collide on organizations.slug / users.email — the
    # configured_app fixture re-runs migrations but never drops rows.
    nonce = uuid4().hex[:8]

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'Acme', $3),"
            " ($2, 'Beta', $4)",
            tenant,
            foreign_tenant,
            f"acme-{nonce}",
            f"beta-{nonce}",
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
        # A template owned by the FOREIGN tenant, carrying grants that
        # must NOT leak when adopted by the Acme admin.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status, is_template, default_kb_grants)"
            " VALUES ($1, $2, 'Foreign template', 'active', true, $3)",
            foreign_template,
            foreign_tenant,
            list(API_REST_KB_SLUGS),
        )
        # A plain (non-template) project of the Acme tenant — must be
        # rejected when passed as template_id.
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status)"
            " VALUES ($1, $2, 'Plain project', 'active')",
            plain_project,
            tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "admin_user": admin_user,
        "foreign_tenant": foreign_tenant,
        "foreign_template": foreign_template,
        "plain_project": plain_project,
    }


# ---------------------------------------------------------------------------
# Fixture: migrations + catalog seeds + configured ASGI app
# ---------------------------------------------------------------------------
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


async def _mint(user_id: UUID, tenant_id: UUID | None) -> str:
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
# Happy path: POST with the api-rest template pre-grants its 3 KBs
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_project_with_template_pregrants_kbs(
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
            json={"name": "P-from-api-rest", "template_id": str(_api_rest_template_id())},
        )
        assert r.status_code == 201, r.text
        project_id = UUID(r.json()["id"])

    granted = await _kb_project_ids(migrations_pg_dsn, project_id)
    assert granted == _expected_kb_ids()


# ---------------------------------------------------------------------------
# Backward compatible: no template_id → no auto-grants
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_project_without_template_grants_nothing(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post("/projects", headers=headers, json={"name": "P-plain"})
        assert r.status_code == 201, r.text
        project_id = UUID(r.json()["id"])

    assert await _kb_project_ids(migrations_pg_dsn, project_id) == set()


# ---------------------------------------------------------------------------
# Idempotency: two projects from the same template each get the full set
# (the helper's ON CONFLICT DO NOTHING never duplicates within a project)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repeated_template_adoption_does_not_duplicate(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}
    template_id = str(_api_rest_template_id())

    project_ids: list[UUID] = []
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        for i in range(2):
            r = await client.post(
                "/projects",
                headers=headers,
                json={"name": f"P-dup-{i}", "template_id": template_id},
            )
            assert r.status_code == 201, r.text
            project_ids.append(UUID(r.json()["id"]))

    expected = _expected_kb_ids()
    for pid in project_ids:
        granted = await _kb_project_ids(migrations_pg_dsn, pid)
        assert granted == expected
        # exactly 3 rows — no duplicates.
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM kb_projects WHERE project_id = $1", pid
            )
        finally:
            await conn.close()
        assert count == len(API_REST_KB_SLUGS)


# ---------------------------------------------------------------------------
# Unknown template_id → 404, project not created
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_project_unknown_template_is_404(
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
            json={"name": "P-bad-template", "template_id": str(uuid4())},
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# A plain (is_template=false) project id is not a template → 404
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_project_non_template_id_is_404(
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
            json={"name": "P-from-plain", "template_id": str(seed["plain_project"])},
        )
        assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# Cross-tenant: a template owned by ANOTHER tenant grants nothing.
# The permissive projects_template_read RLS policy would let the Acme
# session SELECT the Beta template, so the router must explicitly scope
# adoption to the caller's tenant + the platform catalog (404, no leak).
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_template_grants_nothing(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_tenant(migrations_pg_dsn)
    token = await _mint(seed["admin_user"], seed["tenant"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/projects",
            headers=headers,
            json={"name": "P-cross", "template_id": str(seed["foreign_template"])},
        )
        assert r.status_code == 404, r.text

    # No project from the foreign template, hence no leaked grants. Verify
    # no kb_projects row references the Acme tenant at all.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        leaked = await conn.fetchval(
            "SELECT COUNT(*) FROM kb_projects WHERE tenant_id = $1", seed["tenant"]
        )
    finally:
        await conn.close()
    assert leaked == 0
