"""Integration tests for per-project incoming-webhook config CRUD (task_13_11).

``/projects/{project_id}/incoming-webhooks`` — the operator-facing CONFIG
surface for INBOUND webhooks (the inverse of Plan 10's OUTGOING signing). The
project owner / Tenant Admin creates / lists / edits / rotates / disables the
per-project configs the PUBLIC receive endpoint (task_13_08) resolves and whose
HMAC it verifies. These endpoints are JWT-authenticated, gated on
``tenant_admin`` and run on a tenant-scoped RLS session.

Coverage:

  * create mints + returns the signing secret EXACTLY ONCE, persists only its
    Fernet ciphertext (never the clear value), and the secret round-trips
    (decrypts back to what was returned) so it can verify a real signature;
  * list shows origin / name / enabled / mappings / incoming_path but NEVER the
    secret;
  * rotate mints a NEW secret (changes the ciphertext) and returns it once;
  * update (disable) flips ``enabled`` without touching the secret;
  * delete soft-deletes (drops out of the listing);
  * RBAC: a non-admin (tenant_user) is denied (403);
  * cross-tenant (@pytest.mark.cross_tenant): a tenant cannot see / rotate /
    delete another tenant's config, and a config of another project 404s.

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


async def _config_row(dsn: str, *, config_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            "SELECT tenant_id, project_id, origin, name, enabled, "
            "signing_secret_encrypted, deleted_at "
            "FROM incoming_webhook_configs WHERE id = $1",
            config_id,
        )
    finally:
        await conn.close()


async def _count_configs(dsn: str, *, project_id: UUID, live_only: bool = True) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        if live_only:
            return await conn.fetchval(
                "SELECT count(*) FROM incoming_webhook_configs "
                "WHERE project_id = $1 AND deleted_at IS NULL",
                project_id,
            )
        return await conn.fetchval(
            "SELECT count(*) FROM incoming_webhook_configs WHERE project_id = $1",
            project_id,
        )
    finally:
        await conn.close()


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE incoming_webhook_events, incoming_webhook_configs, "
            "projects, user_org_memberships, organizations, users "
            "RESTART IDENTITY CASCADE"
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


# ---------------------------------------------------------------------------
# Create returns the secret ONCE + persists only the ciphertext
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_returns_secret_once_persists_only_ciphertext(
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

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={
                "origin": "github",
                "name": "CI on acme/api",
                "action_mappings": [
                    {"event_type": "github.pull_request_review", "action": "create_task"}
                ],
            },
            headers=_auth(admin_jwt),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    secret = body["signing_secret"]
    config_id = UUID(body["id"])
    assert secret
    assert body["origin"] == "github"
    assert body["name"] == "CI on acme/api"
    assert body["enabled"] is True
    assert body["incoming_path"] == f"/webhooks/incoming/github/{config_id}"
    assert body["action_mappings"][0]["action"] == "create_task"

    # The DB stores only the Fernet ciphertext, never the clear secret.
    row = await _config_row(migrations_pg_dsn, config_id=config_id)
    assert row is not None
    assert row["tenant_id"] == tenant
    assert row["project_id"] == project
    assert secret not in row["signing_secret_encrypted"]

    # The stored ciphertext decrypts back to the returned clear secret (so it
    # can verify a real provider signature).
    from api_server.webhooks.secrets import decrypt_signing_secret

    assert decrypt_signing_secret(row["signing_secret_encrypted"]) == secret


# ---------------------------------------------------------------------------
# List never exposes the secret
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_never_exposes_secret(
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

    async with _client(configured_app) as client:
        mint = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={"origin": "sentry", "name": "Sentry prod"},
            headers=_auth(admin_jwt),
        )
        assert mint.status_code == 201, mint.text
        secret = mint.json()["signing_secret"]

        listing = await client.get(
            f"/projects/{project}/incoming-webhooks", headers=_auth(admin_jwt)
        )
        assert listing.status_code == 200, listing.text
        rows = listing.json()
        assert len(rows) == 1
        entry = rows[0]
        assert entry["name"] == "Sentry prod"
        assert entry["origin"] == "sentry"
        assert "signing_secret" not in entry
        assert "signing_secret_encrypted" not in entry
        assert secret not in listing.text


# ---------------------------------------------------------------------------
# Rotate mints a NEW secret (changes the ciphertext)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rotate_changes_secret(
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

    async with _client(configured_app) as client:
        mint = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={"origin": "github", "name": "to-rotate"},
            headers=_auth(admin_jwt),
        )
        config_id = mint.json()["id"]
        first_secret = mint.json()["signing_secret"]
        first_ct = (await _config_row(migrations_pg_dsn, config_id=UUID(config_id)))[
            "signing_secret_encrypted"
        ]

        rotate = await client.post(
            f"/projects/{project}/incoming-webhooks/{config_id}/rotate-secret",
            headers=_auth(admin_jwt),
        )
        assert rotate.status_code == 200, rotate.text
        new_secret = rotate.json()["signing_secret"]

    assert new_secret != first_secret
    new_ct = (await _config_row(migrations_pg_dsn, config_id=UUID(config_id)))[
        "signing_secret_encrypted"
    ]
    assert new_ct != first_ct
    from api_server.webhooks.secrets import decrypt_signing_secret

    assert decrypt_signing_secret(new_ct) == new_secret


# ---------------------------------------------------------------------------
# Update disables a config without touching the secret
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_disables_without_touching_secret(
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

    async with _client(configured_app) as client:
        mint = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={"origin": "github", "name": "toggle"},
            headers=_auth(admin_jwt),
        )
        config_id = mint.json()["id"]
        ct_before = (await _config_row(migrations_pg_dsn, config_id=UUID(config_id)))[
            "signing_secret_encrypted"
        ]

        upd = await client.put(
            f"/projects/{project}/incoming-webhooks/{config_id}",
            json={"enabled": False, "name": "toggle-off"},
            headers=_auth(admin_jwt),
        )
        assert upd.status_code == 200, upd.text
        assert upd.json()["enabled"] is False
        assert upd.json()["name"] == "toggle-off"

    row = await _config_row(migrations_pg_dsn, config_id=UUID(config_id))
    assert row["enabled"] is False
    # The secret ciphertext is untouched by a non-secret update.
    assert row["signing_secret_encrypted"] == ct_before


# ---------------------------------------------------------------------------
# Delete soft-deletes (drops out of listing)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_soft_deletes(
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

    async with _client(configured_app) as client:
        mint = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={"origin": "github", "name": "to-delete"},
            headers=_auth(admin_jwt),
        )
        config_id = mint.json()["id"]

        delete = await client.delete(
            f"/projects/{project}/incoming-webhooks/{config_id}", headers=_auth(admin_jwt)
        )
        assert delete.status_code == 204, delete.text

        listing = await client.get(
            f"/projects/{project}/incoming-webhooks", headers=_auth(admin_jwt)
        )
        assert listing.json() == []

        # Deleting again 404s (no live row matches).
        again = await client.delete(
            f"/projects/{project}/incoming-webhooks/{config_id}", headers=_auth(admin_jwt)
        )
        assert again.status_code == 404, again.text

    # The row stays (soft delete) for audit but is excluded from live listings.
    assert await _count_configs(migrations_pg_dsn, project_id=project, live_only=True) == 0
    assert await _count_configs(migrations_pg_dsn, project_id=project, live_only=False) == 1


# ---------------------------------------------------------------------------
# Recent deliveries lists recorded events (metadata only, never raw body)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_deliveries_lists_events_metadata_only(
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

    async with _client(configured_app) as client:
        mint = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={"origin": "github", "name": "deliveries"},
            headers=_auth(admin_jwt),
        )
        config_id = mint.json()["id"]

    # Seed a recorded event directly (the public receive path is task_13_08's
    # suite; here we just assert the read view).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO incoming_webhook_events "
            "(id, tenant_id, config_id, project_id, origin, delivery_id, event_type, "
            " raw_body, verified) "
            "VALUES ($1, $2, $3, $4, 'github', 'gh-1', 'pull_request_review', "
            " '{\"secret_in_body\": false}', true)",
            uuid4(),
            tenant,
            UUID(config_id),
            project,
        )
    finally:
        await conn.close()

    async with _client(configured_app) as client:
        resp = await client.get(
            f"/projects/{project}/incoming-webhooks/{config_id}/deliveries",
            headers=_auth(admin_jwt),
        )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    entry = rows[0]
    assert entry["origin"] == "github"
    assert entry["delivery_id"] == "gh-1"
    assert entry["event_type"] == "pull_request_review"
    assert entry["verified"] is True
    # The raw body is NOT exposed in the deliveries view (replay territory).
    assert "raw_body" not in entry
    assert "signature" not in entry


# ---------------------------------------------------------------------------
# RBAC: a non-admin (tenant_user) is denied (403)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_admin_denied_403(
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

    async with _client(configured_app) as client:
        create = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={"origin": "github", "name": "nope"},
            headers=_auth(user_jwt),
        )
        assert create.status_code == 403, create.text

        listing = await client.get(
            f"/projects/{project}/incoming-webhooks", headers=_auth(user_jwt)
        )
        assert listing.status_code == 403, listing.text

    assert await _count_configs(migrations_pg_dsn, project_id=project, live_only=False) == 0


# ---------------------------------------------------------------------------
# A comment/escalate mapping without a target_task_id is a 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_comment_mapping_without_target_is_422(
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

    async with _client(configured_app) as client:
        resp = await client.post(
            f"/projects/{project}/incoming-webhooks",
            json={
                "origin": "github",
                "name": "bad-mapping",
                "action_mappings": [
                    {"event_type": "github.pull_request_review", "action": "comment"}
                ],
            },
            headers=_auth(admin_jwt),
        )
    assert resp.status_code == 422, resp.text
    assert await _count_configs(migrations_pg_dsn, project_id=project, live_only=False) == 0


# ---------------------------------------------------------------------------
# Cross-tenant: a tenant cannot see / rotate / delete another tenant's config
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_tenant_a_cannot_touch_tenant_b_config(
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
    _admin_b, jwt_b = await _seed_user_with_jwt(
        migrations_pg_dsn,
        test_redis_url,
        tenant_id=tenant_b,
        email="admin@bravo.example.com",
        role="tenant_admin",
    )

    async with _client(configured_app) as client:
        # Tenant B creates a config in its own project.
        mint_b = await client.post(
            f"/projects/{project_b}/incoming-webhooks",
            json={"origin": "github", "name": "b-config"},
            headers=_auth(jwt_b),
        )
        assert mint_b.status_code == 201, mint_b.text
        b_config_id = mint_b.json()["id"]
        b_secret = mint_b.json()["signing_secret"]

        # Tenant A cannot list B's project's configs (project not visible -> 404).
        list_a_on_b = await client.get(
            f"/projects/{project_b}/incoming-webhooks", headers=_auth(jwt_a)
        )
        assert list_a_on_b.status_code == 404, list_a_on_b.text

        # Tenant A's own project lists nothing (B's config is invisible).
        list_a = await client.get(f"/projects/{project_a}/incoming-webhooks", headers=_auth(jwt_a))
        assert list_a.status_code == 200, list_a.text
        assert list_a.json() == []

        # Tenant A cannot rotate B's config (404, even using A's own project path).
        rotate_a = await client.post(
            f"/projects/{project_a}/incoming-webhooks/{b_config_id}/rotate-secret",
            headers=_auth(jwt_a),
        )
        assert rotate_a.status_code == 404, rotate_a.text

        # Tenant A cannot delete B's config either.
        delete_a = await client.delete(
            f"/projects/{project_a}/incoming-webhooks/{b_config_id}", headers=_auth(jwt_a)
        )
        assert delete_a.status_code == 404, delete_a.text

    # B's config is untouched and still lives only in tenant B.
    row = await _config_row(migrations_pg_dsn, config_id=UUID(b_config_id))
    assert row is not None
    assert row["tenant_id"] == tenant_b
    assert row["deleted_at"] is None
    from api_server.webhooks.secrets import decrypt_signing_secret

    assert decrypt_signing_secret(row["signing_secret_encrypted"]) == b_secret
