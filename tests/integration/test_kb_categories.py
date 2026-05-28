"""Integration tests for /kb-categories endpoints (Plan 06.10 task_06_10_06).

Covers:
  * GET listas built-ins + tenant custom mezclados
  * POST crea custom y rechaza slug duplicado (409)
  * PUT/DELETE rechazados sobre built-ins (403)
  * PUT/DELETE OK sobre custom
  * DELETE soft-deletea + nullifica `knowledge_bases.category_id`
  * `KnowledgeBase` puede crearse con `category_id`
  * `GET /knowledge-bases?category=<slug>` filtra
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


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " kb_categories, memory_entries, plans, conversations, projects,"
            " agents, teams, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug)" " VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Cat",
            "tenant-cat",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-cat",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3)",
            user_id,
            "alice@cat.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            user_id,
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "user_id": user_id}


async def _seed_builtin_categories(dsn: str) -> None:
    """Inserta las 5 built-in directamente via asyncpg (sin pasar por la
    sesión RLS) — la migration crea la tabla vacía."""
    from api_server.seeds.builtin_kb_categories import BUILTIN_KB_CATEGORIES

    conn = await asyncpg.connect(dsn)
    try:
        for cat in BUILTIN_KB_CATEGORIES:
            await conn.execute(
                "INSERT INTO kb_categories (id, tenant_id, slug, name, color)"
                " VALUES ($1, NULL, $2, $3, $4)"
                " ON CONFLICT (id) DO NOTHING",
                cat.id,
                cat.slug,
                cat.name,
                cat.color,
            )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture (carbon-copy del test_kb_endpoints.py)
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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


# ===========================================================================
# GET — lista built-ins + tenant custom mezclados
# ===========================================================================
@pytest.mark.asyncio
async def test_list_categories_returns_builtins_and_custom(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    await _seed_builtin_categories(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Tenant crea una custom.
        post = await client.post(
            "/kb-categories",
            json={"slug": "fintech", "name": "Fintech", "color": "#ff0000"},
            headers=headers,
        )
        assert post.status_code == 201, post.text

        listed = await client.get("/kb-categories", headers=headers)
        assert listed.status_code == 200
        rows = listed.json()
        # 5 built-ins + 1 custom = 6
        assert len(rows) == 6
        slugs = {r["slug"] for r in rows}
        assert "stack" in slugs and "role" in slugs and "fintech" in slugs
        # Built-ins: tenant_id is null + is_builtin True
        builtin_row = next(r for r in rows if r["slug"] == "stack")
        assert builtin_row["tenant_id"] is None
        assert builtin_row["is_builtin"] is True
        # Custom: tenant_id propio + is_builtin False
        custom_row = next(r for r in rows if r["slug"] == "fintech")
        assert custom_row["tenant_id"] == str(seeded["tenant_id"])
        assert custom_row["is_builtin"] is False


# ===========================================================================
# POST — rechaza duplicado (vs built-in y vs custom)
# ===========================================================================
@pytest.mark.asyncio
async def test_post_rejects_duplicate_slug_vs_builtin(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    await _seed_builtin_categories(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # `stack` ya existe como built-in.
        dup = await client.post(
            "/kb-categories",
            json={"slug": "stack", "name": "My Stack"},
            headers=headers,
        )
        assert dup.status_code == 409, dup.text


@pytest.mark.asyncio
async def test_post_rejects_duplicate_slug_vs_own_custom(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/kb-categories",
            json={"slug": "fintech", "name": "Fintech"},
            headers=headers,
        )
        assert first.status_code == 201
        dup = await client.post(
            "/kb-categories",
            json={"slug": "fintech", "name": "Fintech 2"},
            headers=headers,
        )
        assert dup.status_code == 409


# ===========================================================================
# PUT — rechaza built-in
# ===========================================================================
@pytest.mark.asyncio
async def test_put_builtin_returns_403(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.seeds.builtin_kb_categories import kb_category_id_for_slug

    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    await _seed_builtin_categories(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    stack_id = kb_category_id_for_slug("stack")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.put(
            f"/kb-categories/{stack_id}",
            json={"name": "Mutated"},
            headers=headers,
        )
        assert resp.status_code == 403


# ===========================================================================
# DELETE — rechaza built-in + soft-deletea custom + nullifica KB.category_id
# ===========================================================================
@pytest.mark.asyncio
async def test_delete_builtin_returns_403(configured_app, migrations_pg_dsn: str) -> None:
    from api_server.seeds.builtin_kb_categories import kb_category_id_for_slug

    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    await _seed_builtin_categories(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    role_id = kb_category_id_for_slug("role")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.delete(f"/kb-categories/{role_id}", headers=headers)
        assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_custom_nullifies_kb_category(configured_app, migrations_pg_dsn: str) -> None:
    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Crea categoría custom.
        cat = await client.post(
            "/kb-categories",
            json={"slug": "fintech", "name": "Fintech"},
            headers=headers,
        )
        assert cat.status_code == 201
        cat_id = cat.json()["id"]

        # 2. Crea KB con esa categoría.
        kb = await client.post(
            "/knowledge-bases",
            json={"name": "KB Fin", "category_id": cat_id},
            headers=headers,
        )
        assert kb.status_code == 201, kb.text
        kb_id = kb.json()["id"]
        assert kb.json()["category"]["slug"] == "fintech"

        # 3. Borra la categoría.
        delete = await client.delete(f"/kb-categories/{cat_id}", headers=headers)
        assert delete.status_code == 204

        # 4. La KB sigue existiendo pero sin categoría.
        kb_after = await client.get(f"/knowledge-bases/{kb_id}", headers=headers)
        assert kb_after.status_code == 200
        assert kb_after.json()["category"] is None


# ===========================================================================
# KB con category_id + filtro ?category=slug
# ===========================================================================
@pytest.mark.asyncio
async def test_kb_can_be_created_with_category_and_filtered(
    configured_app, migrations_pg_dsn: str
) -> None:
    app = configured_app
    seeded = await _seed(migrations_pg_dsn)
    await _seed_builtin_categories(migrations_pg_dsn)
    token = await _mint_token(seeded["user_id"], seeded["tenant_id"])
    headers = {"Authorization": f"Bearer {token}"}

    from api_server.seeds.builtin_kb_categories import kb_category_id_for_slug

    stack_id = kb_category_id_for_slug("stack")
    role_id = kb_category_id_for_slug("role")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 2 KBs: una stack, otra role.
        kb_a = await client.post(
            "/knowledge-bases",
            json={"name": "KB Stack", "category_id": str(stack_id)},
            headers=headers,
        )
        assert kb_a.status_code == 201, kb_a.text
        assert kb_a.json()["category"]["slug"] == "stack"

        kb_b = await client.post(
            "/knowledge-bases",
            json={"name": "KB Role", "category_id": str(role_id)},
            headers=headers,
        )
        assert kb_b.status_code == 201

        # Filtro por slug — solo la stack.
        filtered = await client.get("/knowledge-bases?category=stack", headers=headers)
        assert filtered.status_code == 200
        rows = filtered.json()
        assert len(rows) == 1
        assert rows[0]["name"] == "KB Stack"

        # Filtro por slug que no existe — array vacío, no 404.
        empty = await client.get("/knowledge-bases?category=does-not-exist", headers=headers)
        assert empty.status_code == 200
        assert empty.json() == []
