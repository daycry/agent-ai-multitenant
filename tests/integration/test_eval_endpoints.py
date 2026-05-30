"""Integration tests for the eval dataset / criteria / item CRUD (task_14_03).

The full eval data-foundation REST surface:

  * ``GET/POST/GET{id}/PUT/DELETE /eval-datasets`` — CRUD of a tenant's
    per-tenant golden datasets;
  * ``GET/POST /eval-datasets/{id}/criteria`` + ``GET/PUT/DELETE
    /eval-criteria/{id}`` — the judging criteria (rubric / weight / pass
    threshold consumed by the LLM-as-judge in Fase B);
  * ``GET/POST /eval-datasets/{id}/items`` + ``GET/PUT/DELETE
    /eval-dataset-items/{id}`` — the golden items (input + reference output).

Every endpoint is JWT-authenticated, gated on ``tenant_admin`` and runs on a
tenant-scoped RLS session.

Coverage:

  * CRUD a dataset (create / get / update / list / delete-then-gone);
  * CRUD a criterion under a dataset (rubric + weight + pass threshold);
  * CRUD a golden item under a dataset;
  * lists are paginated (``limit``/``offset``) and tenant-scoped;
  * RBAC: a plain member (tenant_user) is denied (403) on every verb;
  * cross-tenant (@pytest.mark.cross_tenant): tenant A never sees / edits
    tenant B's datasets / criteria / items — all 404 under RLS.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# DB seed helpers (BYPASSRLS via migrations_user DSN)
# ---------------------------------------------------------------------------
async def _seed_tenant(dsn: str, *, slug: str) -> UUID:
    tenant = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            slug.title(),
            slug,
        )
    finally:
        await conn.close()
    return tenant


async def _seed_user_with_jwt(
    dsn: str, redis_url: str, *, tenant_id: UUID, email: str, role: str
) -> tuple[UUID, str]:
    """Seed a user + active membership with ``role`` + a LIVE Redis session,
    returning ``(user_id, jwt)`` so the test can call the API as that user."""
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore
    from redis.asyncio import Redis
    from uuid6 import uuid7

    user_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) "
            "VALUES ($1, $2, $3, false)",
            user_id,
            email,
            "x",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role, is_active) "
            "VALUES ($1, $2, $3, $4, true)",
            uuid4(),
            tenant_id,
            user_id,
            role,
        )
    finally:
        await conn.close()

    session_id = uuid7()
    redis: Redis = Redis.from_url(redis_url, decode_responses=True)
    try:
        await SessionStore(redis).create(
            session_id, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
        )
    finally:
        await redis.aclose()
    jwt = encode_jwt(user_id=user_id, session_id=session_id, tenant_id=tenant_id)
    return user_id, jwt


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE eval_results, eval_runs, eval_criteria, eval_dataset_items, "
            "eval_datasets, executions, tasks, projects, user_org_memberships, "
            "organizations, users RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _row_exists(dsn: str, *, table: str, row_id: UUID) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(
            await conn.fetchval(
                f"SELECT count(*) FROM {table} WHERE id = $1 AND deleted_at IS NULL",
                row_id,
            )
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# App fixture: real api-server wired to the test DB + Redis
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
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")
    monkeypatch.setenv("API_SERVER_SSO_REDIRECT_BASE_URL", "http://testserver")
    monkeypatch.delenv("API_SERVER_VAULT_TOKEN", raising=False)

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


def _auth(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def _client(app: object) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")


async def _create_dataset(client: AsyncClient, jwt: str, *, name: str) -> str:
    resp = await client.post("/eval-datasets", json={"name": name}, headers=_auth(jwt))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _admin(dsn: str, redis_url: str, *, tenant: UUID, slug: str) -> str:
    _id, jwt = await _seed_user_with_jwt(
        dsn, redis_url, tenant_id=tenant, email=f"admin@{slug}.example.com", role="tenant_admin"
    )
    return jwt


# ---------------------------------------------------------------------------
# Dataset CRUD round-trip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dataset_crud(configured_app, migrations_pg_dsn: str, test_redis_url: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")

    async with _client(configured_app) as client:
        # create
        created = await client.post(
            "/eval-datasets",
            json={"name": "Login golden", "description": "approved login tasks"},
            headers=_auth(jwt),
        )
        assert created.status_code == 201, created.text
        dataset_id = created.json()["id"]
        assert created.json()["kind"] == "golden"
        assert created.json()["item_count"] == 0

        # get
        got = await client.get(f"/eval-datasets/{dataset_id}", headers=_auth(jwt))
        assert got.status_code == 200, got.text
        assert got.json()["name"] == "Login golden"

        # update (partial)
        updated = await client.put(
            f"/eval-datasets/{dataset_id}",
            json={"name": "Login golden v2", "target_role": "backend-dev"},
            headers=_auth(jwt),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "Login golden v2"
        assert updated.json()["target_role"] == "backend-dev"

        # list (paginated)
        listing = await client.get("/eval-datasets?limit=10&offset=0", headers=_auth(jwt))
        assert listing.status_code == 200, listing.text
        assert [d["id"] for d in listing.json()] == [dataset_id]

        # delete (soft) -> gone
        deleted = await client.delete(f"/eval-datasets/{dataset_id}", headers=_auth(jwt))
        assert deleted.status_code == 204, deleted.text
        gone = await client.get(f"/eval-datasets/{dataset_id}", headers=_auth(jwt))
        assert gone.status_code == 404, gone.text
        empty = await client.get("/eval-datasets", headers=_auth(jwt))
        assert empty.json() == []

    assert await _row_exists(migrations_pg_dsn, table="eval_datasets", row_id=UUID(dataset_id)) is (
        False
    )


# ---------------------------------------------------------------------------
# Criterion CRUD round-trip (rubric + weight + pass threshold)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_criterion_crud(configured_app, migrations_pg_dsn: str, test_redis_url: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")

    async with _client(configured_app) as client:
        dataset_id = await _create_dataset(client, jwt, name="golden")

        created = await client.post(
            f"/eval-datasets/{dataset_id}/criteria",
            json={
                "name": "PEP 8",
                "judge_instruction": "Does the code follow PEP 8?",
                "weight": "2.000",
                "pass_threshold": "0.700",
            },
            headers=_auth(jwt),
        )
        assert created.status_code == 201, created.text
        criterion_id = created.json()["id"]
        assert created.json()["dataset_id"] == dataset_id
        assert created.json()["judge_instruction"] == "Does the code follow PEP 8?"
        assert created.json()["weight"] == "2.000"
        assert created.json()["pass_threshold"] == "0.700"

        # default weight/threshold when omitted
        defaulted = await client.post(
            f"/eval-datasets/{dataset_id}/criteria",
            json={"name": "tone", "judge_instruction": "Is the brand tone respected?"},
            headers=_auth(jwt),
        )
        assert defaulted.status_code == 201, defaulted.text
        assert defaulted.json()["weight"] == "1.000"
        assert defaulted.json()["pass_threshold"] == "0.500"

        # get
        got = await client.get(f"/eval-criteria/{criterion_id}", headers=_auth(jwt))
        assert got.status_code == 200, got.text

        # update
        updated = await client.put(
            f"/eval-criteria/{criterion_id}",
            json={"pass_threshold": "0.900", "description": "lint clean"},
            headers=_auth(jwt),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["pass_threshold"] == "0.900"
        assert updated.json()["description"] == "lint clean"

        # list under the dataset (paginated)
        listing = await client.get(
            f"/eval-datasets/{dataset_id}/criteria?limit=10", headers=_auth(jwt)
        )
        assert listing.status_code == 200, listing.text
        assert {c["name"] for c in listing.json()} == {"PEP 8", "tone"}

        # delete -> gone
        deleted = await client.delete(f"/eval-criteria/{criterion_id}", headers=_auth(jwt))
        assert deleted.status_code == 204, deleted.text
        gone = await client.get(f"/eval-criteria/{criterion_id}", headers=_auth(jwt))
        assert gone.status_code == 404, gone.text

    # out-of-range threshold is rejected (422), never persisted
    async with _client(configured_app) as client:
        bad = await client.post(
            f"/eval-datasets/{dataset_id}/criteria",
            json={"name": "bad", "judge_instruction": "x", "pass_threshold": "1.500"},
            headers=_auth(jwt),
        )
        assert bad.status_code == 422, bad.text


# ---------------------------------------------------------------------------
# Item CRUD round-trip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_item_crud(configured_app, migrations_pg_dsn: str, test_redis_url: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")

    async with _client(configured_app) as client:
        dataset_id = await _create_dataset(client, jwt, name="golden")

        created = await client.post(
            f"/eval-datasets/{dataset_id}/items",
            json={
                "input": {"prompt": "add login"},
                "expected_output": "the diff",
            },
            headers=_auth(jwt),
        )
        assert created.status_code == 201, created.text
        item_id = created.json()["id"]
        assert created.json()["dataset_id"] == dataset_id
        assert created.json()["input"] == {"prompt": "add login"}
        assert created.json()["expected_output"] == "the diff"
        # Hand-authored: no provenance.
        assert created.json()["source_task_id"] is None

        # update (input + clear expected_output)
        updated = await client.put(
            f"/eval-dataset-items/{item_id}",
            json={"input": {"prompt": "add login v2"}, "expected_output": None},
            headers=_auth(jwt),
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["input"] == {"prompt": "add login v2"}
        assert updated.json()["expected_output"] is None

        # list under the dataset
        listing = await client.get(f"/eval-datasets/{dataset_id}/items", headers=_auth(jwt))
        assert listing.status_code == 200, listing.text
        assert [i["id"] for i in listing.json()] == [item_id]

        # delete -> gone
        deleted = await client.delete(f"/eval-dataset-items/{item_id}", headers=_auth(jwt))
        assert deleted.status_code == 204, deleted.text
        gone = await client.get(f"/eval-dataset-items/{item_id}", headers=_auth(jwt))
        assert gone.status_code == 404, gone.text


# ---------------------------------------------------------------------------
# Lists are paginated
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_dataset_list_pagination(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")

    async with _client(configured_app) as client:
        for n in range(3):
            await _create_dataset(client, jwt, name=f"ds-{n}")

        page1 = await client.get("/eval-datasets?limit=2&offset=0", headers=_auth(jwt))
        assert page1.status_code == 200, page1.text
        assert len(page1.json()) == 2

        page2 = await client.get("/eval-datasets?limit=2&offset=2", headers=_auth(jwt))
        assert page2.status_code == 200, page2.text
        assert len(page2.json()) == 1

        # out-of-range limit is a clean 422 (not a silent clamp)
        bad = await client.get("/eval-datasets?limit=0", headers=_auth(jwt))
        assert bad.status_code == 422, bad.text


# ---------------------------------------------------------------------------
# RBAC: a plain member (tenant_user) is denied (403) on every verb
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_plain_member_denied_403(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    admin_jwt = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant, slug="acme")
    _uid, member_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )

    async with _client(configured_app) as client:
        dataset_id = await _create_dataset(client, admin_jwt, name="golden")

        assert (await client.get("/eval-datasets", headers=_auth(member_jwt))).status_code == 403
        assert (
            await client.post("/eval-datasets", json={"name": "x"}, headers=_auth(member_jwt))
        ).status_code == 403
        assert (
            await client.get(f"/eval-datasets/{dataset_id}", headers=_auth(member_jwt))
        ).status_code == 403
        assert (
            await client.put(
                f"/eval-datasets/{dataset_id}",
                json={"name": "y"},
                headers=_auth(member_jwt),
            )
        ).status_code == 403
        assert (
            await client.delete(f"/eval-datasets/{dataset_id}", headers=_auth(member_jwt))
        ).status_code == 403
        assert (
            await client.post(
                f"/eval-datasets/{dataset_id}/criteria",
                json={"name": "c", "judge_instruction": "x"},
                headers=_auth(member_jwt),
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/eval-datasets/{dataset_id}/items",
                json={"input": {}},
                headers=_auth(member_jwt),
            )
        ).status_code == 403


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A never sees / edits tenant B's datasets/criteria/items
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_isolation(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    jwt_a = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant_a, slug="alpha")
    jwt_b = await _admin(migrations_pg_dsn, test_redis_url, tenant=tenant_b, slug="bravo")

    async with _client(configured_app) as client:
        # B owns a dataset with a criterion + an item.
        dataset_b = await _create_dataset(client, jwt_b, name="b-golden")
        crit_b = await client.post(
            f"/eval-datasets/{dataset_b}/criteria",
            json={"name": "b-crit", "judge_instruction": "secret rubric"},
            headers=_auth(jwt_b),
        )
        assert crit_b.status_code == 201, crit_b.text
        crit_b_id = crit_b.json()["id"]
        item_b = await client.post(
            f"/eval-datasets/{dataset_b}/items",
            json={"input": {"prompt": "b-secret"}},
            headers=_auth(jwt_b),
        )
        assert item_b.status_code == 201, item_b.text
        item_b_id = item_b.json()["id"]

        # A owns its own dataset.
        await _create_dataset(client, jwt_a, name="a-golden")

        # A's listing never shows B's dataset.
        listing_a = await client.get("/eval-datasets", headers=_auth(jwt_a))
        assert listing_a.status_code == 200, listing_a.text
        assert {d["name"] for d in listing_a.json()} == {"a-golden"}

        # A cannot GET / PUT / DELETE B's dataset (404 under RLS).
        assert (
            await client.get(f"/eval-datasets/{dataset_b}", headers=_auth(jwt_a))
        ).status_code == 404
        assert (
            await client.put(
                f"/eval-datasets/{dataset_b}", json={"name": "hijack"}, headers=_auth(jwt_a)
            )
        ).status_code == 404
        assert (
            await client.delete(f"/eval-datasets/{dataset_b}", headers=_auth(jwt_a))
        ).status_code == 404

        # A cannot enumerate / read / edit B's criteria.
        assert (
            await client.get(f"/eval-datasets/{dataset_b}/criteria", headers=_auth(jwt_a))
        ).status_code == 404
        assert (
            await client.get(f"/eval-criteria/{crit_b_id}", headers=_auth(jwt_a))
        ).status_code == 404
        assert (
            await client.put(
                f"/eval-criteria/{crit_b_id}",
                json={"judge_instruction": "tampered"},
                headers=_auth(jwt_a),
            )
        ).status_code == 404
        assert (
            await client.delete(f"/eval-criteria/{crit_b_id}", headers=_auth(jwt_a))
        ).status_code == 404

        # A cannot enumerate / read / edit B's items.
        assert (
            await client.get(f"/eval-datasets/{dataset_b}/items", headers=_auth(jwt_a))
        ).status_code == 404
        assert (
            await client.get(f"/eval-dataset-items/{item_b_id}", headers=_auth(jwt_a))
        ).status_code == 404
        assert (
            await client.delete(f"/eval-dataset-items/{item_b_id}", headers=_auth(jwt_a))
        ).status_code == 404

        # B still sees its own criterion + item intact (A never touched them).
        still_b = await client.get(f"/eval-criteria/{crit_b_id}", headers=_auth(jwt_b))
        assert still_b.status_code == 200, still_b.text
        assert still_b.json()["judge_instruction"] == "secret rubric"
