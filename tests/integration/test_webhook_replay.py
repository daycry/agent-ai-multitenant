"""Integration tests for incoming-webhook delivery REPLAY from audit (task_13_12).

Every received webhook is RECORDED (task_13_08) with its raw payload + the
signature we verified + the action it triggered — an audit trail. This phase
adds a project-scoped operator endpoint (RBAC tenant_admin) to LIST recent
deliveries and REPLAY a recorded one: re-run verify + parse + map + action
against the STORED payload, for debugging. A replay RE-VERIFIES the stored
signature, re-executes the mapped action, is ITSELF audited (a new
``incoming_webhook_events`` row with ``replayed_from_event_id`` set), and is
explicitly operator-initiated (its replay row has no ``delivery_id`` so it never
collides with inbound idempotency).

This suite proves, end-to-end over the config router:

  * a recorded delivery can be LISTED and REPLAYED, and the replay RE-RUNS the
    mapped action (create_task / comment) against the config's project;
  * the replay is AUDITED — a new delivery row pointing at the source via
    ``replayed_from_event_id``; replaying twice records two replay rows;
  * a replay whose stored signature no longer verifies (secret rotated) is 422;
  * RBAC: a non-owner (tenant_user) is 403;
  * cross-tenant (@pytest.mark.cross_tenant): a tenant CANNOT replay another
    tenant's delivery (404), and the replay can never touch another tenant.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are healthy;
the fixtures create a throwaway DB and flush Redis DB 15.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_SECRET = "s3cret-signing-key-acme"  # - test fixture, not a real secret

_PR_REVIEW_PAYLOAD = json.dumps(
    {
        "action": "submitted",
        "review": {
            "state": "approved",
            "body": "LGTM, ship it",
            "html_url": "https://github.com/acme/api/pull/42#pullrequestreview-1",
            "user": {"login": "reviewer-jane"},
        },
        "pull_request": {"number": 42, "title": "Add retry logic"},
        "repository": {"full_name": "acme/api"},
    }
).encode("utf-8")


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
    dsn: str, *, tenant_id: UUID, project_id: UUID, title: str = "existing task"
) -> UUID:
    task_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO tasks (id, tenant_id, project_id, title, status, priority) "
            "VALUES ($1, $2, $3, $4, 'in_progress', 'medium')",
            task_id,
            tenant_id,
            project_id,
            title,
        )
    finally:
        await conn.close()
    return task_id


async def _seed_user_with_jwt(
    dsn: str, redis_url: str, *, tenant_id: UUID, email: str, role: str
) -> tuple[UUID, str]:
    """Seed a user + active membership + LIVE Redis session, return (id, jwt)."""
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


async def _seed_config(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    origin: str = "github",
    secret: str = _SECRET,
    action_mappings: list[dict] | None = None,
) -> UUID:
    """Seed an ``incoming_webhook_configs`` row (secret Fernet-encrypted)."""
    from api_server.webhooks.secrets import encrypt_signing_secret

    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO incoming_webhook_configs "
            "(id, tenant_id, project_id, origin, name, signing_secret_encrypted, "
            " enabled, action_mappings) "
            "VALUES ($1, $2, $3, $4, $5, $6, true, $7)",
            config_id,
            tenant_id,
            project_id,
            origin,
            f"{origin}-config",
            encrypt_signing_secret(secret),
            json.dumps(action_mappings or []),
        )
    finally:
        await conn.close()
    return config_id


async def _seed_event(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    config_id: UUID,
    origin: str = "github",
    secret: str = _SECRET,
    body: bytes = _PR_REVIEW_PAYLOAD,
    event_type: str | None = "pull_request_review",
    delivery_id: str | None = "gh-recorded-1",
) -> UUID:
    """Seed a recorded, VERIFIED delivery with a valid stored signature."""
    from api_server.webhooks.signatures import compute_incoming_signature

    event_id = uuid4()
    signature = "sha256=" + compute_incoming_signature(secret, body)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO incoming_webhook_events "
            "(id, tenant_id, config_id, project_id, origin, delivery_id, event_type, "
            " signature, raw_body, verified) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true)",
            event_id,
            tenant_id,
            config_id,
            project_id,
            origin,
            delivery_id,
            event_type,
            signature,
            body.decode("utf-8"),
        )
    finally:
        await conn.close()
    return event_id


async def _count_tasks(dsn: str, *, project_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval("SELECT count(*) FROM tasks WHERE project_id = $1", project_id)
    finally:
        await conn.close()
    return int(row)


async def _count_events(dsn: str, *, config_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM incoming_webhook_events WHERE config_id = $1", config_id
        )
    finally:
        await conn.close()
    return int(row)


async def _count_replays_of(dsn: str, *, source_event_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM incoming_webhook_events WHERE replayed_from_event_id = $1",
            source_event_id,
        )
    finally:
        await conn.close()
    return int(row)


async def _audit_events(dsn: str, *, task_id: UUID, kind: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM task_audit_events WHERE task_id = $1 AND kind = $2",
            task_id,
            kind,
        )
    finally:
        await conn.close()
    return int(row)


async def _rotate_secret(dsn: str, *, config_id: UUID, new_secret: str) -> None:
    from api_server.webhooks.secrets import encrypt_signing_secret

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "UPDATE incoming_webhook_configs SET signing_secret_encrypted = $1 WHERE id = $2",
            encrypt_signing_secret(new_secret),
            config_id,
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE incoming_webhook_events, incoming_webhook_configs, "
            "task_audit_events, tasks, projects, user_org_memberships, "
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
    monkeypatch.setenv("API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY", "test-webhook-enc-key")
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


# ===========================================================================
# A recorded delivery can be listed + replayed and re-runs the mapped action
# ===========================================================================
@pytest.mark.asyncio
async def test_list_and_replay_reruns_create_task(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[
            {
                "event_type": "github.pull_request_review",
                "action": "create_task",
                "title_template": "Review: {title}",
            }
        ],
    )
    source_event = await _seed_event(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, config_id=config_id
    )

    async with _client(configured_app) as client:
        # LIST the recorded delivery.
        listing = await client.get(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries",
            headers=_auth(admin_jwt),
        )
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        assert len(rows) == 1
        assert rows[0]["id"] == str(source_event)
        assert rows[0]["replayed_from_event_id"] is None
        assert "raw_body" not in rows[0]  # never exposes the payload

        # REPLAY it — re-runs the mapped create_task action.
        replay = await client.post(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries/{source_event}/replay",
            headers=_auth(admin_jwt),
        )
        assert replay.status_code == 202, replay.text
        body = replay.json()
        assert body["source_event_id"] == str(source_event)
        assert body["action"] == "create_task"
        assert body["task_id"]
        assert _SECRET not in replay.text

    # The replay re-ran the mapped action: a task was created in the project.
    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 1

    # The replay is audited: a NEW event row pointing at the source.
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 2
    assert await _count_replays_of(migrations_pg_dsn, source_event_id=source_event) == 1

    # The replay row shows up in the deliveries listing tagged as a replay.
    async with _client(configured_app) as client:
        listing = await client.get(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries",
            headers=_auth(admin_jwt),
        )
    rows = listing.json()
    assert len(rows) == 2
    replay_rows = [r for r in rows if r["replayed_from_event_id"] == str(source_event)]
    assert len(replay_rows) == 1
    assert replay_rows[0]["delivery_id"] is None  # operator-initiated, no collision


# ===========================================================================
# Replaying twice records two replay rows (operator-initiated, no idempotency)
# ===========================================================================
@pytest.mark.asyncio
async def test_replay_comment_is_audited_and_repeatable(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    target_task = await _seed_task(migrations_pg_dsn, tenant_id=tenant, project_id=project)
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[
            {
                "event_type": "github.pull_request_review",
                "action": "comment",
                "target_task_id": str(target_task),
                "body_template": "Review {review_state} by {actor}",
            }
        ],
    )
    source_event = await _seed_event(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, config_id=config_id
    )

    url = f"/projects/{project}/incoming-webhooks/{config_id}/deliveries/{source_event}/replay"
    async with _client(configured_app) as client:
        first = await client.post(url, headers=_auth(admin_jwt))
        second = await client.post(url, headers=_auth(admin_jwt))
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["action"] == "comment"
    assert first.json()["task_id"] == str(target_task)

    # Each replay re-ran the comment action AND recorded its own audit row.
    assert await _audit_events(migrations_pg_dsn, task_id=target_task, kind="comment") == 2
    assert await _count_replays_of(migrations_pg_dsn, source_event_id=source_event) == 2
    # No new task was created by a comment replay (only the seeded target task).
    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 1


# ===========================================================================
# A replay whose stored signature no longer verifies (secret rotated) is 422
# ===========================================================================
@pytest.mark.asyncio
async def test_replay_after_secret_rotation_is_422(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    _admin_id, admin_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="admin@acme.example.com",
        role="tenant_admin",
    )
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[{"event_type": "github.pull_request_review", "action": "create_task"}],
    )
    source_event = await _seed_event(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, config_id=config_id
    )
    # Rotate the config's secret AFTER recording the event: the stored signature
    # no longer verifies under the new secret.
    await _rotate_secret(migrations_pg_dsn, config_id=config_id, new_secret="rotated-secret-xyz")

    async with _client(configured_app) as client:
        replay = await client.post(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries/{source_event}/replay",
            headers=_auth(admin_jwt),
        )
    assert replay.status_code == 422, replay.text
    # No action ran, no replay row was recorded (the txn rolled back).
    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 0
    assert await _count_replays_of(migrations_pg_dsn, source_event_id=source_event) == 0


# ===========================================================================
# RBAC: a non-owner (tenant_user) is 403
# ===========================================================================
@pytest.mark.asyncio
async def test_non_owner_replay_denied_403(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    _user_id, user_jwt = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant,
        email="member@acme.example.com",
        role="tenant_user",
    )
    config_id = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant,
        project_id=project,
        origin="github",
        action_mappings=[{"event_type": "github.pull_request_review", "action": "create_task"}],
    )
    source_event = await _seed_event(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, config_id=config_id
    )

    async with _client(configured_app) as client:
        replay = await client.post(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries/{source_event}/replay",
            headers=_auth(user_jwt),
        )
        listing = await client.get(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries",
            headers=_auth(user_jwt),
        )
    assert replay.status_code == 403, replay.text
    assert listing.status_code == 403, listing.text
    # The non-owner's replay attempt did nothing.
    assert await _count_tasks(migrations_pg_dsn, project_id=project) == 0
    assert await _count_replays_of(migrations_pg_dsn, source_event_id=source_event) == 0


# ===========================================================================
# Cross-tenant: a tenant CANNOT replay another tenant's delivery (404)
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cannot_replay_another_tenants_delivery(
    configured_app, migrations_pg_dsn: str, test_redis_url: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="alpha")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="bravo")
    project_a = await _seed_project(migrations_pg_dsn, tenant_id=tenant_a, name="proj-a")
    project_b = await _seed_project(migrations_pg_dsn, tenant_id=tenant_b, name="proj-b")
    _admin_a, jwt_a = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_a,
        email="admin@alpha.example.com",
        role="tenant_admin",
    )

    # Tenant B has a config + a recorded delivery in ITS project.
    config_b = await _seed_config(
        migrations_pg_dsn,
        tenant_id=tenant_b,
        project_id=project_b,
        origin="github",
        action_mappings=[{"event_type": "github.pull_request_review", "action": "create_task"}],
    )
    event_b = await _seed_event(
        migrations_pg_dsn, tenant_id=tenant_b, project_id=project_b, config_id=config_b
    )

    async with _client(configured_app) as client:
        # Tenant A tries to replay B's delivery via B's project path -> 404
        # (B's project is invisible to A's RLS scope).
        via_b_path = await client.post(
            f"/projects/{project_b}/incoming-webhooks/{config_b}/deliveries/{event_b}/replay",
            headers=_auth(jwt_a),
        )
        assert via_b_path.status_code == 404, via_b_path.text

        # Tenant A tries via its OWN project path with B's ids -> 404
        # (the config is invisible under A's tenant RLS scope).
        via_a_path = await client.post(
            f"/projects/{project_a}/incoming-webhooks/{config_b}/deliveries/{event_b}/replay",
            headers=_auth(jwt_a),
        )
        assert via_a_path.status_code == 404, via_a_path.text

    # B's delivery was never replayed; no task created in either project.
    assert await _count_tasks(migrations_pg_dsn, project_id=project_a) == 0
    assert await _count_tasks(migrations_pg_dsn, project_id=project_b) == 0
    assert await _count_replays_of(migrations_pg_dsn, source_event_id=event_b) == 0
