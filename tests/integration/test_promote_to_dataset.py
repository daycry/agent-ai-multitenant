"""Integration tests for promote-to-golden + the dataset pick/create surface (task_14_02).

``POST /tasks/{task_id}/promote-to-dataset`` promotes a real, APPROVED task into
a tenant golden dataset as a dataset item: it copies the task's input and the
approved execution's output (the reference the judge later compares against),
with provenance back to the real task/execution. ``GET/POST /eval-datasets`` is
the minimal pick/create surface the Promote UI uses. Every endpoint is
JWT-authenticated, gated on ``tenant_admin`` and runs on a tenant-scoped RLS
session.

Coverage:

  * promoting an APPROVED task creates a dataset item carrying the task input +
    the reference output, with provenance (source_task_id / source_execution_id);
  * re-promoting the SAME task into the SAME dataset is idempotent (no duplicate;
    the existing item is returned with created=false);
  * a non-approved task is REJECTED (422) — and ALLOWED when allow_unapproved=true
    (the explicit opt-in);
  * RBAC: a plain member (tenant_user) is denied (403);
  * cross-tenant (@pytest.mark.cross_tenant): tenant A cannot promote its task
    into tenant B's dataset, nor promote B's task — both 404 under RLS.

The reversible-migration check is folded in here too (up / down to 0040 / up)
so the same suite proves the eval schema round-trips.

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
# DB seed + inspection helpers (BYPASSRLS via migrations_user DSN)
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


async def _seed_project(dsn: str, *, tenant_id: UUID, name: str) -> UUID:
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, status) VALUES ($1, $2, $3, 'active')",
            project_id,
            tenant_id,
            name,
        )
    finally:
        await conn.close()
    return project_id


async def _seed_task(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    title: str,
    status: str = "done",
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, description, status, "
            " acceptance_criteria, inputs) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)",
            task_id,
            tenant_id,
            project_id,
            title,
            "the original prompt",
            status,
            '["compiles", "passes tests"]',
            '{"repo": "acme/api", "branch": "main"}',
        )
    finally:
        await conn.close()
    return task_id


async def _seed_execution(
    dsn: str,
    *,
    tenant_id: UUID,
    task_id: UUID,
    output: str,
    status: str = "done",
) -> UUID:
    execution_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO executions (id, tenant_id, task_id, status, output) "
            "VALUES ($1, $2, $3, $4, $5)",
            execution_id,
            tenant_id,
            task_id,
            status,
            output,
        )
    finally:
        await conn.close()
    return execution_id


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


async def _count_items(dsn: str, *, dataset_id: UUID, task_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT count(*) FROM eval_dataset_items "
            "WHERE dataset_id = $1 AND source_task_id = $2 AND deleted_at IS NULL",
            dataset_id,
            task_id,
        )
    finally:
        await conn.close()


async def _item_row(dsn: str, *, item_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT tenant_id, dataset_id, expected_output, source_task_id, source_execution_id "
            "FROM eval_dataset_items WHERE id = $1",
            item_id,
        )
    finally:
        await conn.close()


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


# ---------------------------------------------------------------------------
# Promoting an approved task creates a golden item (input + reference output)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_promote_approved_task_creates_item(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    task = await _seed_task(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, title="add login", status="done"
    )
    await _seed_execution(
        migrations_pg_dsn, tenant_id=tenant, task_id=task, output="the approved diff", status="done"
    )
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )

    async with _client(configured_app) as client:
        dataset_id = await _create_dataset(client, admin_jwt, name="Login golden")
        resp = await client.post(
            f"/tasks/{task}/promote-to-dataset",
            json={"dataset_id": dataset_id},
            headers=_auth(admin_jwt),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["dataset_id"] == dataset_id
    assert body["source_task_id"] == str(task)
    assert body["expected_output"] == "the approved diff"
    # The task's input (title / description / acceptance / inputs) is copied.
    assert body["input"]["title"] == "add login"
    assert body["input"]["acceptance_criteria"] == ["compiles", "passes tests"]
    assert body["input"]["inputs"] == {"repo": "acme/api", "branch": "main"}

    row = await _item_row(migrations_pg_dsn, item_id=UUID(body["id"]))
    assert row is not None
    assert row["tenant_id"] == tenant
    assert row["dataset_id"] == UUID(dataset_id)
    assert row["expected_output"] == "the approved diff"
    assert row["source_task_id"] == task


# ---------------------------------------------------------------------------
# Re-promoting the same task into the same dataset is idempotent
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_re_promote_is_idempotent(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    task = await _seed_task(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, title="add login", status="done"
    )
    await _seed_execution(
        migrations_pg_dsn, tenant_id=tenant, task_id=task, output="the approved diff", status="done"
    )
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )

    async with _client(configured_app) as client:
        dataset_id = await _create_dataset(client, admin_jwt, name="Login golden")
        first = await client.post(
            f"/tasks/{task}/promote-to-dataset",
            json={"dataset_id": dataset_id},
            headers=_auth(admin_jwt),
        )
        assert first.status_code == 201, first.text
        assert first.json()["created"] is True
        first_item_id = first.json()["id"]

        second = await client.post(
            f"/tasks/{task}/promote-to-dataset",
            json={"dataset_id": dataset_id},
            headers=_auth(admin_jwt),
        )
    assert second.status_code == 201, second.text
    assert second.json()["created"] is False
    # Same item returned, no duplicate inserted.
    assert second.json()["id"] == first_item_id
    assert await _count_items(migrations_pg_dsn, dataset_id=UUID(dataset_id), task_id=task) == 1


# ---------------------------------------------------------------------------
# A non-approved task is rejected (422) unless allow_unapproved is set
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_approved_task_rejected_unless_flag(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    task = await _seed_task(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        title="wip task",
        status="in_progress",
    )
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )

    async with _client(configured_app) as client:
        dataset_id = await _create_dataset(client, admin_jwt, name="golden")
        rejected = await client.post(
            f"/tasks/{task}/promote-to-dataset",
            json={"dataset_id": dataset_id},
            headers=_auth(admin_jwt),
        )
        assert rejected.status_code == 422, rejected.text

        allowed = await client.post(
            f"/tasks/{task}/promote-to-dataset",
            json={"dataset_id": dataset_id, "allow_unapproved": True},
            headers=_auth(admin_jwt),
        )
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["created"] is True
    # No approved execution exists, so the reference output is NULL.
    assert allowed.json()["expected_output"] is None
    assert await _count_items(migrations_pg_dsn, dataset_id=UUID(dataset_id), task_id=task) == 1


# ---------------------------------------------------------------------------
# RBAC: a plain member (tenant_user) is denied (403)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_plain_member_denied_403(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    task = await _seed_task(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, title="done task", status="done"
    )
    # Seed a dataset directly (the member cannot create one).
    dataset_id = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO eval_datasets (id, tenant_id, name, kind) VALUES ($1, $2, $3, 'golden')",
            dataset_id,
            tenant,
            "golden",
        )
    finally:
        await conn.close()

    _user_id, user_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )

    async with _client(configured_app) as client:
        promote = await client.post(
            f"/tasks/{task}/promote-to-dataset",
            json={"dataset_id": str(dataset_id)},
            headers=_auth(user_jwt),
        )
        assert promote.status_code == 403, promote.text

        listing = await client.get("/eval-datasets", headers=_auth(user_jwt))
        assert listing.status_code == 403, listing.text

    assert await _count_items(migrations_pg_dsn, dataset_id=dataset_id, task_id=task) == 0


# ---------------------------------------------------------------------------
# Cross-tenant: tenant A cannot promote into B's dataset, nor promote B's task
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_promote_is_blocked(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    project_a = await _seed_project(migrations_pg_dsn, tenant_id=tenant_a, name="proj-a")
    project_b = await _seed_project(migrations_pg_dsn, tenant_id=tenant_b, name="proj-b")
    task_a = await _seed_task(
        migrations_pg_dsn, tenant_id=tenant_a, project_id=project_a, title="a-task", status="done"
    )
    task_b = await _seed_task(
        migrations_pg_dsn, tenant_id=tenant_b, project_id=project_b, title="b-task", status="done"
    )
    _admin_a, jwt_a = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_a,
        email="admin@alpha.example.com",
        role="tenant_admin",
    )
    _admin_b, jwt_b = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_b,
        email="admin@bravo.example.com",
        role="tenant_admin",
    )

    async with _client(configured_app) as client:
        # Tenant B owns a dataset.
        dataset_b = await _create_dataset(client, jwt_b, name="b-golden")
        # Tenant A owns a dataset.
        dataset_a = await _create_dataset(client, jwt_a, name="a-golden")

        # A cannot promote its own task into B's dataset (dataset not visible -> 404).
        into_b = await client.post(
            f"/tasks/{task_a}/promote-to-dataset",
            json={"dataset_id": dataset_b},
            headers=_auth(jwt_a),
        )
        assert into_b.status_code == 404, into_b.text

        # A cannot promote B's task into A's own dataset (task not visible -> 404).
        b_task = await client.post(
            f"/tasks/{task_b}/promote-to-dataset",
            json={"dataset_id": dataset_a},
            headers=_auth(jwt_a),
        )
        assert b_task.status_code == 404, b_task.text

        # A's listing never shows B's dataset.
        listing_a = await client.get("/eval-datasets", headers=_auth(jwt_a))
        assert listing_a.status_code == 200, listing_a.text
        names = {d["name"] for d in listing_a.json()}
        assert names == {"a-golden"}

    # Nothing leaked into either dataset.
    assert await _count_items(migrations_pg_dsn, dataset_id=UUID(dataset_b), task_id=task_a) == 0
    assert await _count_items(migrations_pg_dsn, dataset_id=UUID(dataset_a), task_id=task_b) == 0


# ---------------------------------------------------------------------------
# The eval migration round-trips (up / down to 0040 / up)
# ---------------------------------------------------------------------------
def test_eval_migration_reversible(alembic_config) -> None:
    # Synchronous (NOT @pytest.mark.asyncio): alembic's env.py runs its own
    # asyncio.run, which cannot be nested inside a running event loop. Up to
    # head (creates the eval tables), down to before the eval migration, then
    # back up — proves downgrade() drops policies + tables cleanly.
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "0040_sso_email_domains")
    command.upgrade(alembic_config, "head")
