"""Integration tests for the incoming-webhook endpoint + HMAC (task_13_08).

``POST /webhooks/incoming/{origin}/{config_id}`` is the PUBLIC inbound surface
(the inverse of Plan 10's OUTGOING signing): an external tool POSTs an event,
stamps an HMAC-SHA256 signature header over the raw body with a shared secret,
and we accept the event ONLY on a constant-time match against the per-PROJECT
secret. This suite proves:

  * a correctly-signed payload verifies, is accepted (202) and is RECORDED;
  * a tampered body / wrong secret / missing signature -> 401, NO event;
  * an unknown (or disabled / wrong-origin) config -> 404;
  * an oversize body -> 413 (the DDoS body cap, before any work);
  * a redelivery (same delivery id) is idempotent (one stored event);
  * cross-tenant (@pytest.mark.cross_tenant): a config's secret only validates
    for its OWN tenant/project — tenant-B's secret never authenticates an event
    aimed at tenant-A's config;
  * the signing secret is never echoed in any response.

Pre-condition: postgres (15432) + redis (6379) from docker-compose are
healthy; the ``configured_app`` fixture migrates a throwaway DB and flushes
Redis DB 15.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_GITHUB_SIG_HEADER = "X-Hub-Signature-256"
_SECRET = "s3cret-signing-key-acme"  # - test fixture, not a real secret


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


async def _seed_config(
    dsn: str,
    *,
    tenant_id: UUID,
    project_id: UUID,
    origin: str = "github",
    secret: str = _SECRET,
    enabled: bool = True,
) -> UUID:
    """Seed an ``incoming_webhook_configs`` row with the secret Fernet-encrypted.

    Encryption goes through the SAME helper the config-write path uses, so the
    app process decrypts it with the same key (both default to the dev key,
    which the ``configured_app`` fixture does not override).
    """
    from api_server.webhooks.secrets import encrypt_signing_secret

    config_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO incoming_webhook_configs "
            "(id, tenant_id, project_id, origin, name, signing_secret_encrypted, enabled) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            config_id,
            tenant_id,
            project_id,
            origin,
            f"{origin}-config",
            encrypt_signing_secret(secret),
            enabled,
        )
    finally:
        await conn.close()
    return config_id


async def _count_events(dsn: str, *, config_id: UUID) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "SELECT count(*) FROM incoming_webhook_events WHERE config_id = $1", config_id
        )
    finally:
        await conn.close()
    return int(row)


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE incoming_webhook_events, incoming_webhook_configs, "
            "projects, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


def _github_signature(secret: str, body: bytes) -> str:
    import hashlib
    import hmac

    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


# ===========================================================================
# Correctly-signed payload verifies + is accepted + recorded
# ===========================================================================
@pytest.mark.asyncio
async def test_valid_signature_accepted_and_recorded(
    configured_app, migrations_pg_dsn: str
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(migrations_pg_dsn, tenant_id=tenant, project_id=project)

    body = b'{"action":"opened","number":7}'
    headers = {
        _GITHUB_SIG_HEADER: _github_signature(_SECRET, body),
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-abc",
        "Content-Type": "application/json",
    }
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "accepted"
    # The secret is NEVER echoed.
    assert _SECRET not in resp.text
    # Exactly one event recorded, raw body + verified flag persisted.
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 1
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT raw_body, verified, event_type, delivery_id, tenant_id, project_id "
            "FROM incoming_webhook_events WHERE config_id = $1",
            config_id,
        )
    finally:
        await conn.close()
    assert row["raw_body"] == body.decode()
    assert row["verified"] is True
    assert row["event_type"] == "pull_request"
    assert row["delivery_id"] == "delivery-abc"
    assert row["tenant_id"] == tenant
    assert row["project_id"] == project


# ===========================================================================
# Tampered body / wrong secret / missing signature -> 401, no event
# ===========================================================================
@pytest.mark.asyncio
async def test_tampered_body_rejected_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(migrations_pg_dsn, tenant_id=tenant, project_id=project)

    signed_body = b'{"action":"opened"}'
    tampered_body = b'{"action":"closed"}'
    headers = {_GITHUB_SIG_HEADER: _github_signature(_SECRET, signed_body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=tampered_body, headers=headers
        )
    assert resp.status_code == 401, resp.text
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 0


@pytest.mark.asyncio
async def test_wrong_secret_rejected_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(migrations_pg_dsn, tenant_id=tenant, project_id=project)

    body = b'{"action":"opened"}'
    headers = {_GITHUB_SIG_HEADER: _github_signature("the-wrong-secret", body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 401, resp.text
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 0


@pytest.mark.asyncio
async def test_missing_signature_rejected_401(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(migrations_pg_dsn, tenant_id=tenant, project_id=project)

    body = b'{"action":"opened"}'
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(f"/webhooks/incoming/github/{config_id}", content=body)
    assert resp.status_code == 401, resp.text
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 0


# ===========================================================================
# Unknown config -> 404 (and disabled / wrong-origin -> 404 too)
# ===========================================================================
@pytest.mark.asyncio
async def test_unknown_config_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    body = b"{}"
    headers = {_GITHUB_SIG_HEADER: _github_signature(_SECRET, body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{uuid4()}", content=body, headers=headers
        )
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_disabled_config_404(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, enabled=False
    )
    body = b"{}"
    headers = {_GITHUB_SIG_HEADER: _github_signature(_SECRET, body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 404, resp.text
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 0


@pytest.mark.asyncio
async def test_origin_mismatch_404(configured_app, migrations_pg_dsn: str) -> None:
    """A github config addressed via the /gitlab/ path is a 404 (no scheme leak)."""
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(
        migrations_pg_dsn, tenant_id=tenant, project_id=project, origin="github"
    )
    body = b"{}"
    headers = {_GITHUB_SIG_HEADER: _github_signature(_SECRET, body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/gitlab/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 404, resp.text


# ===========================================================================
# Oversize body -> 413 (DDoS body cap, before any work)
# ===========================================================================
@pytest.mark.asyncio
async def test_oversize_body_rejected_413(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(migrations_pg_dsn, tenant_id=tenant, project_id=project)

    # Shrink the cap so the test body is "oversize" without sending megabytes.
    from api_server.config import get_settings

    cap = 64
    monkeypatch.setattr(get_settings(), "incoming_webhook_max_body_bytes", cap)

    body = b"x" * (cap + 1)
    headers = {_GITHUB_SIG_HEADER: _github_signature(_SECRET, body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert resp.status_code == 413, resp.text
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 0


# ===========================================================================
# Redelivery (same delivery id) is idempotent — one stored event
# ===========================================================================
@pytest.mark.asyncio
async def test_redelivery_is_idempotent(configured_app, migrations_pg_dsn: str) -> None:
    await _truncate_all(migrations_pg_dsn)
    tenant = await _seed_tenant(migrations_pg_dsn, slug="acme")
    project = await _seed_project(migrations_pg_dsn, tenant_id=tenant, name="proj-a")
    config_id = await _seed_config(migrations_pg_dsn, tenant_id=tenant, project_id=project)

    body = b'{"action":"opened","number":9}'
    headers = {
        _GITHUB_SIG_HEADER: _github_signature(_SECRET, body),
        "X-GitHub-Delivery": "dup-1",
    }
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        first = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
        second = await client.post(
            f"/webhooks/incoming/github/{config_id}", content=body, headers=headers
        )
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert second.json()["status"] == "duplicate"
    assert first.json()["event_id"] == second.json()["event_id"]
    assert await _count_events(migrations_pg_dsn, config_id=config_id) == 1


# ===========================================================================
# Cross-tenant: a config's secret only validates for its own tenant/project
# ===========================================================================
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cross_tenant_secret_does_not_validate(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Tenant-B's secret must never authenticate an event aimed at tenant-A's config.

    Two tenants, each with its own config + DISTINCT secret. A payload signed
    with tenant-B's secret but POSTed to tenant-A's config id is rejected (401)
    and records nothing — the secret is scoped to its own tenant/project.
    """
    await _truncate_all(migrations_pg_dsn)
    tenant_a = await _seed_tenant(migrations_pg_dsn, slug="acme")
    tenant_b = await _seed_tenant(migrations_pg_dsn, slug="globex")
    project_a = await _seed_project(migrations_pg_dsn, tenant_id=tenant_a, name="proj-a")
    project_b = await _seed_project(migrations_pg_dsn, tenant_id=tenant_b, name="proj-b")
    config_a = await _seed_config(
        migrations_pg_dsn, tenant_id=tenant_a, project_id=project_a, secret="secret-A"
    )
    await _seed_config(
        migrations_pg_dsn, tenant_id=tenant_b, project_id=project_b, secret="secret-B"
    )

    body = b'{"x":1}'
    # Sign with tenant-B's secret, aim at tenant-A's config.
    headers = {_GITHUB_SIG_HEADER: _github_signature("secret-B", body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        resp = await client.post(
            f"/webhooks/incoming/github/{config_a}", content=body, headers=headers
        )
    assert resp.status_code == 401, resp.text
    assert await _count_events(migrations_pg_dsn, config_id=config_a) == 0

    # The CORRECT secret for tenant-A's own config still works (and only records
    # for tenant A).
    headers_a = {_GITHUB_SIG_HEADER: _github_signature("secret-A", body)}
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://testserver"
    ) as client:
        ok = await client.post(
            f"/webhooks/incoming/github/{config_a}", content=body, headers=headers_a
        )
    assert ok.status_code == 202, ok.text
    assert await _count_events(migrations_pg_dsn, config_id=config_a) == 1
