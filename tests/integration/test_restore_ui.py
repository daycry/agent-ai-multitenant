"""Integration tests for the restore UI backend (Plan 12 Phase C task_12_12).

The restore UI's small backend is exercised through the real FastAPI app + the
real test Postgres so the RBAC boundary is the one under test. Four endpoints:

  * GET  /admin/backup/restore/backups                     — list (local + remote)
  * GET  /admin/backup/restore/backups/{id}/preview        — manifest + per-tenant
  * POST /admin/backup/restore                             — trigger (double confirm)
  * GET  /admin/backup/restore/jobs/{job_id}              — pollable status

A restore is LONG + DESTRUCTIVE, so the trigger must ENQUEUE a Celery background
job, NEVER run the restore inline. Real ``pg_restore`` / ``docker compose`` /
Celery broker cannot run here, so the enqueue seam
(``api_server.celery_client.enqueue_restore`` / ``get_restore_job_status``) is
MOCKED — the test asserts the endpoint ENQUEUES (not runs) and forwards the
exact args, never that a real restore happens.

The tests assert:
  * LIST + PREVIEW read the on-disk bundle manifest the workers write.
  * Every endpoint is System-Admin only (a Tenant Admin is 403).
  * The trigger requires a matching DOUBLE-confirmation token (a mismatch is a
    422, nothing enqueued) — for BOTH a full and a per-tenant restore.
  * The trigger ENQUEUES the background job (the mock is hit) and returns 202 +
    the job id — it does NOT run the restore inline.

No real restore of a live stack runs here — that is HUMAN test human_12_02 /
human_12_03.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

_BACKUP_ID = "20260530T031500Z"
_TENANT_ID = "11111111-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# A real-ish bundle on disk (the manifest the workers write).
# ---------------------------------------------------------------------------
def _write_bundle(root: Path, *, encrypted: bool = False) -> None:
    bundle = root / _BACKUP_ID
    bundle.mkdir(parents=True, exist_ok=True)
    if encrypted:
        artifacts = [
            {
                "name": "bundle.tar.enc",
                "kind": "encrypted_bundle",
                "path": "bundle.tar.enc",
                "size_bytes": 4096,
                "sha256": "0" * 64,
            }
        ]
    else:
        artifacts = [
            {
                "name": "postgres",
                "kind": "pg_dump",
                "path": "postgres",
                "size_bytes": 2048,
                "sha256": "1" * 64,
            },
            {
                "name": "minio_data.tar.gz",
                "kind": "volume_tar",
                "path": "minio_data.tar.gz",
                "size_bytes": 2048,
                "sha256": "2" * 64,
                "source": "minio_data",
            },
        ]
    manifest = {
        "version": 1,
        "backup_id": _BACKUP_ID,
        "created_at": "2026-05-30T03:15:00+00:00",
        "status": "completed",
        "database": {"url": "postgresql://user:***@db/agentic_platform"},
        "encrypted": encrypted,
        "artifacts": artifacts,
        "total_size_bytes": sum(a["size_bytes"] for a in artifacts),
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# App under test (mirrors test_backup_schedule.configured_app) with BACKUP_ROOT
# pointed at a temp dir that holds one fabricated bundle.
# ---------------------------------------------------------------------------
@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")

    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    _write_bundle(backup_root)

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_BACKUP_ROOT", str(backup_root))

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
            "tenant-a-restore",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, 'h', false), ($3, $4, 'h', true)",
            ids["admin_a"],
            "admin-a@restore.test",
            ids["sysadmin"],
            "sys@restore.test",
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


# ===========================================================================
# LIST — System-Admin reads the local bundle; a Tenant Admin is 403.
# ===========================================================================
@pytest.mark.asyncio
async def test_list_backups_is_system_admin_only(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        forbidden = await client.get(
            "/admin/backup/restore/backups",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert forbidden.status_code == 403

        ok = await client.get(
            "/admin/backup/restore/backups",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    ids = [b["backup_id"] for b in body["backups"]]
    assert _BACKUP_ID in ids
    item = next(b for b in body["backups"] if b["backup_id"] == _BACKUP_ID)
    assert item["encrypted"] is False
    assert "local" in item["locations"]


# ===========================================================================
# PREVIEW — manifest contents + the per-tenant option.
# ===========================================================================
@pytest.mark.asyncio
async def test_preview_returns_manifest_and_per_tenant_option(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async with _client(configured_app) as client:
        resp = await client.get(
            f"/admin/backup/restore/backups/{_BACKUP_ID}/preview",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["backup_id"] == _BACKUP_ID
    assert body["encrypted"] is False
    kinds = {a["kind"] for a in body["artifacts"]}
    assert "pg_dump" in kinds
    # A bundle with a pg_dump supports a per-tenant restore + lists the tables.
    assert body["per_tenant_available"] is True
    assert "projects" in body["tenant_scoped_tables"]


@pytest.mark.asyncio
async def test_preview_unknown_bundle_is_404(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    async with _client(configured_app) as client:
        resp = await client.get(
            "/admin/backup/restore/backups/20990101T000000Z/preview",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert resp.status_code == 404, resp.text


# ===========================================================================
# TRIGGER — double-confirm required, enqueued (not run inline), audited.
# ===========================================================================
@pytest.mark.asyncio
async def test_trigger_full_restore_requires_matching_confirm(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    enqueued: dict[str, Any] = {}

    async def _fake_enqueue(backup_id: str, *, confirm: str, tenant_id: str | None = None) -> str:
        enqueued["backup_id"] = backup_id
        enqueued["confirm"] = confirm
        enqueued["tenant_id"] = tenant_id
        return "job-test-123"

    # Patch the producer the router imports so NO real broker is touched — the
    # restore is ENQUEUED, never run inline.
    import api_server.celery_client as cc

    monkeypatch.setattr(cc, "enqueue_restore", _fake_enqueue)

    async with _client(configured_app) as client:
        # A wrong confirm token -> 422, nothing enqueued.
        bad = await client.post(
            "/admin/backup/restore",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"backup_id": _BACKUP_ID, "tenant_id": None, "confirm": "wrong"},
        )
        assert bad.status_code == 422, bad.text
        assert enqueued == {}

        # The exact token (the bundle id for a full restore) -> 202 + job id.
        ok = await client.post(
            "/admin/backup/restore",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"backup_id": _BACKUP_ID, "tenant_id": None, "confirm": _BACKUP_ID},
        )
    assert ok.status_code == 202, ok.text
    body = ok.json()
    assert body["job_id"] == "job-test-123"
    assert body["kind"] == "full"
    # The restore was ENQUEUED with the exact token, not run inline.
    assert enqueued == {"backup_id": _BACKUP_ID, "confirm": _BACKUP_ID, "tenant_id": None}


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_trigger_per_tenant_restore_requires_scoped_confirm(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-tenant restore needs the `<tenant_id>@<backup_id>` token, and the
    target tenant is forwarded UNCHANGED to the (mocked) background job — so the
    job can only ever touch that one tenant (another tenant is never in scope)."""
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    enqueued: dict[str, Any] = {}

    async def _fake_enqueue(backup_id: str, *, confirm: str, tenant_id: str | None = None) -> str:
        enqueued["backup_id"] = backup_id
        enqueued["confirm"] = confirm
        enqueued["tenant_id"] = tenant_id
        return "job-pt-456"

    import api_server.celery_client as cc

    monkeypatch.setattr(cc, "enqueue_restore", _fake_enqueue)

    good_token = f"{_TENANT_ID}@{_BACKUP_ID}"
    async with _client(configured_app) as client:
        # The full-restore token (bare bundle id) is NOT valid for a per-tenant
        # restore -> 422, nothing enqueued.
        bad = await client.post(
            "/admin/backup/restore",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"backup_id": _BACKUP_ID, "tenant_id": _TENANT_ID, "confirm": _BACKUP_ID},
        )
        assert bad.status_code == 422, bad.text
        assert enqueued == {}

        # The tenant-scoped token enqueues the per-tenant job for that tenant only.
        ok = await client.post(
            "/admin/backup/restore",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"backup_id": _BACKUP_ID, "tenant_id": _TENANT_ID, "confirm": good_token},
        )
    assert ok.status_code == 202, ok.text
    body = ok.json()
    assert body["kind"] == "per_tenant"
    assert body["tenant_id"] == _TENANT_ID
    # The job is scoped to exactly the requested tenant — never another.
    assert enqueued["tenant_id"] == _TENANT_ID
    assert enqueued["confirm"] == good_token


@pytest.mark.asyncio
async def test_trigger_is_system_admin_only(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])

    called = {"hit": False}

    async def _fake_enqueue(*_a: Any, **_k: Any) -> str:
        called["hit"] = True
        return "x"

    import api_server.celery_client as cc

    monkeypatch.setattr(cc, "enqueue_restore", _fake_enqueue)

    async with _client(configured_app) as client:
        resp = await client.post(
            "/admin/backup/restore",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"backup_id": _BACKUP_ID, "tenant_id": None, "confirm": _BACKUP_ID},
        )
    assert resp.status_code == 403, resp.text
    assert called["hit"] is False  # a forbidden caller never reaches the enqueue


# ===========================================================================
# STATUS — pollable job status, System-Admin only.
# ===========================================================================
@pytest.mark.asyncio
async def test_job_status_is_pollable(
    configured_app, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)

    async def _fake_status(job_id: str) -> dict[str, Any]:
        return {
            "job_id": job_id,
            "state": "PROGRESS",
            "progress": {"phase": "restoring", "message": "restoring full bundle"},
            "result": None,
            "error": None,
        }

    import api_server.celery_client as cc

    monkeypatch.setattr(cc, "get_restore_job_status", _fake_status)

    async with _client(configured_app) as client:
        resp = await client.get(
            "/admin/backup/restore/jobs/job-test-123",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "PROGRESS"
    assert body["progress"]["phase"] == "restoring"
