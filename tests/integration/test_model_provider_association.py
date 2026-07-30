"""Integration tests for the model→provider association (Plan 11.2 task_11_2_06).

Plan 11.2 adds a lightweight association between a catalog price
(``model_prices``) and a configured platform provider
(``llm_providers``) — a nullable ``provider_id`` FK (migration 0071,
``ON DELETE SET NULL``). It does NOT rebuild the price catalog or the
LiteLLM sync; it only links a model to its provider so the
``/admin/model-prices`` screen can show + filter by provider.

These tests drive that association end-to-end against the real Postgres:

  - a System Admin can create a price LINKED to a provider, and the
    response echoes ``provider_id``;
  - a System Admin can associate / disassociate an existing price via
    PATCH (``provider_id: <id>`` then ``provider_id: null``);
  - listing/filtering by ``provider_id`` returns only the prices linked
    to that provider (a tenant session may read — global-read RLS);
  - an unknown ``provider_id`` on create/update is a clean 422 (not an
    opaque 500 from the FK), distinct from the duplicate-period 409;
  - ON DELETE SET NULL: deleting the provider row leaves the price intact
    with ``provider_id`` cleared (the price outlives the provider);
  - the migration is reversible (head -> 0070 -> head) and the column
    comes and goes with it.

Fixture wiring mirrors ``test_prices_endpoints.py`` (seed via the
BYPASSRLS migrations role, mint a System-Admin JWT, drive via
AsyncClient).
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
# Seed: one tenant with an admin + a plain member, plus a System Admin user.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "admin_a": uuid4(),
        "member_a": uuid4(),
        "sysadmin": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE model_prices, llm_providers, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-assoc",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-assoc",
        )
        await conn.execute(
            # prod-09 task_prod09_04: `require_system_admin` re-reads
            # `users.is_system_admin` from the DB, so the System Admin fixture
            # must actually CARRY the flag — a `sys` JWT claim over a row whose
            # flag is false is exactly the privilege the gate now refuses.
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, $3, false), ($4, $5, $6, false), ($7, $8, $9, true)",
            ids["admin_a"],
            "admin-a@assoc.test",
            "h",
            ids["member_a"],
            "member-a@assoc.test",
            "h",
            ids["sysadmin"],
            "sysadmin@assoc.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'),"
            " ($4, $5, $6, 'tenant_user')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
            uuid4(),
            ids["tenant_a"],
            ids["member_a"],
        )
    finally:
        await conn.close()
    return ids


async def _seed_provider(
    dsn: str,
    *,
    kind: str = "ollama",
    display_name: str = "Ollama local",
    base_url: str | None = "http://ollama.test:11434",
) -> UUID:
    """Insert one platform provider as the BYPASSRLS migrations user."""
    provider_id = uuid7()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO llm_providers (id, kind, slug, display_name, base_url)"
            " VALUES ($1, $2, $5, $3, $4)",
            provider_id,
            kind,
            display_name,
            base_url,
            str(provider_id),
        )
    finally:
        await conn.close()
    return provider_id


async def _seed_price(
    dsn: str,
    *,
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-4-5",
    modality: str = "text",
    input_price: float = 3.0,
    output_price: float = 15.0,
    provider_id: UUID | None = None,
) -> UUID:
    """Insert one open price row (optionally pre-linked) as migrations user."""
    price_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO model_prices"
            " (id, provider, model_id, modality, input_price, output_price, provider_id)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7)",
            price_id,
            provider,
            model_id,
            modality,
            input_price,
            output_price,
            provider_id,
        )
    finally:
        await conn.close()
    return price_id


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_prices_endpoints.configured_app)
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
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _mint_token(
    user_id: UUID, tenant_id: UUID | None, *, is_system_admin: bool = False
) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(
        user_id=user_id,
        session_id=sid,
        tenant_id=tenant_id,
        is_system_admin=is_system_admin,
    )


def _client(app) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _valid_create_body(**overrides) -> dict:
    body = {
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5",
        "modality": "text",
        "input_price": "3.0",
        "output_price": "15.0",
    }
    body.update(overrides)
    return body


# ===========================================================================
# Create linked to a provider
# ===========================================================================
@pytest.mark.asyncio
async def test_create_price_linked_to_provider(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    provider_id = await _seed_provider(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/model-prices",
            json=_valid_create_body(provider_id=str(provider_id)),
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["provider_id"] == str(provider_id)


@pytest.mark.asyncio
async def test_create_price_without_provider_is_null(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices", json=_valid_create_body(), headers=headers)
    assert resp.status_code == 201, resp.text
    # Unassociated by default — the FK is nullable.
    assert resp.json()["provider_id"] is None


@pytest.mark.asyncio
async def test_create_with_unknown_provider_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/model-prices",
            json=_valid_create_body(provider_id=str(uuid7())),
            headers=headers,
        )
    # A clean 422 (unknown provider), NOT an opaque 500 from the FK.
    assert resp.status_code == 422, resp.text


# ===========================================================================
# Associate / disassociate an existing price via PATCH
# ===========================================================================
@pytest.mark.asyncio
async def test_associate_and_disassociate_via_patch(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    provider_id = await _seed_provider(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Associate.
        linked = await client.patch(
            f"/admin/model-prices/{price_id}",
            json={"provider_id": str(provider_id)},
            headers=headers,
        )
        assert linked.status_code == 200, linked.text
        assert linked.json()["provider_id"] == str(provider_id)

        # Disassociate — provider_id: null present on the wire clears it.
        cleared = await client.patch(
            f"/admin/model-prices/{price_id}",
            json={"provider_id": None},
            headers=headers,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["provider_id"] is None


@pytest.mark.asyncio
async def test_patch_unknown_provider_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.patch(
            f"/admin/model-prices/{price_id}",
            json={"provider_id": str(uuid7())},
            headers=headers,
        )
    assert resp.status_code == 422, resp.text


# ===========================================================================
# List / filter by provider
# ===========================================================================
@pytest.mark.asyncio
async def test_filter_by_provider(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    provider_a = await _seed_provider(migrations_pg_dsn, display_name="A")
    provider_b = await _seed_provider(migrations_pg_dsn, display_name="B")

    linked_a = await _seed_price(migrations_pg_dsn, model_id="model-a", provider_id=provider_a)
    _linked_b = await _seed_price(migrations_pg_dsn, model_id="model-b", provider_id=provider_b)
    _unlinked = await _seed_price(migrations_pg_dsn, model_id="model-c", provider_id=None)

    # A tenant session may read the global catalog (global-read RLS).
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get(f"/model-prices?provider_id={provider_a}", headers=headers)
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert {UUID(r["id"]) for r in rows} == {linked_a}
    assert all(r["provider_id"] == str(provider_a) for r in rows)


# ===========================================================================
# ON DELETE SET NULL — the price outlives the provider
# ===========================================================================
@pytest.mark.asyncio
async def test_delete_provider_sets_null_keeps_price(
    configured_app, migrations_pg_dsn: str, admin_pg_dsn: str
) -> None:
    await _seed(migrations_pg_dsn)
    provider_id = await _seed_provider(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn, provider_id=provider_id)

    # Delete the provider row directly (BYPASSRLS migrations role).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute("DELETE FROM llm_providers WHERE id = $1", provider_id)
    finally:
        await conn.close()

    # The price survives with provider_id cleared (ON DELETE SET NULL).
    conn = await asyncpg.connect(admin_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT id, provider_id FROM model_prices WHERE id = $1", price_id
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["provider_id"] is None


# ===========================================================================
# Migration reversibility (head -> 0070 -> head)
# ===========================================================================
async def _fetchval(dsn: str, sql: str, *args: object) -> object:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def test_migration_reversible(alembic_config, admin_pg_dsn: str) -> None:
    has_col_sql = (
        "SELECT count(*) FROM information_schema.columns"
        " WHERE table_name = 'model_prices' AND column_name = 'provider_id'"
    )

    def _has_column() -> bool:
        return int(asyncio.run(_fetchval(admin_pg_dsn, has_col_sql))) == 1  # type: ignore[arg-type]

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _has_column()

    command.downgrade(alembic_config, "0070_llm_providers")  # type: ignore[arg-type]
    assert not _has_column()

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    assert _has_column()
