"""Integration tests for the backup-destination config + connectivity API
(Plan 12 Phase B — task_12_09).

A System Admin manages the list of remote destinations (S3, B2, SFTP/NAS,
rclone) the backup bundle is uploaded to, and probes each one's connectivity,
from the admin panel. This suite exercises the backend half through the real
FastAPI app + the real test Postgres so the RBAC / RLS boundary is the one under
test:

  * GET  /admin/backup/destinations          — System-Admin only (empty list
    when unset). prod-09 task_prod09_01 closed the earlier
    ``require_tenant_member`` read: the config carries no credential, but it
    names the buckets/hosts every tenant's data is copied to.
  * PUT  /admin/backup/destinations           — System-Admin-only (a Tenant
    Admin is 403); validates EVERY item (unknown type / missing required field /
    any secret-looking field -> 422, nothing persisted) and PERSISTS the list.
  * POST /admin/backup/destinations/{name}/test — System-Admin-only; DELEGATES
    the probe to the worker (prod-15 ``task_gov_app_boundary_11``) and relays its
    ok/detail. Hasta 2026-08-19 el adaptador se construía en el proceso del
    api-server, que es donde NO están las ``WORKERS_BACKUP_*``; el productor está
    MOCKEADO aquí, así que no hay broker ni red.

The headline guarantee under test is that CREDENTIALS are NEVER stored nor
echoed: a payload that tries to smuggle a secret into ``config`` is a clean 422,
and the read-back of a stored destination contains only non-secret config.

No real network, no real credentials anywhere.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

# A distinctive secret value so a leak assertion can grep for it. It stands in
# for an S3 access key a malicious/confused client might try to store; it MUST
# NOT survive into platform_settings nor any response.
_SECRET_MARKER = "AKIA-MUST-NOT-BE-STORED-0123456789"


# ---------------------------------------------------------------------------
# App under test (RLS-bound app_user engine), mirrors test_backup_schedule.
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


# ---------------------------------------------------------------------------
# Seeding: one tenant, a Tenant Admin in it + a platform System Admin.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    ids = {"tenant_a": uuid4(), "admin_a": uuid4(), "sysadmin": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE platform_settings, audit_log,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant_a"],
            "Tenant A",
            "tenant-a-dest",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, 'h', false), ($3, $4, 'h', true)",
            ids["admin_a"],
            "admin-a@dest.test",
            ids["sysadmin"],
            "sys@dest.test",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["admin_a"],
        )
    finally:
        await conn.close()
    return ids


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


async def _setting(dsn: str, key: str) -> Any:
    import json as _json

    conn = await asyncpg.connect(dsn)
    try:
        await conn.set_type_codec(
            "jsonb", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog"
        )
        return await conn.fetchval("SELECT value FROM platform_settings WHERE key = $1", key)
    finally:
        await conn.close()


_S3_DEST = {
    "type": "s3",
    "name": "offsite-s3",
    "enabled": True,
    "config": {"bucket": "backups", "prefix": "nightly/", "endpoint_url": "https://minio:9000"},
}
_SFTP_DEST = {
    "type": "sftp",
    "name": "nas",
    "enabled": False,
    "config": {"host": "nas.local", "username": "backup", "remote_path": "/backups"},
}


# ===========================================================================
# GET — empty when unset; System-Admin only.
# ===========================================================================
@pytest.mark.asyncio
async def test_get_destinations_empty_when_unset(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    async with _client(configured_app) as client:
        resp = await client.get(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"destinations": []}


@pytest.mark.asyncio
async def test_get_destinations_is_not_readable_by_a_tenant_admin(
    configured_app, migrations_pg_dsn: str
) -> None:
    """prod-09 task_prod09_01 (authz-1): the destination list holds no
    credential, but it DOES name the buckets/hosts every tenant's data is copied
    to — a map of the off-site copies. It used to be readable by any tenant
    member; the whole ``/admin/backup`` surface is System-Admin only now."""
    seeded = await _seed(migrations_pg_dsn)
    tenant_admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.get(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {tenant_admin_token}"},
        )
    assert resp.status_code == 403, resp.text


# ===========================================================================
# PUT — System-Admin-gated + persists, secret never stored/echoed.
# ===========================================================================
@pytest.mark.asyncio
async def test_set_destinations_requires_system_admin_and_persists(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    payload = {"destinations": [_S3_DEST, _SFTP_DEST]}

    async with _client(configured_app) as client:
        # A Tenant Admin cannot set destinations.
        forbidden = await client.put(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert forbidden.status_code == 403

        # The System Admin can — and it persists (normalised).
        ok = await client.put(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {sys_token}"},
            json=payload,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        names = {d["name"] for d in body["destinations"]}
        assert names == {"offsite-s3", "nas"}

        # Read-back through the API reflects the persisted (non-secret) config.
        read = await client.get(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert read.status_code == 200, read.text
    read_body = read.json()
    s3 = next(d for d in read_body["destinations"] if d["name"] == "offsite-s3")
    assert s3["config"]["bucket"] == "backups"
    assert s3["enabled"] is True

    # The source-of-truth row holds only the non-secret config (no credential).
    stored = await _setting(migrations_pg_dsn, "backup_destinations")
    assert _SECRET_MARKER not in str(stored)


@pytest.mark.asyncio
async def test_set_destinations_rejects_smuggled_secret(
    configured_app, migrations_pg_dsn: str
) -> None:
    """A client tries to store an S3 access key inside `config`. The server
    rejects any field outside the type's non-secret allow-list — a credential
    can NEVER reach platform_settings (nor be echoed back)."""
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    payload = {
        "destinations": [
            {
                "type": "s3",
                "name": "leaky",
                "enabled": True,
                "config": {
                    "bucket": "backups",
                    "backup_s3_access_key_id": _SECRET_MARKER,
                },
            }
        ]
    }
    async with _client(configured_app) as client:
        resp = await client.put(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {sys_token}"},
            json=payload,
        )
    assert resp.status_code == 422, resp.text
    assert _SECRET_MARKER not in resp.text
    # Nothing persisted by the rejected write.
    assert await _setting(migrations_pg_dsn, "backup_destinations") is None


@pytest.mark.asyncio
async def test_set_destinations_rejects_unknown_type_and_missing_field(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    async with _client(configured_app) as client:
        # Unknown type.
        bad_type = await client.put(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"destinations": [{"type": "ftp", "name": "x", "config": {}}]},
        )
        assert bad_type.status_code == 422, bad_type.text

        # Known type, missing the required bucket.
        missing = await client.put(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"destinations": [{"type": "s3", "name": "x", "config": {}}]},
        )
        assert missing.status_code == 422, missing.text
    assert await _setting(migrations_pg_dsn, "backup_destinations") is None


# ===========================================================================
# POST /test — System-Admin-gated; builds the adapter + probes (MOCKED).
# ===========================================================================
@pytest.mark.asyncio
async def test_connectivity_probe_is_system_admin_gated_and_mocked(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connectivity probe hands the stored NON-secret config to the WORKER
    and relays its verdict — MOCKED so no broker and no real S3 endpoint are
    reached. A Tenant Admin is 403; a System Admin gets ok/detail.

    Desde prod-15 ``task_gov_app_boundary_11`` el api-server ya no construye el
    adaptador: lo hace ``workers.backup_test_destination``, que corre donde están
    las credenciales. Lo que este test sigue fijando —y ahora dice algo más
    fuerte— es QUÉ viaja: la config no secreta del destino y nada más."""
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    # Seed one S3 destination through the real PUT.
    async with _client(configured_app) as client:
        seed_resp = await client.put(
            "/admin/backup/destinations",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"destinations": [_S3_DEST]},
        )
        assert seed_resp.status_code == 200, seed_resp.text

        # MOCK the PRODUCER so the probe never reaches a broker (and therefore
        # never a real S3 endpoint): it returns the canned verdict a worker would
        # send back, and we capture the payload to assert it carries the right
        # NON-secret config and nothing else.
        from api_server import celery_client

        captured: dict[str, Any] = {}

        async def _fake_probe(config: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
            captured["config"] = config
            return {"ok": True, "detail": "bucket 'backups' reachable"}

        monkeypatch.setattr(celery_client, "probe_backup_destination_and_wait", _fake_probe)

        # A Tenant Admin cannot probe.
        forbidden = await client.post(
            "/admin/backup/destinations/offsite-s3/test",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert forbidden.status_code == 403

        # The System Admin gets the probe result.
        ok = await client.post(
            "/admin/backup/destinations/offsite-s3/test",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["ok"] is True
        assert "reachable" in body["detail"]

        # A missing destination is a clean 404.
        missing = await client.post(
            "/admin/backup/destinations/does-not-exist/test",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
        assert missing.status_code == 404, missing.text

    # Lo que viajó al worker es el type/name del destino + su config NO SECRETA,
    # nunca una credencial: las resuelve el worker desde su propio entorno.
    assert captured["config"]["type"] == "s3"
    assert captured["config"]["name"] == "offsite-s3"
    assert captured["config"]["bucket"] == "backups"
    assert all("key" not in k.lower() and "secret" not in k.lower() for k in captured["config"])


# ===========================================================================
# Registry factory — build_destination maps type -> adapter (workers unit).
# ===========================================================================
def test_build_destination_maps_each_type() -> None:
    from workers.backup_destinations import (
        B2Destination,
        DestinationError,
        RcloneDestination,
        S3Destination,
        SftpDestination,
        build_destination,
    )
    from workers.secrets import StaticSecretsProvider

    secrets = StaticSecretsProvider(values={})

    s3 = build_destination({"type": "s3", "name": "s", "bucket": "b"}, secrets=secrets)
    assert isinstance(s3, S3Destination)
    assert s3.config.bucket == "b"

    b2 = build_destination(
        {"type": "b2", "name": "x", "bucket": "b", "region": "us-west-002"}, secrets=secrets
    )
    assert isinstance(b2, B2Destination)
    assert "us-west-002" in (b2.config.endpoint_url or "")

    sftp = build_destination(
        {"type": "sftp", "name": "n", "host": "h", "username": "u"}, secrets=secrets
    )
    assert isinstance(sftp, SftpDestination)
    assert sftp.config.host == "h"

    rclone = build_destination({"type": "rclone", "name": "r", "remote": "gd"}, secrets=secrets)
    assert isinstance(rclone, RcloneDestination)
    assert rclone.config.remote == "gd"

    # Unknown type + missing required field both raise a typed error.
    with pytest.raises(DestinationError):
        build_destination({"type": "ftp", "name": "x"}, secrets=secrets)
    with pytest.raises(DestinationError):
        build_destination({"type": "s3", "name": "x"}, secrets=secrets)
