"""Integration tests for the model-price catalog endpoints (Plan 11 task_11_12).

Drives the price-catalog REST surface end-to-end against the real
Postgres + the global-read RLS of migration 0049:

  - a System Admin can create / update / supersede (delete) a price;
  - a ``tenant_admin`` and a plain ``tenant_user`` CANNOT write (403) —
    the write surface is ``require_system_admin``;
  - reads succeed for an authenticated tenant user (the global-read RLS
    policy lets a tenant session read the platform-global catalog);
  - list filters (``provider`` / ``model_id`` / ``modality`` /
    ``current_only``) + pagination bounds (``limit``/``offset`` ge/le)
    work (an out-of-range page is a clean 422);
  - the current-price lookup returns the open (in-effect) row, and 404s
    once that period is superseded;
  - USD-canonical: a created row comes back ``currency == "USD"`` and the
    create schema has no currency knob;
  - a duplicate OPEN period for the same key is a 409 (the
    partial-unique ``uq_model_prices_current``).

Fixture pattern mirrors ``test_marketplace_endpoints.py``: seed via the
BYPASSRLS migrations role, mint JWTs (incl. a System-Admin token via the
``sys`` claim), then drive the API via AsyncClient.
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
# No price rows — the tests create them through the API / seed a couple
# directly for the read paths.
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
            "TRUNCATE model_prices, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-prices",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-prices",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES"
            " ($1, $2, $3), ($4, $5, $6), ($7, $8, $9)",
            ids["admin_a"],
            "admin-a@prices.test",
            "h",
            ids["member_a"],
            "member-a@prices.test",
            "h",
            ids["sysadmin"],
            "sysadmin@prices.test",
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


async def _seed_price(
    dsn: str,
    *,
    provider: str = "anthropic",
    model_id: str = "claude-sonnet-4-5",
    modality: str = "text",
    input_price: float = 3.0,
    output_price: float = 15.0,
    closed: bool = False,
) -> UUID:
    """Insert one price row (open by default) as the BYPASSRLS migrations user."""
    price_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        if closed:
            await conn.execute(
                "INSERT INTO model_prices"
                " (id, provider, model_id, modality, input_price, output_price,"
                "  effective_from, effective_to)"
                " VALUES ($1, $2, $3, $4, $5, $6,"
                "  now() - interval '2 days', now() - interval '1 day')",
                price_id,
                provider,
                model_id,
                modality,
                input_price,
                output_price,
            )
        else:
            await conn.execute(
                "INSERT INTO model_prices"
                " (id, provider, model_id, modality, input_price, output_price)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                price_id,
                provider,
                model_id,
                modality,
                input_price,
                output_price,
            )
    finally:
        await conn.close()
    return price_id


# ---------------------------------------------------------------------------
# Fixtures (identical wiring to test_marketplace_endpoints.configured_app)
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
        "cached_input_price": "0.30",
        "context_window": 200000,
    }
    body.update(overrides)
    return body


# ===========================================================================
# Auth gate
# ===========================================================================
@pytest.mark.asyncio
async def test_unauthenticated_read_is_401(configured_app) -> None:
    async with _client(configured_app) as client:
        resp = await client.get("/model-prices")
    assert resp.status_code == 401


# ===========================================================================
# System Admin can create / update / supersede
# ===========================================================================
@pytest.mark.asyncio
async def test_system_admin_can_create(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices", json=_valid_create_body(), headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["model_id"] == "claude-sonnet-4-5"
    # USD-canonical: the catalog is USD-only and the row reflects it.
    assert body["currency"] == "USD"
    assert body["effective_to"] is None  # a fresh row opens the current period
    assert UUID(body["updated_by"]) == seeded["sysadmin"]
    assert body["source"] == "manual"


@pytest.mark.asyncio
async def test_system_admin_can_update(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.patch(
            f"/admin/model-prices/{price_id}",
            json={"output_price": "20.0", "cached_input_price": "0.5"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert float(body["output_price"]) == 20.0
    assert float(body["cached_input_price"]) == 0.5
    # Untouched fields survive.
    assert float(body["input_price"]) == 3.0


@pytest.mark.asyncio
async def test_empty_update_is_422(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.patch(f"/admin/model-prices/{price_id}", json={}, headers=headers)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_system_admin_can_supersede(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # Supersede (close) the open period.
        resp = await client.delete(f"/admin/model-prices/{price_id}", headers=headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["effective_to"] is not None

        # The row survives (effective-dated history), just closed.
        got = await client.get(f"/model-prices/{price_id}", headers=headers)
        assert got.status_code == 200
        assert got.json()["effective_to"] is not None

        # A second supersede on the now-closed row is a 409.
        again = await client.delete(f"/admin/model-prices/{price_id}", headers=headers)
        assert again.status_code == 409


@pytest.mark.asyncio
async def test_duplicate_open_period_conflicts(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        first = await client.post("/admin/model-prices", json=_valid_create_body(), headers=headers)
        assert first.status_code == 201, first.text
        # Same (provider, model_id, modality) open period -> 409.
        second = await client.post(
            "/admin/model-prices", json=_valid_create_body(output_price="16.0"), headers=headers
        )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_supersede_unknown_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.delete(f"/admin/model-prices/{uuid4()}", headers=headers)
    assert resp.status_code == 404


# ===========================================================================
# RBAC — a tenant_admin / member cannot write (403)
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_admin_cannot_create(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices", json=_valid_create_body(), headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_member_cannot_create(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.post("/admin/model-prices", json=_valid_create_body(), headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_tenant_admin_cannot_update_or_supersede(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        patch = await client.patch(
            f"/admin/model-prices/{price_id}", json={"output_price": "99.0"}, headers=headers
        )
        delete = await client.delete(f"/admin/model-prices/{price_id}", headers=headers)
    assert patch.status_code == 403
    assert delete.status_code == 403


# ===========================================================================
# Reads succeed for an authenticated tenant user
# ===========================================================================
@pytest.mark.asyncio
async def test_tenant_user_can_read(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        # List the global catalog (RLS global-read lets a tenant session see it).
        listed = await client.get("/model-prices", headers=headers)
        assert listed.status_code == 200, listed.text
        assert {UUID(r["id"]) for r in listed.json()} == {price_id}

        # Fetch one row.
        one = await client.get(f"/model-prices/{price_id}", headers=headers)
        assert one.status_code == 200
        assert UUID(one.json()["id"]) == price_id


@pytest.mark.asyncio
async def test_get_unknown_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get(f"/model-prices/{uuid4()}", headers=headers)
    assert resp.status_code == 404


# ===========================================================================
# List filters + pagination bounds
# ===========================================================================
@pytest.mark.asyncio
async def test_list_filters(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    anthropic_id = await _seed_price(
        migrations_pg_dsn, provider="anthropic", model_id="claude-sonnet-4-5"
    )
    openai_id = await _seed_price(
        migrations_pg_dsn, provider="openai", model_id="gpt-4o", input_price=2.5, output_price=10.0
    )
    # A closed historical period for the anthropic model (filtered by current_only).
    await _seed_price(
        migrations_pg_dsn,
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        input_price=2.0,
        output_price=10.0,
        closed=True,
    )
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        by_provider = await client.get("/model-prices?provider=openai", headers=headers)
        by_model = await client.get("/model-prices?model_id=claude-sonnet-4-5", headers=headers)
        current_only = await client.get(
            "/model-prices?model_id=claude-sonnet-4-5&current_only=true", headers=headers
        )
        bad_modality = await client.get("/model-prices?modality=not_a_modality", headers=headers)

    assert by_provider.status_code == 200
    assert {UUID(r["id"]) for r in by_provider.json()} == {openai_id}
    # Both the open and the closed anthropic period match the model filter.
    assert by_model.status_code == 200
    assert len(by_model.json()) == 2
    # current_only drops the closed historical row.
    assert current_only.status_code == 200
    assert {UUID(r["id"]) for r in current_only.json()} == {anthropic_id}
    # Unknown enum value -> 422.
    assert bad_modality.status_code == 422


@pytest.mark.asyncio
async def test_pagination_bounds_enforced(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    await _seed_price(migrations_pg_dsn)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        too_big = await client.get("/model-prices?limit=99999", headers=headers)
        zero = await client.get("/model-prices?limit=0", headers=headers)
        neg_offset = await client.get("/model-prices?offset=-1", headers=headers)
        ok = await client.get("/model-prices?limit=1&offset=0", headers=headers)

    assert too_big.status_code == 422
    assert zero.status_code == 422
    assert neg_offset.status_code == 422
    assert ok.status_code == 200
    assert len(ok.json()) == 1


# ===========================================================================
# Current-price lookup returns the row in effect
# ===========================================================================
@pytest.mark.asyncio
async def test_current_price_lookup(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    open_id = await _seed_price(migrations_pg_dsn, input_price=3.0, output_price=15.0)
    # A closed historical period for the SAME key — must NOT win the lookup.
    await _seed_price(migrations_pg_dsn, input_price=2.0, output_price=10.0, closed=True)
    token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with _client(configured_app) as client:
        resp = await client.get(
            "/model-prices/current" "?provider=anthropic&model_id=claude-sonnet-4-5&modality=text",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The OPEN period is the current price (not the closed historical one).
    assert UUID(body["id"]) == open_id
    assert body["effective_to"] is None
    assert float(body["input_price"]) == 3.0


@pytest.mark.asyncio
async def test_current_price_lookup_404_when_superseded(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    price_id = await _seed_price(migrations_pg_dsn)
    member_token = await _mint_token(seeded["member_a"], seeded["tenant_a"])
    admin_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        # Initially the current lookup finds the open period.
        before = await client.get(
            "/model-prices/current?provider=anthropic&model_id=claude-sonnet-4-5",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert before.status_code == 200
        assert UUID(before.json()["id"]) == price_id

        # System Admin supersedes it (closes the period).
        sup = await client.delete(
            f"/admin/model-prices/{price_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert sup.status_code == 200

        # Now there is no current price for that key -> 404.
        after = await client.get(
            "/model-prices/current?provider=anthropic&model_id=claude-sonnet-4-5",
            headers={"Authorization": f"Bearer {member_token}"},
        )
    assert after.status_code == 404
