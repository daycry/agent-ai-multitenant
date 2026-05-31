"""Integration tests for the configurable backup schedule (Plan 12 task_12_04).

The backup schedule (cron cadence + a live enable flag + the local retention
window) is a PLATFORM setting a System Admin configures from the admin panel —
never a hardcoded cron. Two surfaces are exercised:

  * **API** (``/admin/backup/schedule``) through the real FastAPI app + the
    real test Postgres so the RBAC / RLS boundary is the one under test:
      - GET is readable by any authenticated member (defaults when unset).
      - PUT is System-Admin-only (a Tenant Admin is 403).
      - PUT validates the cron + retention window (a bad value is a 422, no
        write) and PERSISTS the three settings (read-back proves it).

  * **Beat task** (``workers.run_daily_backup``) reads the CONFIG live: with a
    MOCKED backup engine (no real pg_dump / tar / disk), the test proves the
    task reads ``backup_enabled`` (a disabled run is a no-op) and applies the
    configured ``retention_days`` to the engine — not the env default.

No real backup of the live stack happens anywhere here.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# App under test (RLS-bound app_user engine), mirrors test_notification_config.
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
    ids = {
        "tenant_a": uuid4(),
        "admin_a": uuid4(),
        "sysadmin": uuid4(),
    }
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
            "tenant-a-bk",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_admin) VALUES"
            " ($1, $2, 'h', false), ($3, $4, 'h', true)",
            ids["admin_a"],
            "admin-a@bk.test",
            ids["sysadmin"],
            "sys@bk.test",
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
    """Read a platform_settings JSONB value, decoded to a Python object.

    asyncpg returns a JSONB column as the raw JSON TEXT unless a codec is set;
    register the json codec so we get ``False`` / ``14`` rather than ``'false'``
    / ``'14'``."""
    import json as _json

    conn = await asyncpg.connect(dsn)
    try:
        await conn.set_type_codec(
            "jsonb", encoder=_json.dumps, decoder=_json.loads, schema="pg_catalog"
        )
        return await conn.fetchval("SELECT value FROM platform_settings WHERE key = $1", key)
    finally:
        await conn.close()


# ===========================================================================
# GET — defaults when unset, readable by any member.
# ===========================================================================
@pytest.mark.asyncio
async def test_get_schedule_returns_platform_defaults_when_unset(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    async with _client(configured_app) as client:
        resp = await client.get(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Platform defaults: enabled, daily 03:00, 7-day retention.
    assert body["enabled"] is True
    assert body["cron"] == "0 3 * * *"
    assert body["retention_days"] == 7


# ===========================================================================
# PUT — System-Admin-gated + persists.
# ===========================================================================
@pytest.mark.asyncio
async def test_set_schedule_requires_system_admin(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    admin_token = await _mint_token(seeded["admin_a"], seeded["tenant_a"])
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    payload = {"enabled": False, "cron": "30 2 * * *", "retention_days": 14}

    async with _client(configured_app) as client:
        # A Tenant Admin cannot set the schedule.
        forbidden = await client.put(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {admin_token}"},
            json=payload,
        )
        assert forbidden.status_code == 403

        # The System Admin can — and it persists.
        ok = await client.put(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {sys_token}"},
            json=payload,
        )
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body == {"enabled": False, "cron": "30 2 * * *", "retention_days": 14}

        # Read-back through the API reflects the persisted values.
        read = await client.get(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {sys_token}"},
        )
    assert read.status_code == 200, read.text
    assert read.json() == {"enabled": False, "cron": "30 2 * * *", "retention_days": 14}

    # And the three platform_settings rows exist (the source of truth).
    assert await _setting(migrations_pg_dsn, "backup_enabled") is False
    assert await _setting(migrations_pg_dsn, "backup_cron") == "30 2 * * *"
    assert await _setting(migrations_pg_dsn, "backup_retention_days") == 14


# ===========================================================================
# PUT — validation: bad cron / out-of-range retention is a 422, no write.
# ===========================================================================
@pytest.mark.asyncio
async def test_set_schedule_rejects_bad_cron(configured_app, migrations_pg_dsn: str) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    async with _client(configured_app) as client:
        # Not 5 fields.
        resp = await client.put(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"enabled": True, "cron": "0 3 * *", "retention_days": 7},
        )
        assert resp.status_code == 422, resp.text

        # 5 fields but a field Celery's crontab rejects.
        resp2 = await client.put(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"enabled": True, "cron": "99 3 * * *", "retention_days": 7},
        )
        assert resp2.status_code == 422, resp2.text

    # Nothing was persisted by the rejected writes.
    assert await _setting(migrations_pg_dsn, "backup_cron") is None


@pytest.mark.asyncio
async def test_set_schedule_rejects_out_of_range_retention(
    configured_app, migrations_pg_dsn: str
) -> None:
    seeded = await _seed(migrations_pg_dsn)
    sys_token = await _mint_token(seeded["sysadmin"], None, is_system_admin=True)
    async with _client(configured_app) as client:
        # 0 days would prune the bundle just written — Pydantic ge=1 -> 422.
        resp = await client.put(
            "/admin/backup/schedule",
            headers={"Authorization": f"Bearer {sys_token}"},
            json={"enabled": True, "cron": "0 3 * * *", "retention_days": 0},
        )
    assert resp.status_code == 422, resp.text
    assert await _setting(migrations_pg_dsn, "backup_retention_days") is None


# ===========================================================================
# platform_settings helpers — cron + retention validation (unit-ish).
# ===========================================================================
def test_validate_backup_cron_normalises_and_rejects() -> None:
    from api_server.db.platform_settings import (
        InvalidBackupScheduleError,
        validate_backup_cron,
    )

    # Collapses extra whitespace, returns the 5 fields.
    assert validate_backup_cron("0   3 * * *") == "0 3 * * *"
    assert validate_backup_cron("30 */6 * * *") == "30 */6 * * *"

    for bad in ("0 3 * *", "", "0 3 * * * *", "99 3 * * *"):
        with pytest.raises(InvalidBackupScheduleError):
            validate_backup_cron(bad)


def test_validate_backup_retention_days_bounds() -> None:
    from api_server.db.platform_settings import (
        InvalidBackupScheduleError,
        validate_backup_retention_days,
    )

    assert validate_backup_retention_days(1) == 1
    assert validate_backup_retention_days(7) == 7
    for bad in (0, -1, 3651):
        with pytest.raises(InvalidBackupScheduleError):
            validate_backup_retention_days(bad)


# ===========================================================================
# Beat task reads the live config — MOCKED engine (no real backup).
# ===========================================================================
@pytest.fixture()
def migrated_db(alembic_config, migrations_pg_dsn: str):
    command.upgrade(alembic_config, "head")

    async def _truncate() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE platform_settings RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_truncate())
    return migrations_pg_dsn


async def _set_schedule_row(dsn: str, key: str, value_jsonb: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES ($1, $2::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key,
            value_jsonb,
        )
    finally:
        await conn.close()


def _worker_settings(admin_database_url: str):
    """A workers Settings pointed at the (BYPASSRLS) test DB.

    ``backup_retention_days`` here is the ENV default (90) so we can prove the
    beat task overrides it with the panel-configured value."""
    from workers.config import Settings

    return Settings(
        database_url=admin_database_url,
        backup_retention_days=90,
        backup_cron="0 3 * * *",
    )


@pytest.mark.asyncio
async def test_beat_task_skips_when_disabled(
    migrated_db: str, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A System Admin turned the backup OFF — the run is a no-op, the engine
    is never invoked (proving the task reads the live enable flag)."""
    import workers.backup_task as bt

    await _set_schedule_row(migrated_db, "backup_enabled", "false")

    def _explode(**_kwargs: Any) -> Any:
        raise AssertionError("disabled run must not invoke the backup engine")

    monkeypatch.setattr(bt, "run_full_backup", _explode)

    settings = _worker_settings(admin_database_url)
    result = await bt._run_daily_backup(settings)
    assert result == {"enabled": False, "skipped": True}


@pytest.mark.asyncio
async def test_beat_task_applies_configured_retention(
    migrated_db: str, admin_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configured retention_days (panel) overrides the env default and is
    handed to the (mocked) engine — proving the task reads the live config."""
    import workers.backup_task as bt
    from workers.backup import BackupResult

    # Panel config: enabled, every-6-hours cron, 21-day retention.
    await _set_schedule_row(migrated_db, "backup_enabled", "true")
    await _set_schedule_row(migrated_db, "backup_cron", '"30 */6 * * *"')
    await _set_schedule_row(migrated_db, "backup_retention_days", "21")

    captured: dict[str, Any] = {}

    def _fake_engine(**kwargs: Any) -> BackupResult:
        settings = kwargs["settings"]
        captured["retention_days"] = settings.backup_retention_days
        return BackupResult(
            backup_id="20260530T031500Z",
            bundle_dir=Path("/tmp/bundle"),
            manifest_path=Path("/tmp/bundle/manifest.json"),
            artifacts=(),
            pruned=(),
        )

    monkeypatch.setattr(bt, "run_full_backup", _fake_engine)
    # Skip the post-backup verification (it would touch a non-existent bundle).
    monkeypatch.setattr(bt, "_verify_after_backup", lambda *_a, **_k: True)

    settings = _worker_settings(admin_database_url)  # env default 90
    result = await bt._run_daily_backup(settings)

    assert result["enabled"] is True
    assert result["ok"] is True
    # The engine got the PANEL value (21), NOT the env default (90).
    assert captured["retention_days"] == 21
    assert result["retention_days"] == 21
    assert result["cron"] == "30 */6 * * *"


# ===========================================================================
# Beat schedule wiring — the entry exists + reads its cadence from config.
# ===========================================================================
def test_backup_beat_entry_reads_cron_from_config() -> None:
    import workers.backup_task  # noqa: F401  (registers workers.run_daily_backup)
    from celery.schedules import crontab
    from workers.beat_schedule import BACKUP_BEAT_ENTRY, build_beat_schedule
    from workers.celery_app import build_celery_app
    from workers.config import Settings

    app = build_celery_app(
        Settings(broker_url="redis://localhost:6379/1", result_backend="redis://localhost:6379/2")
    )
    assert "workers.run_daily_backup" in app.tasks

    # Default cadence: daily 03:00 — NOT a hardcoded magic schedule, it comes
    # from Settings.backup_cron.
    default_sched = build_beat_schedule(Settings())
    assert BACKUP_BEAT_ENTRY in default_sched
    entry = default_sched[BACKUP_BEAT_ENTRY]
    assert entry["task"] == "workers.run_daily_backup"
    assert isinstance(entry["schedule"], crontab)
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {0}

    # A different configured cron is honoured.
    custom = build_beat_schedule(Settings(backup_cron="15 */4 * * *"))
    custom_cron = custom[BACKUP_BEAT_ENTRY]["schedule"]
    assert isinstance(custom_cron, crontab)
    assert custom_cron.minute == {15}
    assert custom_cron.hour == {0, 4, 8, 12, 16, 20}
